"""
Central Inventory Server - Nuansa Wangi (FastAPI)

Mendukung dua backend database, dipilih lewat DB_BACKEND:
    postgres -> Supabase / PostgreSQL
    mysql    -> MySQL 8.0+ / Laragon

Endpoint:
    GET  /health            cek server + koneksi database (publik)
    GET  /auth/me           info identitas API key yang dipakai
    GET  /items             lihat stok semua barang   (?sku=... untuk filter)
    GET  /items/{sku}       lihat stok satu barang
    POST /items/update      tambah / kurangi stok (row-level locking + atomic transaction)
    GET  /logs              riwayat transaksi (?branch_id= &sku= &status= &limit=)
    GET  /branches          daftar cabang (admin)
    GET  /summary           ringkasan stok & transaksi (admin)
    GET  /metrics           metrik Prometheus
    GET  /dashboard         dashboard web sederhana

Autentikasi: header  X-API-Key: <kunci cabang / kunci admin>
Dokumentasi interaktif (Swagger): http://localhost:8000/docs
"""
import hashlib
import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, field_validator

from . import db
from .auth import AUTH_REQUIRED, AuthContext, require_admin, require_auth
from .limiter import LIMITER
from .metrics import METRICS

APP_VERSION = "1.1.0"
BASE_DIR = Path(__file__).resolve().parent.parent  # folder server/
STATIC_DIR = BASE_DIR / "static"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("inventory.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Membuka connection pool (%s) ...", db.BACKEND)
    db.open_pool()  # gagal jelas kalau konfigurasi/kredensial/jaringan salah
    logger.info("Server siap (backend: %s, auth: %s).", db.BACKEND, "on" if AUTH_REQUIRED else "off")
    yield
    db.close_pool()


app = FastAPI(
    title="Nuansa Wangi - Central Inventory Server",
    description="Sistem Inventaris Multi-Cabang berbasis Client-Server dengan Pessimistic Locking.",
    version=APP_VERSION,
    lifespan=lifespan,
)


# ----------------------------------------------------------------------
# Skema request
# ----------------------------------------------------------------------
class StockUpdate(BaseModel):
    branch_id: str = Field(min_length=1, max_length=50, examples=["branch-a"])
    sku: str = Field(min_length=1, max_length=64, examples=["BIBIT-BACCARAT"])
    action: Literal["ADD", "SUBTRACT"]
    quantity: StrictInt = Field(gt=0, le=1_000_000, description="Jumlah bulat > 0 (ml untuk bibit, pcs untuk botol)")
    request_id: Optional[str] = Field(
        default=None, min_length=8, max_length=100,
        description="Idempotency key. Kirim ulang request_id yang sama = tidak diproses dua kali.",
    )
    note: Optional[str] = Field(default=None, max_length=255)

    @field_validator("action", mode="before")
    @classmethod
    def _normalize_action(cls, v):
        return v.strip().upper() if isinstance(v, str) else v


def _err(status: int, code: str, message: str, **extra) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


# ----------------------------------------------------------------------
# Error handler global untuk masalah koneksi / lock database.
# Exception domain ini sama untuk PostgreSQL maupun MySQL.
# ----------------------------------------------------------------------
@app.exception_handler(db.DatabaseUnavailable)
async def db_unavailable_handler(request: Request, exc: Exception):
    logger.error("Database tidak tersedia: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "DB_UNAVAILABLE",
                            "message": "Database sedang tidak dapat dijangkau, coba lagi nanti."}},
    )


@app.exception_handler(db.LockTimeout)
async def lock_timeout_handler(request: Request, exc: Exception):
    logger.warning("Lock timeout: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "LOCK_TIMEOUT",
                            "message": "Baris stok sedang dikunci transaksi lain terlalu lama, coba lagi."}},
    )


# ----------------------------------------------------------------------
# Observability: rate limit, metrik, header keamanan
# ----------------------------------------------------------------------
RATE_LIMIT_PREFIXES = ("/items", "/logs", "/branches", "/summary", "/auth")


def _metric_path(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", None) or request.url.path


def _finish(response, request: Request, started: float) -> None:
    path = _metric_path(request)
    METRICS.inc(
        "nw_http_requests_total",
        method=request.method, path=path, status=str(response.status_code),
    )
    METRICS.observe(
        "nw_http_request_duration_seconds",
        time.perf_counter() - started, path=path,
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")


@app.middleware("http")
async def observability_middleware(request: Request, call_next):
    started = time.perf_counter()

    # Bucket rate limit: pakai hash API key kalau ada, kalau tidak IP client.
    raw_key = request.headers.get("x-api-key", "").strip()
    bucket = (
        hashlib.sha256(raw_key.encode("utf-8")).hexdigest()[:24]
        if raw_key else (request.client.host if request.client else "unknown")
    )

    rate_headers: dict[str, str] = {}
    if request.url.path.startswith(RATE_LIMIT_PREFIXES):
        allowed, remaining, retry_after = LIMITER.check(bucket)
        if not allowed:
            METRICS.inc("nw_rate_limited_total")
            response = JSONResponse(
                status_code=429,
                content={"detail": {
                    "code": "RATE_LIMITED",
                    "message": f"Terlalu banyak request. Coba lagi dalam {retry_after} detik.",
                }},
            )
            response.headers["Retry-After"] = str(retry_after)
            response.headers["X-RateLimit-Remaining"] = "0"
            _finish(response, request, started)
            return response
        rate_headers = {
            "X-RateLimit-Limit": str(int(LIMITER.describe["burst"])),
            "X-RateLimit-Remaining": str(remaining),
        }

    try:
        response = await call_next(request)
    except Exception:
        METRICS.inc(
            "nw_http_requests_total",
            method=request.method, path=_metric_path(request), status="500",
        )
        METRICS.observe(
            "nw_http_request_duration_seconds",
            time.perf_counter() - started, path=_metric_path(request),
        )
        raise

    for name, value in rate_headers.items():
        response.headers.setdefault(name, value)
    _finish(response, request, started)
    return response


# ----------------------------------------------------------------------
# Halaman dashboard (statis, tanpa build step)
# ----------------------------------------------------------------------
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/dashboard")


@app.get("/dashboard", include_in_schema=False)
def dashboard_page():
    return FileResponse(STATIC_DIR / "dashboard.html")


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------
@app.get("/health")
def health():
    db.ping()
    return {
        "status": "ok",
        "database": "connected",
        "backend": db.BACKEND,
        "version": APP_VERSION,
        "uptime_seconds": round(METRICS.uptime, 1),
    }


@app.get("/auth/me")
def auth_me(ctx: AuthContext = Depends(require_auth)):
    """Dipakai dashboard untuk tahu apakah kunci valid dan apakah dia admin."""
    return {
        "branch_id": ctx.branch_id,
        "is_admin": ctx.is_admin,
        "auth_required": AUTH_REQUIRED,
    }


@app.get("/items")
def get_items(
    sku: Optional[str] = Query(default=None, description="Filter berdasarkan SKU"),
    _ctx: AuthContext = Depends(require_auth),
):
    """Lihat stok terpusat (semua cabang membaca sumber data yang sama)."""
    items = db.list_items(sku)
    return {"count": len(items), "items": items}


@app.get("/items/{sku}")
def get_one_item(sku: str, _ctx: AuthContext = Depends(require_auth)):
    item = db.get_item(sku)
    if item is None:
        raise _err(404, "ITEM_NOT_FOUND", f"Barang dengan SKU '{sku}' tidak ditemukan.")
    return item


@app.post("/items/update")
def update_stock(payload: StockUpdate, ctx: AuthContext = Depends(require_auth)):
    """Tambah/kurangi stok. Dijalankan dalam satu transaksi dengan row lock (SELECT ... FOR UPDATE)."""
    if not ctx.is_admin and payload.branch_id != ctx.branch_id:
        raise _err(
            403, "BRANCH_MISMATCH",
            f"API key ini milik cabang '{ctx.branch_id}', tidak boleh mengubah '{payload.branch_id}'.",
        )

    request_id = payload.request_id or f"srv-{uuid.uuid4().hex}"
    try:
        result = db.apply_stock_update(
            branch_id=payload.branch_id,
            sku=payload.sku,
            action=payload.action,
            quantity=payload.quantity,
            request_id=request_id,
            note=payload.note,
        )
        METRICS.inc(
            "nw_stock_updates_total",
            action=payload.action, status="SUCCESS",
            replay=str(result["idempotent_replay"]).lower(),
        )
        return result
    except db.BranchNotFound as e:
        raise _err(403, "BRANCH_NOT_REGISTERED", f"Cabang '{e.branch_id}' tidak terdaftar.")
    except db.ItemNotFound as e:
        raise _err(404, "ITEM_NOT_FOUND", f"Barang dengan SKU '{e.sku}' tidak ditemukan.")
    except db.InsufficientStock as e:
        METRICS.inc("nw_stock_updates_total", action=payload.action, status="REJECTED")
        raise _err(409, "INSUFFICIENT_STOCK",
                   f"Stok {e.sku} tidak mencukupi: tersedia {e.available} {e.unit}, diminta {e.requested} {e.unit}.",
                   available=e.available, requested=e.requested, unit=e.unit)
    except db.RequestIdMismatch as e:
        raise _err(409, "REQUEST_ID_MISMATCH",
                   f"request_id '{e.request_id}' sudah dipakai untuk transaksi lain.")
    except db.RequestIdConflict:
        raise _err(409, "REQUEST_ID_CONFLICT", "request_id yang sama sedang diproses bersamaan.")
    except db.ConstraintViolation:
        # Pengaman lapis kedua dari database (CHECK qty >= 0)
        raise _err(409, "CONSTRAINT_VIOLATION", "Transaksi melanggar aturan integritas stok (qty >= 0).")


@app.get("/logs")
def get_logs(
    branch_id: Optional[str] = Query(default=None),
    sku: Optional[str] = Query(default=None),
    status: Optional[Literal["SUCCESS", "REJECTED"]] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    ctx: AuthContext = Depends(require_auth),
):
    """Riwayat transaksi (terbaru di atas). Kunci cabang otomatis dibatasi ke cabangnya."""
    if ctx.is_admin:
        effective_branch = branch_id
    else:
        if branch_id and branch_id != ctx.branch_id:
            raise _err(403, "BRANCH_MISMATCH",
                       f"API key ini hanya boleh melihat riwayat cabang '{ctx.branch_id}'.")
        effective_branch = ctx.branch_id

    logs = db.list_logs(branch_id=effective_branch, sku=sku, status=status, limit=limit)
    return {"count": len(logs), "logs": logs}


@app.get("/branches")
def get_branches(_ctx: AuthContext = Depends(require_admin)):
    branches = db.list_branches()
    return {"count": len(branches), "branches": branches}


@app.get("/summary")
def get_summary(_ctx: AuthContext = Depends(require_admin)):
    return db.summary()


@app.get("/metrics", include_in_schema=False)
def metrics():
    """Format Prometheus. Hanya metrik agregat (tidak membocorkan stok per SKU)."""
    try:
        s = db.summary()
        METRICS.set_gauge("nw_items_count", s["item_count"])
        METRICS.set_gauge("nw_stock_total_qty", s["total_qty"])
        METRICS.set_gauge("nw_transactions_success_total", s["success"])
        METRICS.set_gauge("nw_transactions_rejected_total", s["rejected"])
    except Exception:  # metrik tidak boleh membuat endpoint ikut gagal
        logger.exception("Gagal memperbarui metrik dari database")
    return PlainTextResponse(
        METRICS.render(db.BACKEND),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )

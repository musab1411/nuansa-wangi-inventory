"""
Central Inventory Server - Nuansa Wangi (FastAPI + Supabase/PostgreSQL)

Endpoint:
    GET  /health            cek server + koneksi database
    GET  /items             lihat stok semua barang   (?sku=... untuk filter)
    GET  /items/{sku}       lihat stok satu barang
    POST /items/update      tambah / kurangi stok (row-level locking + atomic transaction)
    GET  /logs              riwayat transaksi tiap cabang (?branch_id= &sku= &status= &limit=)

Dokumentasi interaktif (Swagger): http://localhost:8000/docs
"""
import logging
import uuid
from contextlib import asynccontextmanager
from typing import Literal, Optional

import psycopg
import psycopg_pool
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt, field_validator

from . import db

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
logger = logging.getLogger("inventory.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not db.DATABASE_URL:
        raise RuntimeError("DATABASE_URL belum diisi. Cek file .env kamu.")
    logger.info("Membuka connection pool ke Supabase ...")
    db.pool.open(wait=True, timeout=30)  # gagal jelas kalau kredensial/jaringan salah
    logger.info("Server siap.")
    yield
    db.pool.close()


app = FastAPI(
    title="Nuansa Wangi - Central Inventory Server",
    description="Sistem Inventaris Multi-Cabang berbasis Client-Server dengan Pessimistic Locking.",
    version="1.0.0",
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
# Error handler global untuk masalah koneksi database
# ----------------------------------------------------------------------
@app.exception_handler(psycopg.OperationalError)
@app.exception_handler(psycopg_pool.PoolTimeout)
async def db_unavailable_handler(request: Request, exc: Exception):
    logger.error("Database tidak tersedia: %s", exc)
    return JSONResponse(
        status_code=503,
        content={"detail": {"code": "DB_UNAVAILABLE",
                            "message": "Database sedang tidak dapat dijangkau, coba lagi nanti."}},
    )


# ----------------------------------------------------------------------
# Endpoint
# ----------------------------------------------------------------------
@app.get("/health")
def health():
    db.ping()
    return {"status": "ok", "database": "connected"}


@app.get("/items")
def get_items(sku: Optional[str] = Query(default=None, description="Filter berdasarkan SKU")):
    """Lihat stok terpusat (semua cabang membaca sumber data yang sama)."""
    items = db.list_items(sku)
    return {"count": len(items), "items": items}


@app.get("/items/{sku}")
def get_one_item(sku: str):
    item = db.get_item(sku)
    if item is None:
        raise _err(404, "ITEM_NOT_FOUND", f"Barang dengan SKU '{sku}' tidak ditemukan.")
    return item


@app.post("/items/update")
def update_stock(payload: StockUpdate):
    """Tambah/kurangi stok. Dijalankan dalam satu transaksi dengan row lock (SELECT ... FOR UPDATE)."""
    request_id = payload.request_id or f"srv-{uuid.uuid4().hex}"
    try:
        return db.apply_stock_update(
            branch_id=payload.branch_id,
            sku=payload.sku,
            action=payload.action,
            quantity=payload.quantity,
            request_id=request_id,
            note=payload.note,
        )
    except db.BranchNotFound as e:
        raise _err(403, "BRANCH_NOT_REGISTERED", f"Cabang '{e.branch_id}' tidak terdaftar.")
    except db.ItemNotFound as e:
        raise _err(404, "ITEM_NOT_FOUND", f"Barang dengan SKU '{e.sku}' tidak ditemukan.")
    except db.InsufficientStock as e:
        raise _err(409, "INSUFFICIENT_STOCK",
                   f"Stok {e.sku} tidak mencukupi: tersedia {e.available} {e.unit}, diminta {e.requested} {e.unit}.",
                   available=e.available, requested=e.requested, unit=e.unit)
    except db.RequestIdMismatch as e:
        raise _err(409, "REQUEST_ID_MISMATCH",
                   f"request_id '{e.request_id}' sudah dipakai untuk transaksi lain.")
    except psycopg.errors.LockNotAvailable:
        raise _err(503, "LOCK_TIMEOUT", "Baris stok sedang dikunci transaksi lain terlalu lama, coba lagi.")
    except psycopg.errors.UniqueViolation:
        raise _err(409, "REQUEST_ID_CONFLICT", "request_id yang sama sedang diproses bersamaan.")
    except psycopg.errors.CheckViolation:
        # Pengaman lapis kedua dari database (CHECK qty >= 0)
        raise _err(409, "CONSTRAINT_VIOLATION", "Transaksi melanggar aturan integritas stok (qty >= 0).")


@app.get("/logs")
def get_logs(
    branch_id: Optional[str] = Query(default=None),
    sku: Optional[str] = Query(default=None),
    status: Optional[Literal["SUCCESS", "REJECTED"]] = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
):
    """Riwayat transaksi (terbaru di atas), bisa difilter per cabang / barang / status."""
    logs = db.list_logs(branch_id=branch_id, sku=sku, status=status, limit=limit)
    return {"count": len(logs), "logs": logs}

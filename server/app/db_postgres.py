"""
Backend database PostgreSQL / Supabase.

Inti Pessimistic Locking ada di apply_stock_update():
    BEGIN;
    SELECT ... FROM inventory_items WHERE sku = :sku FOR UPDATE;   -- kunci baris
    (cek stok -> UPDATE stok -> INSERT log)
    COMMIT;  (atau ROLLBACK kalau stok tidak cukup / ada error)

Transaksi lain yang menyentuh baris yang sama akan MENUNGGU di baris FOR UPDATE
sampai transaksi pertama selesai, lalu membaca stok TERBARU (READ COMMITTED).

Error driver PostgreSQL diterjemahkan ke exception domain di errors.py supaya
main.py tidak bergantung pada psycopg.
"""
import functools
import logging
import os
from datetime import datetime, timezone

import psycopg
import psycopg_pool
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .errors import (
    BranchNotFound,
    ConstraintViolation,
    DatabaseUnavailable,
    InsufficientStock,
    ItemNotFound,
    LockTimeout,
    RequestIdConflict,
    RequestIdMismatch,
)

logger = logging.getLogger("inventory.db.postgres")

BACKEND = "postgres"
DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "10"))
LOCK_TIMEOUT_MS = int(os.getenv("LOCK_TIMEOUT_MS", "5000"))

# prepare_threshold=None -> matikan prepared statement, aman untuk Supabase
# pooler (Supavisor) mode transaction (port 6543) maupun session (port 5432).
pool = ConnectionPool(
    conninfo=DATABASE_URL,
    min_size=DB_POOL_MIN,
    max_size=DB_POOL_MAX,
    kwargs={"row_factory": dict_row, "prepare_threshold": None},
    check=ConnectionPool.check_connection,  # buang koneksi basi (mis. diputus pooler Supabase saat idle)
    max_idle=300,
    timeout=DB_POOL_TIMEOUT,  # maks. menunggu koneksi bebas dari pool
    open=False,
)


# ----------------------------------------------------------------------
# Siklus hidup pool + terjemahan error driver
# ----------------------------------------------------------------------
def is_configured() -> bool:
    return bool(DATABASE_URL)


def open_pool():
    if not is_configured():
        raise RuntimeError("DATABASE_URL belum diisi. Cek file .env kamu.")
    pool.open(wait=True, timeout=30)


def close_pool():
    pool.close()


def _guard(fn):
    """Terjemahkan error psycopg/psycopg_pool menjadi exception domain."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except psycopg.errors.LockNotAvailable as exc:
            raise LockTimeout(str(exc)) from exc
        except psycopg.errors.UniqueViolation as exc:
            raise RequestIdConflict(str(exc)) from exc
        except psycopg.errors.CheckViolation as exc:
            raise ConstraintViolation(str(exc)) from exc
        except psycopg.errors.ForeignKeyViolation as exc:
            raise ConstraintViolation(str(exc)) from exc
        except psycopg_pool.PoolTimeout as exc:
            raise DatabaseUnavailable(str(exc)) from exc
        except psycopg.OperationalError as exc:
            raise DatabaseUnavailable(str(exc)) from exc

    return wrapper


# ----------------------------------------------------------------------
# Query baca
# ----------------------------------------------------------------------
@_guard
def ping() -> bool:
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return True


@_guard
def list_items(sku: str | None = None) -> list[dict]:
    sql = "SELECT sku, name, category, unit, qty, updated_at FROM inventory_items"
    params: list = []
    if sku:
        sql += " WHERE sku = %s"
        params.append(sku)
    sql += " ORDER BY category, sku"
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


def get_item(sku: str) -> dict | None:
    rows = list_items(sku)
    return rows[0] if rows else None


@_guard
def list_logs(branch_id=None, sku=None, status=None, limit: int = 50) -> list[dict]:
    conditions: list[str] = []
    params: list = []
    if branch_id:
        conditions.append("t.branch_id = %s")
        params.append(branch_id)
    if sku:
        conditions.append("t.sku = %s")
        params.append(sku)
    if status:
        conditions.append("t.status = %s")
        params.append(status)
    where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
    params.append(limit)
    sql = f"""
        SELECT t.id, t.request_id, t.branch_id, b.name AS branch_name, t.sku,
               t.action, t.delta, t.qty_before, t.qty_after, t.status,
               t.note, t.created_at
        FROM stock_transactions t
        JOIN branches b ON b.id = t.branch_id
        {where}
        ORDER BY t.id DESC
        LIMIT %s
    """
    with pool.connection() as conn:
        return conn.execute(sql, params).fetchall()


# ----------------------------------------------------------------------
# Auth per cabang
# ----------------------------------------------------------------------
@_guard
def branch_by_api_key(key_hash: str) -> str | None:
    """Cari cabang berdasarkan SHA-256 API key. None = kunci tidak valid/nonaktif."""
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT id FROM branches WHERE api_key_hash = %s", (key_hash,)
        ).fetchone()
    return row["id"] if row else None


@_guard
def list_branches() -> list[dict]:
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT id, name, (api_key_hash IS NOT NULL) AS has_key "
            "FROM branches ORDER BY id"
        ).fetchall()
    return [{"id": r["id"], "name": r["name"], "has_key": bool(r["has_key"])} for r in rows]


@_guard
def set_branch_api_key(branch_id: str, key_hash: str | None) -> bool:
    """Set/rotate/hapus API key cabang. key_hash=None berarti cabang dinonaktifkan."""
    with pool.connection() as conn:
        exists = conn.execute("SELECT 1 FROM branches WHERE id = %s", (branch_id,)).fetchone()
        if exists is None:
            raise BranchNotFound(branch_id)
        conn.execute(
            "UPDATE branches SET api_key_hash = %s WHERE id = %s", (key_hash, branch_id)
        )
    return True


# ----------------------------------------------------------------------
# Ringkasan untuk dashboard / monitoring
# ----------------------------------------------------------------------
@_guard
def summary() -> dict:
    with pool.connection() as conn:
        items = conn.execute(
            "SELECT COUNT(*) AS item_count, COALESCE(SUM(qty), 0) AS total_qty FROM inventory_items"
        ).fetchone()
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM stock_transactions GROUP BY status"
        ).fetchall()
        last = conn.execute(
            "SELECT MAX(created_at) AS last_tx FROM stock_transactions"
        ).fetchone()["last_tx"]
    counts = {r["status"]: r["n"] for r in rows}
    return {
        "item_count": int(items["item_count"]),
        "total_qty": int(items["total_qty"]),
        "success": int(counts.get("SUCCESS", 0)),
        "rejected": int(counts.get("REJECTED", 0)),
        "last_tx": last,
    }


# ----------------------------------------------------------------------
# Backup / restore logis (portabel antar backend)
# ----------------------------------------------------------------------
@_guard
def export_snapshot() -> dict:
    """Ambil seluruh isi database sebagai dict siap disimpan ke JSON."""
    with pool.connection() as conn:
        branches = conn.execute(
            "SELECT id, name, api_key_hash FROM branches ORDER BY id"
        ).fetchall()
        items = conn.execute(
            "SELECT sku, name, category, unit, qty, updated_at "
            "FROM inventory_items ORDER BY sku"
        ).fetchall()
        tx = conn.execute(
            "SELECT id, request_id, branch_id, sku, action, delta, qty_before, "
            "qty_after, status, note, created_at "
            "FROM stock_transactions ORDER BY id"
        ).fetchall()
    return {
        "backend": BACKEND,
        "exported_at": datetime.now(timezone.utc),
        "branches": branches,
        "inventory_items": items,
        "stock_transactions": tx,
    }


@_guard
def import_snapshot(snapshot: dict) -> dict:
    """Ganti seluruh isi database dengan snapshot. Dipakai scripts/restore.py."""
    branches = snapshot.get("branches", [])
    items = snapshot.get("inventory_items", [])
    tx = snapshot.get("stock_transactions", [])

    with pool.connection() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute("DELETE FROM stock_transactions")
                cur.execute("DELETE FROM inventory_items")
                cur.execute("DELETE FROM branches")

                if branches:
                    cur.executemany(
                        "INSERT INTO branches (id, name, api_key_hash) VALUES (%s, %s, %s)",
                        [(b["id"], b["name"], b.get("api_key_hash")) for b in branches],
                    )
                if items:
                    cur.executemany(
                        "INSERT INTO inventory_items (sku, name, category, unit, qty, updated_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s)",
                        [(i["sku"], i["name"], i["category"], i["unit"], int(i["qty"]),
                          i.get("updated_at")) for i in items],
                    )
                if tx:
                    cur.executemany(
                        "INSERT INTO stock_transactions "
                        "(id, request_id, branch_id, sku, action, delta, qty_before, "
                        " qty_after, status, note, created_at) "
                        "OVERRIDING SYSTEM VALUE "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [(int(t["id"]), t["request_id"], t["branch_id"], t["sku"],
                          t["action"], int(t["delta"]), int(t["qty_before"]),
                          int(t["qty_after"]), t["status"], t.get("note"),
                          t.get("created_at")) for t in tx],
                    )

                # Samakan sequence identity dengan id terbesar yang baru dimasukkan.
                cur.execute(
                    "SELECT setval(pg_get_serial_sequence('stock_transactions', 'id'), "
                    "COALESCE((SELECT MAX(id) FROM stock_transactions), 1), "
                    "(SELECT COUNT(*) > 0 FROM stock_transactions))"
                )

    return {
        "branches": len(branches),
        "inventory_items": len(items),
        "stock_transactions": len(tx),
    }


# ----------------------------------------------------------------------
# Mutasi stok (inti sistem)
# ----------------------------------------------------------------------
def _tx_to_result(row: dict, unit: str, replay: bool) -> dict:
    return {
        "status": "SUCCESS",
        "transaction_id": row["id"],
        "request_id": row["request_id"],
        "branch_id": row["branch_id"],
        "sku": row["sku"],
        "action": row["action"],
        "quantity": abs(row["delta"]),
        "unit": unit,
        "qty_before": row["qty_before"],
        "qty_after": row["qty_after"],
        "idempotent_replay": replay,
        "created_at": row["created_at"],
    }


@_guard
def apply_stock_update(*, branch_id: str, sku: str, action: str, quantity: int,
                       request_id: str, note: str | None) -> dict:
    delta = quantity if action == "ADD" else -quantity

    try:
        with pool.connection() as conn:
            with conn.transaction():  # BEGIN ... COMMIT (ROLLBACK jika ada exception)
                # Jangan menunggu lock selamanya: gagal cepat -> HTTP 503
                conn.execute(
                    "SELECT set_config('lock_timeout', %s, true)", (str(LOCK_TIMEOUT_MS),)
                )

                # 1) Validasi cabang terdaftar
                if conn.execute("SELECT 1 FROM branches WHERE id = %s", (branch_id,)).fetchone() is None:
                    raise BranchNotFound(branch_id)

                # 2) PESSIMISTIC LOCK: kunci baris stok sampai transaksi selesai
                item = conn.execute(
                    "SELECT sku, name, unit, qty FROM inventory_items WHERE sku = %s FOR UPDATE",
                    (sku,),
                ).fetchone()
                if item is None:
                    raise ItemNotFound(sku)

                # 3) Idempotensi (dicek SETELAH lock supaya bebas race):
                #    request_id yang sama & sudah sukses -> kembalikan hasil lama, jangan eksekusi ulang
                prev = conn.execute(
                    """SELECT id, request_id, branch_id, sku, action, delta,
                              qty_before, qty_after, created_at
                       FROM stock_transactions
                       WHERE request_id = %s AND status = 'SUCCESS'""",
                    (request_id,),
                ).fetchone()
                if prev is not None:
                    if (prev["branch_id"], prev["sku"], prev["delta"]) != (branch_id, sku, delta):
                        raise RequestIdMismatch(request_id)
                    return _tx_to_result(prev, item["unit"], replay=True)

                # 4) Cek ketersediaan stok pada data TERBARU (sudah terkunci)
                before = item["qty"]
                if before + delta < 0:
                    raise InsufficientStock(sku, item["unit"], before, quantity)

                # 5) Update stok + catat riwayat (satu transaksi atomik)
                after = conn.execute(
                    "UPDATE inventory_items SET qty = qty + %s, updated_at = now() "
                    "WHERE sku = %s RETURNING qty",
                    (delta, sku),
                ).fetchone()["qty"]

                log = conn.execute(
                    """INSERT INTO stock_transactions
                           (request_id, branch_id, sku, action, delta, qty_before, qty_after, status, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'SUCCESS', %s)
                       RETURNING id, request_id, branch_id, sku, action, delta,
                                 qty_before, qty_after, created_at""",
                    (request_id, branch_id, sku, action, delta, before, after, note),
                ).fetchone()

                return _tx_to_result(log, item["unit"], replay=False)
            # keluar dari blok transaction tanpa error -> COMMIT -> lock dilepas

    except InsufficientStock as exc:
        # Transaksi stok sudah ROLLBACK. Catat penolakan di transaksi terpisah (audit trail).
        _log_rejected(branch_id, sku, action, delta, request_id, exc.available, note)
        raise


def _log_rejected(branch_id, sku, action, delta, request_id, qty_now, note):
    try:
        with pool.connection() as conn:
            conn.execute(
                """INSERT INTO stock_transactions
                       (request_id, branch_id, sku, action, delta, qty_before, qty_after, status, note)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'REJECTED', %s)""",
                (request_id, branch_id, sku, action, delta, qty_now, qty_now,
                 (note + " | " if note else "") + "ditolak: stok tidak mencukupi"),
            )
    except Exception:  # log gagal tidak boleh menutupi jawaban 409 ke client
        logger.exception("Gagal mencatat log REJECTED")

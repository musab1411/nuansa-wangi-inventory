"""
Backend database MySQL 8.0+ (Laragon / XAMPP / MySQL Community).

Implementasi ini setara dengan db_postgres.py, tetapi memakai PyMySQL + DBUtils
karena MySQL tidak mendukung beberapa fitur PostgreSQL:

    PostgreSQL                          MySQL
    ---------------------------------   ------------------------------------
    UPDATE ... RETURNING qty            UPDATE lalu SELECT ulang
    INSERT ... RETURNING id             cursor.lastrowid
    UNIQUE INDEX parsial (SUCCESS)      generated column + UNIQUE index
    set_config('lock_timeout')          innodb_lock_wait_timeout
    default READ COMMITTED              harus di-set eksplisit (default REPEATABLE READ)

Mengapa READ COMMITTED wajib:
    MySQL default REPEATABLE READ membaca snapshot yang diambil di awal transaksi.
    Pengecekan idempotensi di bawah memakai SELECT biasa; dengan REPEATABLE READ,
    request kedua bisa TIDAK melihat transaksi pertama yang baru commit sehingga
    lolos pengecekan. READ COMMITTED membuat SELECT melihat data terbaru.
"""
import functools
import logging
import os
from contextlib import contextmanager

import pymysql
from dbutils.pooled_db import PooledDB

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

logger = logging.getLogger("inventory.db.mysql")

BACKEND = "mysql"

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "nuansa_wangi")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
LOCK_TIMEOUT_MS = int(os.getenv("LOCK_TIMEOUT_MS", "5000"))
LOCK_TIMEOUT_S = max(1, (LOCK_TIMEOUT_MS + 999) // 1000)  # MySQL memakai detik (bulat)

_pool: PooledDB | None = None


# ----------------------------------------------------------------------
# Siklus hidup pool + terjemahan error driver
# ----------------------------------------------------------------------
def is_configured() -> bool:
    return bool(MYSQL_HOST and MYSQL_USER and MYSQL_DATABASE)


def open_pool():
    global _pool
    if not is_configured():
        raise RuntimeError(
            "Konfigurasi MySQL belum lengkap. Isi MYSQL_HOST/MYSQL_USER/MYSQL_DATABASE di .env."
        )
    if _pool is not None:
        return
    _pool = PooledDB(
        creator=pymysql,
        mincached=DB_POOL_MIN,      # buka koneksi awal -> gagal jelas kalau MySQL mati
        maxcached=DB_POOL_MAX,
        maxconnections=DB_POOL_MAX,
        blocking=True,
        ping=1,                     # cek koneksi basi saat diambil dari pool
        setsession=[
            "SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED",
            f"SET SESSION innodb_lock_wait_timeout = {LOCK_TIMEOUT_S}",
        ],
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
        connect_timeout=10,
        read_timeout=30,
        write_timeout=30,
    )
    logger.info("MySQL pool siap (%s:%s/%s)", MYSQL_HOST, MYSQL_PORT, MYSQL_DATABASE)


def close_pool():
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None


def _get_pool() -> PooledDB:
    if _pool is None:
        open_pool()
    return _pool


@contextmanager
def _connection():
    """Ambil koneksi dari pool dan pastikan selalu dikembalikan."""
    conn = _get_pool().connection()
    try:
        yield conn
    finally:
        conn.close()


def _guard(fn):
    """Terjemahkan error PyMySQL menjadi exception domain yang sama dengan PostgreSQL."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except pymysql.MySQLError as exc:
            errno = exc.args[0] if exc.args else None
            if errno in (1205, 1213):        # lock wait timeout / deadlock
                raise LockTimeout(str(exc)) from exc
            if errno == 1062:                # duplicate entry (unique index)
                raise RequestIdConflict(str(exc)) from exc
            if errno == 3819:                # check constraint violated
                raise ConstraintViolation(str(exc)) from exc
            if errno == 1452:                # foreign key constraint fails
                raise ConstraintViolation(str(exc)) from exc
            raise DatabaseUnavailable(str(exc)) from exc

    return wrapper


# ----------------------------------------------------------------------
# Query baca
# ----------------------------------------------------------------------
@_guard
def ping() -> bool:
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
        conn.rollback()
    return True


@_guard
def list_items(sku: str | None = None) -> list[dict]:
    sql = "SELECT sku, name, category, unit, qty, updated_at FROM inventory_items"
    params: list = []
    if sku:
        sql += " WHERE sku = %s"
        params.append(sku)
    sql += " ORDER BY category, sku"
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.rollback()  # transaksi baca ditutup, koneksi kembali bersih ke pool
        return rows


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
    with _connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.rollback()
        return rows


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

    conn = None
    try:
        conn = _get_pool().connection()
        with conn.cursor() as cur:
            # Isolation READ COMMITTED + innodb_lock_wait_timeout sudah dipasang
            # lewat setsession saat koneksi dibuat.

            # 1) Validasi cabang terdaftar
            cur.execute("SELECT 1 FROM branches WHERE id = %s", (branch_id,))
            if cur.fetchone() is None:
                raise BranchNotFound(branch_id)

            # 2) PESSIMISTIC LOCK: kunci baris stok sampai transaksi selesai
            cur.execute(
                "SELECT sku, name, unit, qty FROM inventory_items WHERE sku = %s FOR UPDATE",
                (sku,),
            )
            item = cur.fetchone()
            if item is None:
                raise ItemNotFound(sku)

            # 3) Idempotensi (dicek SETELAH lock, dengan READ COMMITTED):
            #    request_id yang sama & sudah sukses -> kembalikan hasil lama
            cur.execute(
                """SELECT id, request_id, branch_id, sku, action, delta,
                          qty_before, qty_after, created_at
                   FROM stock_transactions
                   WHERE request_id = %s AND status = 'SUCCESS'""",
                (request_id,),
            )
            prev = cur.fetchone()
            if prev is not None:
                if (prev["branch_id"], prev["sku"], prev["delta"]) != (branch_id, sku, delta):
                    raise RequestIdMismatch(request_id)
                result = _tx_to_result(prev, item["unit"], replay=True)
                conn.rollback()
                return result

            # 4) Cek ketersediaan stok pada data TERBARU (sudah terkunci)
            before = item["qty"]
            if before + delta < 0:
                raise InsufficientStock(sku, item["unit"], before, quantity)

            # 5) Update stok (tanpa RETURNING -> SELECT ulang), lalu catat riwayat
            cur.execute(
                "UPDATE inventory_items SET qty = qty + %s WHERE sku = %s",
                (delta, sku),
            )
            cur.execute("SELECT qty FROM inventory_items WHERE sku = %s", (sku,))
            after = cur.fetchone()["qty"]

            cur.execute(
                """INSERT INTO stock_transactions
                       (request_id, branch_id, sku, action, delta, qty_before, qty_after, status, note)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'SUCCESS', %s)""",
                (request_id, branch_id, sku, action, delta, before, after, note),
            )
            tx_id = cur.lastrowid

            cur.execute(
                """SELECT id, request_id, branch_id, sku, action, delta,
                          qty_before, qty_after, created_at
                   FROM stock_transactions WHERE id = %s""",
                (tx_id,),
            )
            log = cur.fetchone()

        conn.commit()  # COMMIT -> lock dilepas
        return _tx_to_result(log, item["unit"], replay=False)

    except InsufficientStock as exc:
        # Transaksi stok sudah ROLLBACK. Catat penolakan di transaksi terpisah (audit trail).
        if conn is not None:
            conn.rollback()
        _log_rejected(branch_id, sku, action, delta, request_id, exc.available, note)
        raise
    except Exception:
        if conn is not None:
            conn.rollback()
        raise
    finally:
        if conn is not None:
            conn.close()


def _log_rejected(branch_id, sku, action, delta, request_id, qty_now, note):
    try:
        with _connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO stock_transactions
                           (request_id, branch_id, sku, action, delta, qty_before, qty_after, status, note)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, 'REJECTED', %s)""",
                    (request_id, branch_id, sku, action, delta, qty_now, qty_now,
                     (note + " | " if note else "") + "ditolak: stok tidak mencukupi"),
                )
            conn.commit()
    except Exception:  # log gagal tidak boleh menutupi jawaban 409 ke client
        logger.exception("Gagal mencatat log REJECTED")

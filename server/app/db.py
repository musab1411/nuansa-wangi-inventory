"""
Lapisan database: koneksi ke Supabase (PostgreSQL) + logika transaksi stok.

Inti Pessimistic Locking ada di apply_stock_update():
    BEGIN;
    SELECT ... FROM inventory_items WHERE sku = :sku FOR UPDATE;   -- kunci baris
    (cek stok -> UPDATE stok -> INSERT log)
    COMMIT;  (atau ROLLBACK kalau stok tidak cukup / ada error)

Transaksi lain yang menyentuh baris yang sama akan MENUNGGU di baris FOR UPDATE
sampai transaksi pertama selesai, lalu membaca stok TERBARU (READ COMMITTED).
"""
import logging
import os

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

logger = logging.getLogger("inventory.db")

DATABASE_URL = os.environ.get("DATABASE_URL", "")
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))
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
    timeout=10,  # maks. menunggu koneksi bebas dari pool
    open=False,
)


# ----------------------------------------------------------------------
# Exception domain (diterjemahkan jadi HTTP status di main.py)
# ----------------------------------------------------------------------
class BranchNotFound(Exception):
    def __init__(self, branch_id):
        super().__init__(branch_id)
        self.branch_id = branch_id


class ItemNotFound(Exception):
    def __init__(self, sku):
        super().__init__(sku)
        self.sku = sku


class InsufficientStock(Exception):
    def __init__(self, sku, unit, available, requested):
        super().__init__(f"{sku}: tersedia {available} {unit}, diminta {requested} {unit}")
        self.sku = sku
        self.unit = unit
        self.available = available
        self.requested = requested


class RequestIdMismatch(Exception):
    def __init__(self, request_id):
        super().__init__(request_id)
        self.request_id = request_id


# ----------------------------------------------------------------------
# Query baca
# ----------------------------------------------------------------------
def ping() -> bool:
    with pool.connection() as conn:
        conn.execute("SELECT 1")
    return True


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

"""
Facade lapisan database.

Pilih backend lewat environment variable DB_BACKEND:

    DB_BACKEND=postgres   -> Supabase / PostgreSQL (db_postgres.py)
    DB_BACKEND=mysql      -> MySQL 8.0+ / Laragon  (db_mysql.py)

Kalau DB_BACKEND tidak diisi, backend ditebak: DATABASE_URL ada -> postgres,
kalau tidak ada -> mysql. Dengan begitu mesin yang hanya meng-install
requirements-mysql.txt tetap bisa import modul ini tanpa psycopg.

main.py hanya mengimpor modul ini, jadi tidak peduli driver apa yang dipakai.
"""
import os

# Muat file .env kalau ada (untuk menjalankan langsung dari host tanpa Docker).
# Di Docker, variabel sudah disuntikkan lewat env_file, jadi ini tidak berpengaruh.
try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # dotenv opsional; environment variable tetap dipakai
    pass

from .errors import (  # noqa: F401  (re-export untuk main.py)
    BranchNotFound,
    ConstraintViolation,
    DatabaseUnavailable,
    InsufficientStock,
    ItemNotFound,
    LockTimeout,
    RequestIdConflict,
    RequestIdMismatch,
)

DB_BACKEND = os.getenv("DB_BACKEND", "").strip().lower()
if not DB_BACKEND:
    DB_BACKEND = "postgres" if os.getenv("DATABASE_URL") else "mysql"

if DB_BACKEND in ("mysql", "mariadb"):
    from . import db_mysql as _impl
elif DB_BACKEND in ("postgres", "postgresql", "supabase"):
    from . import db_postgres as _impl
else:
    raise RuntimeError(
        f"DB_BACKEND='{DB_BACKEND}' tidak dikenal. Pakai 'postgres' atau 'mysql'."
    )

BACKEND = _impl.BACKEND

# Siklus hidup pool
is_configured = _impl.is_configured
open_pool = _impl.open_pool
close_pool = _impl.close_pool

# Operasi database
ping = _impl.ping
list_items = _impl.list_items
get_item = _impl.get_item
list_logs = _impl.list_logs
apply_stock_update = _impl.apply_stock_update

#!/usr/bin/env python3
"""
Backup logis database ke file JSON - Nuansa Wangi.

Snapshot bersifat portabel: bisa dibuat dari PostgreSQL lalu di-restore ke
MySQL (dan sebaliknya) karena memakai lapisan db.py, bukan pg_dump/mysqldump.

Contoh (dari root project):
    python scripts/backup.py
    python scripts/backup.py --out backups/manual-1.json
    python scripts/backup.py --keep 30
"""
import argparse
import json
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "server"))

from app import db  # noqa: E402

BACKUP_DIR = ROOT / "backups"


def json_default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"Tipe {type(value).__name__} tidak bisa diserialisasi")


def prune(folder: Path, keep: int) -> list[Path]:
    files = sorted(folder.glob("nuansa-wangi-*.json"), key=lambda p: p.stat().st_mtime)
    removed = []
    while len(files) > keep:
        old = files.pop(0)
        old.unlink()
        removed.append(old)
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(description="Backup database Nuansa Wangi ke JSON")
    parser.add_argument("--out", default=None, help="path file output (default: backups/nuansa-wangi-<waktu>.json)")
    parser.add_argument("--keep", type=int, default=14, help="simpan N backup terbaru (default 14, 0 = semua)")
    parser.add_argument("--quiet", action="store_true", help="hanya cetak path file")
    args = parser.parse_args()

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")

    db.open_pool()
    try:
        snapshot = db.export_snapshot()
    finally:
        db.close_pool()

    snapshot["format"] = "nuansa-wangi-snapshot"
    snapshot["format_version"] = 1

    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        out_path = BACKUP_DIR / f"nuansa-wangi-{snapshot['backend']}-{stamp}.json"

    with out_path.open("w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, ensure_ascii=False, indent=2, default=json_default)

    size_kb = out_path.stat().st_size / 1024
    if not args.quiet:
        print(f"[OK] Backup {snapshot['backend']} -> {out_path}")
        print(f"     branches={len(snapshot['branches'])}  "
              f"items={len(snapshot['inventory_items'])}  "
              f"transactions={len(snapshot['stock_transactions'])}  "
              f"({size_kb:.1f} KB)")

    if args.keep > 0:
        removed = prune(BACKUP_DIR, args.keep)
        if removed and not args.quiet:
            print(f"     prune: {len(removed)} backup lama dihapus (menyimpan {args.keep} terbaru)")

    print(out_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())

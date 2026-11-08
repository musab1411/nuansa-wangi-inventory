#!/usr/bin/env python3
"""
Restore database dari snapshot JSON - Nuansa Wangi.

PERINGATAN: restore MENGGANTI seluruh isi tabel branches, inventory_items,
dan stock_transactions dengan isi snapshot. Lakukan backup dulu.

Contoh (dari root project):
    python scripts/restore.py --file backups/nuansa-wangi-mysql-20240101-120000.json
    python scripts/restore.py --latest --yes
"""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT / "server"))

from app import db  # noqa: E402

BACKUP_DIR = ROOT / "backups"


def latest_backup() -> Path:
    files = sorted(BACKUP_DIR.glob("nuansa-wangi-*.json"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise FileNotFoundError(f"Tidak ada file backup di {BACKUP_DIR}")
    return files[-1]


def summarize(snapshot: dict) -> None:
    print("Ringkasan snapshot:")
    print(f"  format       : {snapshot.get('format', '-')} v{snapshot.get('format_version', '?')}")
    print(f"  backend asal : {snapshot.get('backend', '-')}")
    print(f"  diekspor     : {snapshot.get('exported_at', '-')}")
    print(f"  branches     : {len(snapshot.get('branches', []))}")
    print(f"  items        : {len(snapshot.get('inventory_items', []))}")
    print(f"  transactions : {len(snapshot.get('stock_transactions', []))}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore database Nuansa Wangi dari JSON")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--file", help="path snapshot JSON")
    group.add_argument("--latest", action="store_true", help="pakai backup terbaru di folder backups/")
    parser.add_argument("--yes", action="store_true", help="langsung restore tanpa konfirmasi")
    args = parser.parse_args()

    try:
        path = latest_backup() if args.latest else Path(args.file)
    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not path.is_absolute():
        path = ROOT / path

    if not path.is_file():
        print(f"[ERROR] File tidak ditemukan: {path}", file=sys.stderr)
        return 2

    with path.open("r", encoding="utf-8") as fh:
        snapshot = json.load(fh)

    for key in ("branches", "inventory_items", "stock_transactions"):
        if not isinstance(snapshot.get(key), list):
            print(f"[ERROR] Snapshot tidak valid: field '{key}' harus berupa list.", file=sys.stderr)
            return 2

    print(f"File: {path}")
    summarize(snapshot)
    print()
    print(f"Target database saat ini: {db.BACKEND}")
    print(f"Semua data di target akan DIGANTI dengan isi snapshot di atas.")

    if not args.yes:
        if not sys.stdin.isatty():
            print("\n[ERROR] Mode non-interaktif: tambahkan --yes untuk melanjutkan.", file=sys.stderr)
            return 2
        answer = input("\nKetik 'RESTORE' untuk melanjutkan: ").strip()
        if answer != "RESTORE":
            print("Dibatalkan.")
            return 1

    db.open_pool()
    try:
        result = db.import_snapshot(snapshot)
    finally:
        db.close_pool()

    print("\n[OK] Restore selesai:")
    print(f"     branches={result['branches']}  "
          f"items={result['inventory_items']}  "
          f"transactions={result['stock_transactions']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""
Kelola API key per cabang - Nuansa Wangi.

Database hanya menyimpan SHA-256 dari kunci, jadi kunci mentah HANYA
ditampilkan sekali saat dibuat/di-rotate. Simpan di tempat aman (password
manager / .env yang tidak di-commit).

Contoh (jalankan dari root project):
    python scripts/manage_keys.py list
    python scripts/manage_keys.py rotate --branch branch-a
    python scripts/manage_keys.py set --branch branch-a --key "kunci-pilihan-sendiri"
    python scripts/manage_keys.py disable --branch branch-c
    python scripts/manage_keys.py verify --key "nw-demo-branch-a-7f3a9c1e"
"""
import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)                       # supaya python-dotenv menemukan .env
sys.path.insert(0, str(ROOT / "server"))

from app import db                    # noqa: E402
from app.auth import hash_key         # noqa: E402


def cmd_list(_args) -> int:
    branches = db.list_branches()
    if not branches:
        print("Belum ada cabang di database.")
        return 0
    print(f"{'ID':<12} {'NAMA':<28} STATUS")
    print("-" * 58)
    for b in branches:
        status = "AKTIF" if b["has_key"] else "nonaktif (tanpa kunci)"
        print(f"{b['id']:<12} {b['name']:<28} {status}")
    return 0


def _save_key(branch_id: str, raw: str) -> None:
    db.set_branch_api_key(branch_id, hash_key(raw))
    print(f"[OK] Kunci untuk cabang '{branch_id}' tersimpan (hash SHA-256).")
    print()
    print("  KUNCI BARU (hanya ditampilkan sekali):")
    print(f"    {raw}")
    print()
    print("  Simpan sekarang, lalu perbarui BRANCH_API_KEY di .env / docker-compose")
    print("  dan restart client cabang tersebut.")


def cmd_rotate(args) -> int:
    raw = args.key or ("nw-" + secrets.token_urlsafe(max(16, args.length)))
    _save_key(args.branch, raw)
    return 0


def cmd_set(args) -> int:
    if len(args.key) < 16:
        print("[ERROR] Kunci terlalu pendek; minimal 16 karakter.", file=sys.stderr)
        return 2
    _save_key(args.branch, args.key)
    return 0


def cmd_disable(args) -> int:
    db.set_branch_api_key(args.branch, None)
    print(f"[OK] Cabang '{args.branch}' dinonaktifkan: kunci lama tidak berlaku lagi.")
    return 0


def cmd_verify(args) -> int:
    branch_id = db.branch_by_api_key(hash_key(args.key))
    if branch_id is None:
        print("Tidak cocok dengan cabang mana pun (atau cabang dinonaktifkan).")
        return 1
    print(f"Kunci valid untuk cabang: {branch_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Kelola API key per cabang Nuansa Wangi")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="tampilkan cabang + status kunci").set_defaults(func=cmd_list)

    p = sub.add_parser("rotate", help="buat kunci acak baru untuk satu cabang")
    p.add_argument("--branch", required=True, help="contoh: branch-a")
    p.add_argument("--key", default=None, help="pakai kunci tertentu, bukan acak")
    p.add_argument("--length", type=int, default=32, help="panjang kunci acak (default 32)")
    p.set_defaults(func=cmd_rotate)

    p = sub.add_parser("set", help="set kunci tertentu (minimal 16 karakter)")
    p.add_argument("--branch", required=True)
    p.add_argument("--key", required=True)
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("disable", help="hapus kunci cabang (cabang tidak bisa transaksi)")
    p.add_argument("--branch", required=True)
    p.set_defaults(func=cmd_disable)

    p = sub.add_parser("verify", help="cek kunci ini milik cabang mana")
    p.add_argument("--key", required=True)
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()

    db.open_pool()
    try:
        return args.func(args)
    except db.BranchNotFound as exc:
        print(f"[ERROR] Cabang '{exc.branch_id}' tidak ada di database.", file=sys.stderr)
        return 2
    finally:
        db.close_pool()


if __name__ == "__main__":
    sys.exit(main())

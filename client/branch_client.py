#!/usr/bin/env python3
"""
Node Cabang (Client) - Nuansa Wangi.

Setiap container cabang menjalankan client ini. Identitas cabang diambil dari
environment variable BRANCH_ID, alamat server dari API_URL, dan API key cabang
dari BRANCH_API_KEY.

Contoh (dari dalam container cabang):
    python branch_client.py ping
    python branch_client.py items
    python branch_client.py update --sku BIBIT-BACCARAT --action SUBTRACT --qty 10
    python branch_client.py logs --limit 10
    python branch_client.py logs --mine

Semua endpoint selain /health wajib menyertakan header X-API-Key. Server
menolak (401) kalau kunci tidak ada/tidak dikenal, dan menolak (403) kalau
kunci cabang dipakai untuk cabang lain.
"""
import argparse
import json
import os
import sys
import time
import uuid

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
BRANCH_ID = os.getenv("BRANCH_ID", "branch-a")
BRANCH_NAME = os.getenv("BRANCH_NAME", BRANCH_ID)
BRANCH_API_KEY = os.getenv("BRANCH_API_KEY", "").strip()
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "15"))
MAX_RETRY = int(os.getenv("MAX_RETRY", "3"))


def auth_headers() -> dict:
    """Header autentikasi. Kalau BRANCH_API_KEY kosong, server akan menjawab 401."""
    if not BRANCH_API_KEY:
        print(
            f"[{BRANCH_NAME}] PERINGATAN: BRANCH_API_KEY belum diisi; "
            "server akan menolak dengan HTTP 401.",
            file=sys.stderr,
        )
        return {}
    return {"X-API-Key": BRANCH_API_KEY}


def show(data) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


def request_with_retry(method: str, path: str, **kwargs) -> requests.Response:
    """Retry hanya untuk kegagalan jaringan. Aman untuk POST karena request_id (idempotency key) tetap sama."""
    last_exc = None
    for attempt in range(1, MAX_RETRY + 1):
        try:
            return requests.request(method, f"{API_URL}{path}", timeout=TIMEOUT, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            last_exc = exc
            wait = 2 ** (attempt - 1)
            print(f"[{BRANCH_NAME}] koneksi gagal (percobaan {attempt}/{MAX_RETRY}): {exc.__class__.__name__}", file=sys.stderr)
            if attempt < MAX_RETRY:
                print(f"[{BRANCH_NAME}] coba lagi dalam {wait} detik ...", file=sys.stderr)
                time.sleep(wait)
    raise SystemExit(f"[{BRANCH_NAME}] server tidak dapat dihubungi di {API_URL}: {last_exc.__class__.__name__}")


def cmd_ping(_args) -> int:
    r = request_with_retry("GET", "/health")
    print(f"[{BRANCH_NAME} | {BRANCH_ID}] -> {API_URL}/health : HTTP {r.status_code} {r.text.strip()}")
    return 0 if r.ok else 1


def cmd_items(_args) -> int:
    r = request_with_retry("GET", "/items", headers=auth_headers())
    show(r.json())
    return 0 if r.ok else 1


def cmd_update(args) -> int:
    body = {
        "branch_id": BRANCH_ID,
        "sku": args.sku,
        "action": args.action,
        "quantity": args.qty,
        "request_id": args.request_id or f"{BRANCH_ID}-{uuid.uuid4().hex[:12]}",
    }
    if args.note:
        body["note"] = args.note
    r = request_with_retry("POST", "/items/update", json=body, headers=auth_headers())
    print(f"[{BRANCH_NAME}] HTTP {r.status_code}")
    show(r.json())
    return 0 if r.ok else 1


def cmd_logs(args) -> int:
    params = {"limit": args.limit}
    if args.mine:
        params["branch_id"] = BRANCH_ID
    if args.sku:
        params["sku"] = args.sku
    r = request_with_retry("GET", "/logs", params=params, headers=auth_headers())
    show(r.json())
    return 0 if r.ok else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=f"Client {BRANCH_NAME} ({BRANCH_ID})")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("ping", help="cek koneksi ke server pusat").set_defaults(func=cmd_ping)
    sub.add_parser("items", help="lihat stok").set_defaults(func=cmd_items)

    p = sub.add_parser("update", help="tambah/kurangi stok")
    p.add_argument("--sku", required=True)
    p.add_argument("--action", required=True, choices=["ADD", "SUBTRACT"], type=str.upper)
    p.add_argument("--qty", required=True, type=int)
    p.add_argument("--request-id", dest="request_id", default=None)
    p.add_argument("--note", default=None)
    p.set_defaults(func=cmd_update)

    p = sub.add_parser("logs", help="lihat riwayat transaksi")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--sku", default=None)
    p.add_argument("--mine", action="store_true", help="hanya transaksi cabang ini")
    p.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())

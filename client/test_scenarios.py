#!/usr/bin/env python3
"""
test_scenarios.py - Pengujian otomatis Sistem Inventaris Multi-Cabang Nuansa Wangi

Skenario:
  1. Update stok BERURUTAN dari Cabang 1 (Empang) dan Cabang 2 (Cilendek).
  2. Request PARALEL (multithreading) dari Cabang 1 & 2 memperebutkan sisa stok terakhir
     -> uji Race Condition / Zero Negative Stock / konsistensi data.
  3. Penanganan ERROR: request tidak valid, resource tidak ada, stok kurang,
     koneksi gagal + retry idempoten.
  4. Keamanan: API key per cabang wajib, kunci cabang tidak bisa dipakai
     untuk cabang lain, dan endpoint admin hanya untuk kunci admin.

Cara pakai:
  python test_scenarios.py            # semua skenario
  python test_scenarios.py 2          # hanya skenario 2
  API_URL=http://localhost:8000 python test_scenarios.py

Kunci API diambil dari environment variable, dengan default kunci DEMO yang
sama seperti di database/schema.sql:
  BRANCH_A_API_KEY, BRANCH_B_API_KEY, BRANCH_C_API_KEY, ADMIN_API_KEY
Ganti kunci demo sebelum dipakai sungguhan (scripts/manage_keys.py).

Exit code 0 = semua lulus, 1 = ada yang gagal, 2 = server tidak bisa dihubungi.
"""
import argparse
import os
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import requests

API_URL = os.getenv("API_URL", "http://localhost:8000").rstrip("/")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "30"))
DEAD_URL = "http://127.0.0.1:9"  # port 9 hampir pasti tertutup -> simulasi server mati

CABANG_1 = "branch-a"  # Cabang Empang
CABANG_2 = "branch-b"  # Cabang Cilendek
CABANG_3 = "branch-c"  # Cabang Cibinong

BRANCH_KEYS = {
    CABANG_1: os.getenv("BRANCH_A_API_KEY", "nw-demo-branch-a-7f3a9c1e").strip(),
    CABANG_2: os.getenv("BRANCH_B_API_KEY", "nw-demo-branch-b-2b8d4f6a").strip(),
    CABANG_3: os.getenv("BRANCH_C_API_KEY", "nw-demo-branch-c-9c5e7a1d").strip(),
}
ADMIN_KEY = os.getenv("ADMIN_API_KEY", "nw-demo-admin-4d2f8b6c").strip()

SKU_BIBIT = "BIBIT-BACCARAT"  # satuan ml
SKU_BOTOL = "BOTOL-KACA-30"   # satuan pcs
SEED_STOCK = {SKU_BIBIT: 100, SKU_BOTOL: 50}  # stok awal sesuai database/schema.sql


# ----------------------------------------------------------------------
# Helper
# ----------------------------------------------------------------------
class Report:
    def __init__(self):
        self.passed = 0
        self.failed = 0

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"   [PASS] {name}")
        else:
            self.failed += 1
            print(f"   [FAIL] {name}" + (f"  -> {detail}" if detail else ""))
        return ok


def header(title: str) -> None:
    print("\n" + "=" * 72)
    print(title)
    print("=" * 72)


def new_id(tag: str) -> str:
    return f"test-{tag}-{uuid.uuid4().hex[:10]}"


def jget(resp: requests.Response, key: str):
    try:
        return resp.json().get(key)
    except (ValueError, AttributeError):
        return None


def error_code(resp: requests.Response):
    try:
        detail = resp.json().get("detail")
    except (ValueError, AttributeError):
        return None
    return detail.get("code") if isinstance(detail, dict) else None


def body_of(branch, sku, action, qty, request_id=None, note=None) -> dict:
    body = {
        "branch_id": branch,
        "sku": sku,
        "action": action,
        "quantity": qty,
        "request_id": request_id or new_id("upd"),
    }
    if note:
        body["note"] = note
    return body


def headers_for(branch: str) -> dict:
    """Header API key milik cabang tertentu (dipakai juga untuk uji otorisasi)."""
    key = BRANCH_KEYS.get(branch, "")
    return {"X-API-Key": key} if key else {}


def admin_headers() -> dict:
    return {"X-API-Key": ADMIN_KEY} if ADMIN_KEY else {}


def update_headers(body: dict) -> dict:
    """Kunci mengikuti branch_id di body; cabang tak dikenal -> pakai kunci admin."""
    return headers_for(body.get("branch_id", "")) or admin_headers()


def post_update(body, base_url: str = None, timeout: float = None,
                headers: dict = None) -> requests.Response:
    return requests.post(
        f"{base_url or API_URL}/items/update",
        json=body,
        headers=headers if headers is not None else update_headers(body),
        timeout=timeout or TIMEOUT,
    )


def get_stock(sku: str) -> int:
    r = requests.get(f"{API_URL}/items/{sku}", headers=headers_for(CABANG_1), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["qty"]


def get_logs(**params) -> list:
    # Admin melihat semua cabang; dipakai untuk memeriksa riwayat lintas cabang.
    r = requests.get(f"{API_URL}/logs", params=params, headers=admin_headers(), timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()["logs"]


def set_stock(sku: str, target: int) -> None:
    """Normalisasi stok ke angka tertentu lewat API publik (ADD/SUBTRACT), supaya tiap skenario mulai dari kondisi sama."""
    current = get_stock(sku)
    if current == target:
        return
    diff = target - current
    action = "ADD" if diff > 0 else "SUBTRACT"
    r = post_update(body_of(CABANG_1, sku, action, abs(diff), new_id("setup"), "[TEST] setup stok"))
    r.raise_for_status()


def wait_for_server(max_wait: int = 30) -> bool:
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            if requests.get(f"{API_URL}/health", timeout=3).ok:
                return True
        except requests.RequestException:
            pass
        time.sleep(1)
    return False


def fire_simultaneously(sku: str, branches: list, qty: int, label: str) -> list:
    """Kirim len(branches) request SUBTRACT dalam waktu hampir bersamaan (threading.Barrier)."""
    n = len(branches)
    barrier = threading.Barrier(n)

    def worker(i: int) -> dict:
        branch = branches[i]
        rid = new_id(f"{label}{i}")
        body = body_of(branch, sku, "SUBTRACT", qty, rid, f"[TEST] {label}")
        try:
            barrier.wait(timeout=30)  # semua thread dilepas bersamaan
            t0 = time.perf_counter()
            r = post_update(body)
            return {"branch": branch, "request_id": rid, "status": r.status_code,
                    "code": error_code(r), "ms": (time.perf_counter() - t0) * 1000}
        except Exception as exc:  # noqa: BLE001
            return {"branch": branch, "request_id": rid, "status": None,
                    "code": exc.__class__.__name__, "ms": 0.0}

    with ThreadPoolExecutor(max_workers=n) as ex:
        return list(ex.map(worker, range(n)))


# ----------------------------------------------------------------------
# Skenario 1 - berurutan
# ----------------------------------------------------------------------
def scenario_1(rep: Report) -> None:
    header("SKENARIO 1 - Update stok berurutan (Cabang 1 & Cabang 2)")
    set_stock(SKU_BIBIT, 100)
    print(f"   Stok awal {SKU_BIBIT}: 100 ml\n")

    steps = [
        (CABANG_1, "SUBTRACT", 20),
        (CABANG_2, "SUBTRACT", 30),
        (CABANG_1, "ADD", 10),
        (CABANG_2, "SUBTRACT", 15),
    ]
    expected = 100
    ids = []
    for branch, action, qty in steps:
        rid = new_id("seq")
        ids.append(rid)
        r = post_update(body_of(branch, SKU_BIBIT, action, qty, rid, "[TEST] skenario 1"))
        expected += qty if action == "ADD" else -qty
        ok = r.status_code == 200 and jget(r, "qty_after") == expected
        rep.check(f"{branch} {action:<8} {qty:>3} ml  -> stok jadi {expected} ml", ok,
                  f"HTTP {r.status_code} {r.text[:150]}")

    rep.check(f"Stok akhir di server = {expected} ml (dibaca ulang dari database)",
              get_stock(SKU_BIBIT) == expected, f"stok sekarang {get_stock(SKU_BIBIT)}")

    logs = sorted((l for l in get_logs(sku=SKU_BIBIT, limit=200) if l["request_id"] in ids),
                  key=lambda l: l["id"])
    rep.check("Riwayat (/logs) mencatat 4 transaksi", len(logs) == 4, f"ditemukan {len(logs)}")
    rep.check("Cabang pada riwayat sesuai urutan pengiriman",
              [l["branch_id"] for l in logs] == [s[0] for s in steps])
    chain_ok = all(logs[i]["qty_before"] == logs[i - 1]["qty_after"] for i in range(1, len(logs)))
    rep.check("Rantai qty_before -> qty_after antar transaksi nyambung", chain_ok and len(logs) == 4)


# ----------------------------------------------------------------------
# Skenario 2 - paralel / race condition
# ----------------------------------------------------------------------
def scenario_2(rep: Report) -> None:
    header("SKENARIO 2 - Request paralel memperebutkan stok (uji konsistensi)")

    # --- 2A: kasus persis dari proposal: stok 100 ml, dua cabang masing-masing minta 100 ml
    print("\n -- 2A. Stok 100 ml, Cabang 1 & Cabang 2 serentak minta 100 ml --")
    set_stock(SKU_BIBIT, 100)
    results = fire_simultaneously(SKU_BIBIT, [CABANG_1, CABANG_2], 100, "raceA")
    for r in results:
        print(f"   {r['branch']}: HTTP {r['status']} {r['code'] or 'OK'} ({r['ms']:.0f} ms)")
    statuses = sorted(r["status"] or 0 for r in results)
    rep.check("Tepat 1 request sukses (200) dan 1 ditolak (409)", statuses == [200, 409], f"status: {statuses}")
    rep.check("Request yang ditolak berkode INSUFFICIENT_STOCK",
              any(r["code"] == "INSUFFICIENT_STOCK" for r in results))
    final = get_stock(SKU_BIBIT)
    rep.check("Stok akhir tepat 0 ml (tidak minus)", final == 0, f"stok akhir {final}")
    ids = {r["request_id"] for r in results}
    logs = [l for l in get_logs(sku=SKU_BIBIT, limit=200) if l["request_id"] in ids]
    rep.check("Riwayat: 1 SUCCESS + 1 REJECTED",
              sorted(l["status"] for l in logs) == ["REJECTED", "SUCCESS"],
              f"status log: {[l['status'] for l in logs]}")

    # --- 2B: beban lebih berat: 12 request serentak (6 per cabang), stok 50 pcs, minta 10 pcs per request
    print("\n -- 2B. Stok 50 pcs, 12 request serentak (Cabang 1 & 2) @ 10 pcs --")
    set_stock(SKU_BOTOL, 50)
    branches = [CABANG_1 if i % 2 == 0 else CABANG_2 for i in range(12)]
    results = fire_simultaneously(SKU_BOTOL, branches, 10, "raceB")
    ok_n = sum(1 for r in results if r["status"] == 200)
    rej_n = sum(1 for r in results if r["status"] == 409)
    other = [r for r in results if r["status"] not in (200, 409)]
    print(f"   sukses={ok_n}  ditolak={rej_n}  lainnya={len(other)}")
    rep.check("Tepat 5 request sukses (5 x 10 = 50 pcs)", ok_n == 5, f"sukses={ok_n}")
    rep.check("7 request lainnya ditolak 409", rej_n == 7, f"ditolak={rej_n}")
    rep.check("Tidak ada error lain (500/503/timeout)", not other, f"{other}")
    final = get_stock(SKU_BOTOL)
    rep.check("Stok akhir tepat 0 pcs (tidak minus)", final == 0, f"stok akhir {final}")

    ids = {r["request_id"] for r in results}
    logs = [l for l in get_logs(sku=SKU_BOTOL, limit=200) if l["request_id"] in ids]
    success = sorted((l for l in logs if l["status"] == "SUCCESS"), key=lambda l: l["id"])
    chain_ok = (
        len(success) == 5
        and success[0]["qty_before"] == 50
        and success[-1]["qty_after"] == 0
        and all(success[i]["qty_before"] == success[i - 1]["qty_after"] for i in range(1, len(success)))
    )
    rep.check("Transaksi sukses terserialisasi rapi: 50 -> 40 -> 30 -> 20 -> 10 -> 0", chain_ok)
    rep.check("Semua riwayat punya qty_after >= 0", all(l["qty_after"] >= 0 for l in logs))


# ----------------------------------------------------------------------
# Skenario 3 - error handling
# ----------------------------------------------------------------------
def scenario_3(rep: Report) -> None:
    header("SKENARIO 3 - Penanganan error (request tidak valid & koneksi gagal)")
    set_stock(SKU_BIBIT, 100)
    before = get_stock(SKU_BIBIT)
    base = {"branch_id": CABANG_1, "sku": SKU_BIBIT, "action": "SUBTRACT", "quantity": 5}

    print("\n -- 3A. Request tidak valid / ditolak server --")
    cases = [
        ("quantity = 0",                          {**base, "quantity": 0},          422, None),
        ("quantity negatif (-5)",                 {**base, "quantity": -5},         422, None),
        ("quantity bukan angka ('banyak')",       {**base, "quantity": "banyak"},   422, None),
        ("quantity berupa string ('5')",          {**base, "quantity": "5"},        422, None),
        ("quantity desimal (1.5)",                {**base, "quantity": 1.5},        422, None),
        ("action tidak dikenal ('MULTIPLY')",     {**base, "action": "MULTIPLY"},   422, None),
        ("field 'sku' hilang",                    {k: v for k, v in base.items() if k != "sku"}, 422, None),
        ("cabang tidak terdaftar ('branch-z')",   {**base, "branch_id": "branch-z"}, 403, "BRANCH_NOT_REGISTERED"),
        ("SKU tidak ada ('BIBIT-GAIB')",          {**base, "sku": "BIBIT-GAIB"},    404, "ITEM_NOT_FOUND"),
        ("stok tidak cukup (minta 9999 ml)",      {**base, "quantity": 9999},       409, "INSUFFICIENT_STOCK"),
    ]
    for name, body, want_status, want_code in cases:
        body = {**body, "request_id": new_id("err")}
        r = post_update(body)
        ok = r.status_code == want_status and (want_code is None or error_code(r) == want_code)
        rep.check(f"{name:<40} -> HTTP {want_status}" + (f" {want_code}" if want_code else ""), ok,
                  f"dapat HTTP {r.status_code} {r.text[:120]}")

    r = requests.post(f"{API_URL}/items/update", data="{ini bukan json", timeout=TIMEOUT,
                      headers={**headers_for(CABANG_1), "Content-Type": "application/json"})
    rep.check(f"{'body JSON rusak':<40} -> HTTP 422", r.status_code == 422, f"dapat HTTP {r.status_code}")

    r = requests.get(f"{API_URL}/items/TIDAK-ADA", headers=headers_for(CABANG_1), timeout=TIMEOUT)
    rep.check(f"{'GET /items/<sku tidak ada>':<40} -> HTTP 404", r.status_code == 404, f"dapat HTTP {r.status_code}")

    r = requests.get(f"{API_URL}/logs", params={"limit": 0},
                     headers=headers_for(CABANG_1), timeout=TIMEOUT)
    rep.check(f"{'GET /logs?limit=0':<40} -> HTTP 422", r.status_code == 422, f"dapat HTTP {r.status_code}")

    after = get_stock(SKU_BIBIT)
    rep.check("Stok TIDAK berubah setelah semua request invalid tadi", after == before, f"{before} -> {after}")

    print("\n -- 3B. Koneksi gagal + retry idempoten --")
    try:
        requests.get(f"{DEAD_URL}/items", timeout=3)
        dead_ok = False
    except (requests.ConnectionError, requests.Timeout) as exc:
        dead_ok = True
        print(f"   server mati terdeteksi: {exc.__class__.__name__}")
    rep.check("Client mendeteksi server tidak aktif (ConnectionError), tidak crash", dead_ok)

    rid = new_id("retry")
    body = body_of(CABANG_3, SKU_BIBIT, "SUBTRACT", 7, rid, "[TEST] retry")
    attempts, resp = 0, None
    for base_url in (DEAD_URL, API_URL):  # percobaan 1 gagal (server mati), percobaan 2 ke server asli
        attempts += 1
        try:
            resp = post_update(body, base_url=base_url, timeout=3 if base_url == DEAD_URL else None)
            break
        except (requests.ConnectionError, requests.Timeout):
            print(f"   percobaan {attempts} gagal koneksi -> kirim ulang dengan request_id yang sama")
    rep.check("Percobaan ke-2 (retry) berhasil 200", resp is not None and resp.status_code == 200 and attempts == 2,
              f"attempts={attempts}")
    rep.check("Stok berkurang 7 ml", get_stock(SKU_BIBIT) == before - 7)

    # Respons hilang di jalan -> client tidak tahu sukses/tidak -> kirim ulang request_id yang sama
    r2 = post_update(body)
    rep.check("Kirim ulang request_id yang sama -> 200 + idempotent_replay=true",
              r2.status_code == 200 and jget(r2, "idempotent_replay") is True, f"HTTP {r2.status_code} {r2.text[:120]}")
    rep.check("Stok TIDAK terpotong dua kali (tetap sekali -7)", get_stock(SKU_BIBIT) == before - 7,
              f"stok {get_stock(SKU_BIBIT)}")

    r3 = post_update({**body, "quantity": 50})
    rep.check("request_id sama tapi isi berbeda -> 409 REQUEST_ID_MISMATCH",
              r3.status_code == 409 and error_code(r3) == "REQUEST_ID_MISMATCH", f"HTTP {r3.status_code} {r3.text[:120]}")

    items = requests.get(f"{API_URL}/items", headers=headers_for(CABANG_1), timeout=TIMEOUT).json()["items"]
    rep.check("Invarian akhir: semua stok >= 0", all(i["qty"] >= 0 for i in items))


# ----------------------------------------------------------------------
# Skenario 4 - keamanan API key
# ----------------------------------------------------------------------
def scenario_4(rep: Report) -> None:
    header("SKENARIO 4 - Keamanan: API key per cabang & hak admin")

    print("\n -- 4A. Kunci wajib untuk endpoint data --")
    r = requests.get(f"{API_URL}/items", timeout=TIMEOUT)
    rep.check("Tanpa X-API-Key                          -> HTTP 401 API_KEY_REQUIRED",
              r.status_code == 401 and error_code(r) == "API_KEY_REQUIRED",
              f"dapat HTTP {r.status_code} {r.text[:120]}")

    r = requests.get(f"{API_URL}/items", headers={"X-API-Key": "kunci-palsu-12345"},
                     timeout=TIMEOUT)
    rep.check("Kunci tidak dikenal                       -> HTTP 401 API_KEY_INVALID",
              r.status_code == 401 and error_code(r) == "API_KEY_INVALID",
              f"dapat HTTP {r.status_code} {r.text[:120]}")

    r = requests.get(f"{API_URL}/items", headers=headers_for(CABANG_1), timeout=TIMEOUT)
    rep.check("Kunci cabang valid                       -> HTTP 200",
              r.status_code == 200, f"dapat HTTP {r.status_code}")

    print("\n -- 4B. Kunci cabang hanya berlaku untuk cabangnya sendiri --")
    before = get_stock(SKU_BIBIT)
    rid = new_id("auth")
    body = body_of(CABANG_2, SKU_BIBIT, "SUBTRACT", 1, rid, "[TEST] auth mismatch")
    r = post_update(body, headers=headers_for(CABANG_1))
    rep.check("branch-a mencoba update branch-b          -> HTTP 403 BRANCH_MISMATCH",
              r.status_code == 403 and error_code(r) == "BRANCH_MISMATCH",
              f"dapat HTTP {r.status_code} {r.text[:120]}")
    rep.check("Stok tidak berubah karena request ditolak", get_stock(SKU_BIBIT) == before)

    r = requests.get(f"{API_URL}/logs", params={"branch_id": CABANG_2},
                     headers=headers_for(CABANG_1), timeout=TIMEOUT)
    rep.check("branch-a minta log branch-b               -> HTTP 403 BRANCH_MISMATCH",
              r.status_code == 403 and error_code(r) == "BRANCH_MISMATCH",
              f"dapat HTTP {r.status_code} {r.text[:120]}")

    # Pastikan ada log milik branch-a (netral: -1 lalu +1) walau skenario 4 dijalankan sendiri.
    for action in ("SUBTRACT", "ADD"):
        requests.post(
            f"{API_URL}/items/update",
            json=body_of(CABANG_1, SKU_BIBIT, action, 1, new_id("authlog"), "[TEST] auth log"),
            headers=headers_for(CABANG_1), timeout=TIMEOUT,
        )
    r = requests.get(f"{API_URL}/logs", params={"limit": 100},
                     headers=headers_for(CABANG_1), timeout=TIMEOUT)
    logs = r.json().get("logs", []) if r.ok else []
    rep.check("branch-a /logs tanpa filter otomatis dibatasi ke branch-a",
              r.status_code == 200 and bool(logs)
              and all(l["branch_id"] == CABANG_1 for l in logs),
              f"HTTP {r.status_code}, cabang={sorted({l.get('branch_id') for l in logs})}")

    # Pastikan ada log branch-b untuk uji filter admin.
    for action in ("SUBTRACT", "ADD"):
        requests.post(
            f"{API_URL}/items/update",
            json=body_of(CABANG_2, SKU_BIBIT, action, 1, new_id("authlog"), "[TEST] auth log"),
            headers=headers_for(CABANG_2), timeout=TIMEOUT,
        )

    print("\n -- 4C. Endpoint admin hanya untuk kunci admin --")
    r = requests.get(f"{API_URL}/branches", headers=headers_for(CABANG_1), timeout=TIMEOUT)
    rep.check("branch-a GET /branches                   -> HTTP 403 ADMIN_REQUIRED",
              r.status_code == 403 and error_code(r) == "ADMIN_REQUIRED",
              f"dapat HTTP {r.status_code} {r.text[:120]}")

    r = requests.get(f"{API_URL}/summary", headers=headers_for(CABANG_1), timeout=TIMEOUT)
    rep.check("branch-a GET /summary                    -> HTTP 403 ADMIN_REQUIRED",
              r.status_code == 403 and error_code(r) == "ADMIN_REQUIRED",
              f"dapat HTTP {r.status_code} {r.text[:120]}")

    r = requests.get(f"{API_URL}/branches", headers=admin_headers(), timeout=TIMEOUT)
    data = r.json() if r.ok else {}
    rep.check("admin GET /branches                       -> HTTP 200 (>= 3 cabang)",
              r.status_code == 200 and data.get("count", 0) >= 3,
              f"HTTP {r.status_code} {r.text[:120]}")
    rep.check("Respons /branches tidak membocorkan hash kunci",
              all("api_key_hash" not in b for b in data.get("branches", [])))

    r = requests.get(f"{API_URL}/summary", headers=admin_headers(), timeout=TIMEOUT)
    summary = r.json() if r.ok else {}
    rep.check("admin GET /summary                       -> HTTP 200 + field lengkap",
              r.status_code == 200
              and all(k in summary for k in ("item_count", "total_qty", "success", "rejected")),
              f"HTTP {r.status_code} {r.text[:120]}")

    r = requests.get(f"{API_URL}/logs", params={"branch_id": CABANG_2, "limit": 100},
                     headers=admin_headers(), timeout=TIMEOUT)
    logs = r.json().get("logs", []) if r.ok else []
    rep.check("admin /logs?branch_id=branch-b hanya berisi branch-b",
              r.status_code == 200 and bool(logs)
              and all(l["branch_id"] == CABANG_2 for l in logs),
              f"HTTP {r.status_code}, cabang={sorted({l.get('branch_id') for l in logs})}")

    print("\n -- 4D. Identitas kunci (/auth/me) --")
    r = requests.get(f"{API_URL}/auth/me", headers=headers_for(CABANG_1), timeout=TIMEOUT)
    me = r.json() if r.ok else {}
    rep.check("branch-a /auth/me -> branch_id=branch-a, is_admin=false",
              r.status_code == 200 and me.get("branch_id") == CABANG_1
              and me.get("is_admin") is False,
              f"HTTP {r.status_code} {r.text[:120]}")

    r = requests.get(f"{API_URL}/auth/me", headers=admin_headers(), timeout=TIMEOUT)
    me = r.json() if r.ok else {}
    rep.check("admin /auth/me -> is_admin=true",
              r.status_code == 200 and me.get("is_admin") is True,
              f"HTTP {r.status_code} {r.text[:120]}")


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="Pengujian otomatis Inventaris Multi-Cabang Nuansa Wangi")
    parser.add_argument("scenario", nargs="?", default="all", choices=["1", "2", "3", "4", "all"])
    args = parser.parse_args()

    print(f"Target server: {API_URL}")
    if not wait_for_server():
        print(f"[ERROR] Server tidak merespons di {API_URL}. Pastikan 'docker compose up' sudah jalan.")
        return 2

    rep = Report()
    runners = {"1": scenario_1, "2": scenario_2, "3": scenario_3, "4": scenario_4}
    selected = ["1", "2", "3", "4"] if args.scenario == "all" else [args.scenario]
    started = time.time()
    try:
        for key in selected:
            runners[key](rep)
    finally:
        # kembalikan stok ke angka awal demo (best effort)
        for sku, qty in SEED_STOCK.items():
            try:
                set_stock(sku, qty)
            except Exception:  # noqa: BLE001
                pass

    header("RINGKASAN")
    print(f"   Lulus : {rep.passed}")
    print(f"   Gagal : {rep.failed}")
    print(f"   Waktu : {time.time() - started:.1f} detik")
    print("   HASIL : " + ("SEMUA SKENARIO LULUS" if rep.failed == 0 else "ADA YANG GAGAL"))
    return 0 if rep.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

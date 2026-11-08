# Sistem Inventaris Multi-Cabang Nuansa Wangi (Client-Server)

Project Sistem Terdistribusi: stok **Bibit Parfum (ml)** dan **Botol Parfum (pcs)** untuk 3 cabang
(Empang, Cilendek, Cibinong) disimpan di **satu database terpusat** dan diakses lewat
**satu API Server (FastAPI)**. Konkurensi ditangani dengan **Pessimistic Locking** (`SELECT ... FOR UPDATE`).

Fitur operasional: **API key per cabang**, **dashboard web**, **rate limiting**, **HTTPS lokal**,
**backup/restore JSON**, dan **metrik Prometheus**. Panduan lengkapnya ada di bagian 11-14.

Database bisa memakai salah satu backend (dipilih lewat `DB_BACKEND`):

| `DB_BACKEND` | Database | Dipakai untuk |
|---|---|---|
| `postgres` (default) | Supabase / PostgreSQL | setup utama |
| `mysql` | MySQL 8.0+ (Laragon / XAMPP) | backup / development lokal tanpa internet |

## 1. Arsitektur

```
 branch-a  Cabang 1 / Empang    172.28.0.11 --+
 branch-b  Cabang 2 / Cilendek  172.28.0.12 --+-- HTTP REST (JSON) --> api (FastAPI + Locking) -- TCP/SQL --> Supabase
 branch-c  Cabang 3 / Cibinong  172.28.0.13 --+                        172.28.0.2 : 8000                     (PostgreSQL)
 |------------- Docker network jmk48-net (172.28.0.0/24) ------------------------|                      (di luar Docker)
```

| Komponen | Peran |
|---|---|
| `api` (server pusat) | Koordinator konkurensi: validasi, transaksi atomik, row lock, idempotensi |
| `branch-a/b/c` | Node klien cabang (CLI `branch_client.py`), hanya bicara ke `api`, tidak saling bicara |
| Supabase PostgreSQL | Penyimpan data + penjaga integritas terakhir (`CHECK (qty >= 0)`) |

## 2. Struktur Folder

```
nuansa-wangi-inventory/
├── docker-compose.yml        # 1 server + 3 node cabang
├── .env.example              # salin jadi .env, pilih DB_BACKEND + kredensial + kunci API
├── database/
│   ├── schema.sql            # PostgreSQL: tabel + constraint + data awal + hash API key
│   ├── reset_demo.sql        # PostgreSQL: reset stok & riwayat ke kondisi awal
│   ├── schema.mysql.sql      # MySQL 8.0+: versi portabel + migrasi api_key_hash
│   └── reset_demo.mysql.sql  # MySQL 8.0+: reset stok & riwayat
├── server/                   # Central Inventory Server (FastAPI)
│   ├── Dockerfile
│   ├── requirements.txt      # dependensi + psycopg (PostgreSQL)
│   ├── requirements-mysql.txt # dependensi + PyMySQL/DBUtils (MySQL)
│   ├── requirements-https.txt # (opsional) cryptography untuk membuat sertifikat
│   ├── static/               # dashboard web: HTML + CSS + JS tanpa build step
│   └── app/
│       ├── main.py           # endpoint, validasi, auth, rate limit, metrics
│       ├── auth.py           # API key per cabang + admin (SHA-256, tanpa kunci mentah)
│       ├── limiter.py        # rate limiter token bucket in-process
│       ├── metrics.py        # metrik Prometheus ringan
│       ├── db.py             # facade: pilih backend dari DB_BACKEND
│       ├── db_postgres.py    # implementasi PostgreSQL (psycopg)
│       ├── db_mysql.py       # implementasi MySQL (PyMySQL + DBUtils)
│       └── errors.py         # exception domain bersama
├── scripts/
│   ├── manage_keys.py        # list / rotate / set / disable / verify API key
│   ├── backup.py             # backup database -> backups/*.json
│   ├── restore.py            # restore snapshot JSON
│   ├── make_cert.py          # buat sertifikat HTTPS self-signed
│   ├── run_https.ps1         # jalankan API dengan HTTPS
│   ├── backup.ps1            # wrapper backup + log
│   └── watch_health.ps1      # pemantauan kesehatan API
├── monitoring/
│   └── prometheus.yml        # contoh scrape config Prometheus
└── client/                   # Node cabang + pengujian
    ├── Dockerfile
    ├── requirements.txt
    ├── branch_client.py      # CLI cabang (mengirim X-API-Key)
    └── test_scenarios.py     # pengujian otomatis 54 pemeriksaan
```

## 3. Prasyarat

- Docker Desktop / Docker Engine + **Docker Compose v2** (`docker compose version`)
- Akun Supabase (free tier cukup) dengan 1 project
- **Alternatif MySQL:** Laragon / XAMPP dengan MySQL 8.0+ (tanpa Docker, tanpa internet)
- (Opsional, untuk test dari host) Python 3.10+ dan `pip install requests`

## 4. Instalasi & Menjalankan

### Langkah 1 - Siapkan database di Supabase
1. Buka project Supabase -> **SQL Editor** -> **New query**.
2. Tempel seluruh isi `database/schema.sql` -> **Run**.
3. Cek di **Table Editor**: harus ada tabel `branches`, `inventory_items`, `stock_transactions`.
   Kolom `branches.api_key_hash` harus terisi untuk 3 cabang demo.
   Skema aman dijalankan ulang dan tidak menimpa kunci yang sudah dirotasi.

### Langkah 2 - Ambil connection string
1. Di dashboard Supabase klik tombol **Connect** (bagian atas).
2. Pilih **Session pooler** lalu salin URI-nya (port **5432**).
   - Pakai **Session pooler**, bukan *Direct connection*: koneksi direct hanya IPv6 kecuali kamu beli add-on IPv4,
     sehingga sering gagal dari Docker/jaringan rumah yang IPv4-only. Shared pooler mendukung IPv4.
3. Ganti `[YOUR-PASSWORD]` dengan password database. Kalau lupa: **Project Settings -> Database -> Reset database password**
   (pakai huruf+angka saja supaya tidak perlu URL-encode).

### Langkah 3 - Buat file `.env`
```bash
# Linux / macOS / Git Bash
cp .env.example .env
# Windows PowerShell / CMD
copy .env.example .env
```
Edit `.env`, isi `DATABASE_URL` dengan string dari langkah 2 (tambahkan `?sslmode=require` di ujungnya jika belum ada).

### Langkah 4 - Build & jalankan semua container
```bash
docker compose up --build -d
docker compose ps
```
Urutan start: `api` (menunggu koneksi Supabase + healthcheck `/health`) -> baru `branch-a/b/c`.
Semua container harus berstatus `running` / `healthy`.

### Langkah 5 - Verifikasi
```bash
curl http://localhost:8000/health        # {"status":"ok","database":"connected"}
curl -H "X-API-Key: nw-demo-branch-a-7f3a9c1e" http://localhost:8000/items
docker compose logs branch-a             # [Cabang Empang | branch-a] -> http://api:8000/health : HTTP 200 ...
```
- Swagger UI: **http://localhost:8000/docs** (klik **Authorize**, isi API key).
- Dashboard web: **http://localhost:8000/dashboard** (masukkan kunci cabang atau admin).
- Metrik: **http://localhost:8000/metrics**.

### Langkah 6 - Matikan
```bash
docker compose down        # stop & hapus container (data tetap aman di Supabase)
```

### Alternatif backend: MySQL 8.0 / Laragon (tanpa Supabase)

Cocok untuk backup/development lokal di laptop lain, misalnya saat tidak ada internet.
Semua fitur (row lock, idempotensi, audit log, 54 pengujian) tetap sama; hanya driver
dan sedikit detail SQL yang berbeda.

**Prasyarat:** Laragon dengan **MySQL 8.0+** (8.0.30 sudah diuji) dan Python 3.10+.

**Langkah 1 - Nyalakan MySQL**
Buka Laragon -> **Start All**. Pastikan port `3306` tidak dipakai aplikasi lain.

**Langkah 2 - Buat database + tabel**
- Cara GUI: buka **phpMyAdmin** (`http://localhost/phpmyadmin`) -> tab **Import** ->
  pilih `database/schema.mysql.sql` -> **Go**.
- Cara CLI:
  ```bash
  mysql -u root < database/schema.mysql.sql
  ```
File itu membuat database `nuansa_wangi`, 3 tabel, constraint, index idempotensi,
dan data awal (3 cabang + 4 SKU). Aman dijalankan berulang (`IF NOT EXISTS` + `INSERT IGNORE`).

**Langkah 3 - Arahkan aplikasi ke MySQL**
Salin `.env.example` menjadi `.env`, lalu set:
```dotenv
DB_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=nuansa_wangi
```
`DATABASE_URL` boleh dibiarkan (diabaikan saat `DB_BACKEND=mysql`). Default Laragon:
user `root`, password kosong.

**Langkah 4 - Install dependensi**
```bash
# kalau HANYA memakai MySQL (tanpa psycopg):
pip install -r server/requirements-mysql.txt
# kalau ingin dua-duanya (PostgreSQL + MySQL):
pip install -r server/requirements.txt -r server/requirements-mysql.txt
```

**Langkah 5 - Jalankan server**
```bash
cd server
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```
File `.env` di root project dibaca otomatis lewat `python-dotenv`, jadi tidak perlu
mengatur environment variable secara manual.
Cek: `curl http://localhost:8000/health` harus membalas
`{"status":"ok","database":"connected","backend":"mysql"}`.

**Langkah 6 - Jalankan pengujian**
```bash
python client/test_scenarios.py
```
Target hasil: `Lulus : 54`, `Gagal : 0`. Setelah selesai, stok dikembalikan ke angka awal.

**Langkah 7 - Reset data demo (opsional)**
Jalankan `database/reset_demo.mysql.sql` lewat phpMyAdmin/CLI:
```bash
mysql -u root nuansa_wangi < database/reset_demo.mysql.sql
```

**Catatan teknis MySQL**

| Hal | PostgreSQL | MySQL (implementasi ini) |
|---|---|---|
| Idempotensi | partial unique index `WHERE status='SUCCESS'` | generated column `request_id_success` + `UNIQUE` index |
| `UPDATE ... RETURNING` | didukung | `UPDATE` lalu `SELECT` ulang |
| `INSERT ... RETURNING id` | didukung | `cursor.lastrowid` |
| Isolation | default `READ COMMITTED` | dipaksa `READ COMMITTED` per sesi (default MySQL `REPEATABLE READ`) |
| Lock timeout | `set_config('lock_timeout')` | `innodb_lock_wait_timeout` |
| Error driver | `LockNotAvailable`, `UniqueViolation`, ... | errno `1205/1213`, `1062`, `3819`, `1452` |
| RLS | aktif (menutup REST Supabase) | tidak ada; jangan buka port MySQL ke internet |

- `READ COMMITTED` wajib: dengan `REPEATABLE READ`, pengecekan `request_id` bisa membaca snapshot lama
  sehingga request kedua lolos dan menabrak unique index.
- Docker Compose pada repo ini tetap menunjuk PostgreSQL. Untuk MySQL, jalankan API langsung
  dari host (Laragon) seperti langkah di atas; container tidak bisa menjangkau MySQL di
  `127.0.0.1` laptop tanpa konfigurasi tambahan (`host.docker.internal`).
- Untuk pemakaian jangka panjang, buat user khusus aplikasi (bukan `root`) dan batasi grant-nya:
  ```sql
  CREATE USER 'nuansa'@'localhost' IDENTIFIED BY 'password_kuat';
  GRANT SELECT, INSERT, UPDATE ON nuansa_wangi.* TO 'nuansa'@'localhost';
  ```
- Kalau MySQL memakai `caching_sha2_password` dan login gagal, tambahkan paket `cryptography`
  atau buat user dengan `IDENTIFIED WITH mysql_native_password BY '...'`.


## 5. Menjalankan Pengujian Otomatis

**Cara A - dari dalam container cabang** (memakai jaringan Docker sungguhan, `API_URL=http://api:8000`):
```bash
docker compose exec branch-a python test_scenarios.py        # semua skenario
docker compose exec branch-a python test_scenarios.py 2      # hanya skenario 2
```

**Cara B - dari host:**
```bash
pip install requests
python client/test_scenarios.py          # target default http://localhost:8000
# atau: API_URL=http://localhost:8000 python client/test_scenarios.py 3
```
Test memakai kunci demo yang sama dengan seed database. Untuk database yang kuncinya
sudah dirotasi, set environment variable:
```bash
set BRANCH_A_API_KEY=nw-...        # Windows CMD
set BRANCH_B_API_KEY=nw-...
set BRANCH_C_API_KEY=nw-...
set ADMIN_API_KEY=nw-...
```
(PowerShell: `$env:BRANCH_A_API_KEY="nw-..."`, dan seterusnya.)

Isi skenario:

| # | Skenario | Yang diverifikasi |
|---|---|---|
| 1 | Update **berurutan** Cabang 1 & Cabang 2 | Stok akhir benar, `/logs` mencatat urut, rantai `qty_before -> qty_after` nyambung |
| 2A | **Paralel** (threading + `Barrier`): stok 100 ml, Cabang 1 & 2 serentak minta 100 ml | Tepat 1 sukses (200), 1 ditolak (409), stok akhir 0, **tidak minus** |
| 2B | **Paralel berat**: 12 request serentak, stok 50 pcs, @10 pcs | Tepat 5 sukses + 7 ditolak, transaksi terserialisasi 50->40->...->0 |
| 3A | **Request tidak valid**: qty 0/negatif/string/desimal, action salah, field hilang, JSON rusak, cabang/SKU tidak ada, stok kurang | HTTP 422 / 403 / 404 / 409 yang tepat, **stok tidak berubah** |
| 3B | **Koneksi gagal** + retry | Server mati terdeteksi tanpa crash; retry `request_id` sama tidak memotong stok dua kali (idempotent); `request_id` sama tapi isi beda -> 409 |
| 4 | **Keamanan API key** | Tanpa kunci / kunci palsu -> 401; kunci cabang untuk cabang lain -> 403; `/logs` otomatis dibatasi ke cabangnya; endpoint admin menolak kunci cabang |

Hasil yang diharapkan di akhir: `Lulus : 54`, `Gagal : 0`, `HASIL : SEMUA SKENARIO LULUS` (exit code 0).
Test mengembalikan stok ke angka awal setelah selesai; riwayat `[TEST]` tetap tersimpan di `/logs`
(hapus dengan `database/reset_demo.sql` untuk PostgreSQL atau `database/reset_demo.mysql.sql` untuk MySQL).

**Bonus - demo paralel dari dua container berbeda** (bash / Git Bash / WSL):
```bash
docker compose exec -T branch-a python branch_client.py update --sku BIBIT-BACCARAT --action SUBTRACT --qty 100 &
docker compose exec -T branch-b python branch_client.py update --sku BIBIT-BACCARAT --action SUBTRACT --qty 100 &
wait
docker compose exec branch-c python branch_client.py logs --limit 5
```
(Jeda start `docker exec` bisa membuat request tidak benar-benar bersamaan; untuk pembuktian yang konsisten pakai skenario 2.)

## 6. Memakai Node Cabang (CLI)

Client membaca `BRANCH_ID`, `API_URL`, dan `BRANCH_API_KEY` dari environment
(di Docker sudah diisi otomatis oleh `docker-compose.yml`). Semua perintah di
bawah mengirim header `X-API-Key`; tanpa kunci yang valid server menjawab 401.

```bash
docker compose exec branch-a python branch_client.py items
docker compose exec branch-a python branch_client.py update --sku BIBIT-BACCARAT --action SUBTRACT --qty 10 --note "pesanan #12"
docker compose exec branch-b python branch_client.py update --sku BOTOL-KACA-30 --action ADD --qty 20
docker compose exec branch-c python branch_client.py logs --limit 10
docker compose exec branch-c python branch_client.py logs --mine
```

`logs` tanpa `--mine` untuk kunci cabang tetap otomatis dibatasi ke cabang itu;
hanya kunci admin yang boleh melihat semua cabang.

## 7. Referensi API

Semua endpoint data memakai header:

```
X-API-Key: <kunci cabang atau kunci admin>
```

| Method & Path | Akses | Fungsi |
|---|---|---|
| `GET /health` | publik | Cek server + koneksi database |
| `GET /auth/me` | semua kunci | Info identitas kunci (`branch_id`, `is_admin`) |
| `GET /items` | semua kunci | Stok semua barang (`?sku=` untuk filter) |
| `GET /items/{sku}` | semua kunci | Stok satu barang |
| `POST /items/update` | kunci cabang (cabangnya sendiri) / admin | Tambah / kurangi stok (transaksi atomik + row lock) |
| `GET /logs` | semua kunci | Riwayat transaksi. Filter: `branch_id`, `sku`, `status`, `limit` (1-500) |
| `GET /branches` | admin | Daftar cabang + status ada/tidaknya kunci (hash tidak pernah dikirim) |
| `GET /summary` | admin | Ringkasan jumlah barang, total stok, transaksi sukses/ditolak |
| `GET /metrics` | publik (agregat) | Metrik Prometheus, tanpa stok per SKU |
| `GET /dashboard` | publik (halaman statis) | Dashboard web; login memakai API key |
| `GET /docs` | publik | Swagger UI |

Aturan akses:
- Kunci cabang **hanya** boleh `POST /items/update` untuk `branch_id` miliknya dan
  membaca `/logs` cabangnya sendiri. Pelanggaran -> `403 BRANCH_MISMATCH`.
- Kunci admin (dari `ADMIN_API_KEY`) boleh semua cabang dan endpoint admin.
- Kalau `AUTH_REQUIRED=false`, semua kunci diperlakukan sebagai admin (hanya untuk demo lokal).

**`POST /items/update`**
```json
{
  "branch_id": "branch-a",
  "sku": "BIBIT-BACCARAT",
  "action": "SUBTRACT",
  "quantity": 10,
  "request_id": "branch-a-000123",
  "note": "pesanan #12"
}
```
`action` = `ADD` | `SUBTRACT`; `quantity` bilangan bulat > 0; `request_id` (opsional) = idempotency key; `note` opsional.

Kode respons:

| HTTP | `detail.code` | Arti |
|---|---|---|
| 200 | - | Sukses (`idempotent_replay: true` jika request_id yang sama dikirim ulang) |
| 401 | `API_KEY_REQUIRED` | Header `X-API-Key` tidak dikirim |
| 401 | `API_KEY_INVALID` | Kunci tidak dikenal atau cabang dinonaktifkan |
| 403 | `ADMIN_REQUIRED` | Endpoint admin dipanggil dengan kunci cabang |
| 403 | `BRANCH_MISMATCH` | Kunci cabang dipakai untuk cabang lain |
| 403 | `BRANCH_NOT_REGISTERED` | Cabang tidak terdaftar |
| 404 | `ITEM_NOT_FOUND` | SKU tidak ada |
| 409 | `INSUFFICIENT_STOCK` | Stok tidak cukup (transaksi di-ROLLBACK, dicatat sebagai `REJECTED`) |
| 409 | `REQUEST_ID_MISMATCH` | `request_id` sudah dipakai untuk transaksi lain |
| 409 | `REQUEST_ID_CONFLICT` | `request_id` yang sama sedang diproses bersamaan |
| 422 | - | Payload tidak valid (validasi Pydantic) |
| 429 | `RATE_LIMITED` | Terlalu banyak request; cek header `Retry-After` |
| 503 | `LOCK_TIMEOUT` / `DB_UNAVAILABLE` | Lock terlalu lama / database tidak terjangkau - aman dicoba ulang |

## 8. Cara Kerja Pengendalian Konkurensi

Alur satu transaksi di `server/app/db_postgres.py` atau `server/app/db_mysql.py`
(keduanya mengekspos `apply_stock_update` yang sama):

```
BEGIN
  SET LOCAL lock_timeout = 5s
  cek cabang terdaftar
  SELECT ... FROM inventory_items WHERE sku = :sku FOR UPDATE   <- Pessimistic Lock (row-level)
  cek idempotensi (request_id sudah sukses?) -> kembalikan hasil lama
  stok + delta < 0 ?  -> ROLLBACK + catat REJECTED + HTTP 409
  UPDATE inventory_items SET qty = qty + :delta
  INSERT INTO stock_transactions (...)
COMMIT                                                           <- lock dilepas
```

- **Race condition**: dua cabang yang menyentuh SKU yang sama diserialkan di baris `FOR UPDATE`. Transaksi kedua menunggu,
  lalu membaca stok **terbaru** (isolation level default `READ COMMITTED`), bukan stok lama.
- **Data stale**: tidak ada cache di klien/server; pengecekan stok selalu dilakukan pada baris yang sedang dikunci.
- **Atomicity (ACID)**: update stok + riwayat satu transaksi; gagal = ROLLBACK seluruhnya.
- **Dua lapis pengaman**: (1) validasi + lock di API Server, (2) `CHECK (qty >= 0)` di database.
- **Idempotensi**: unique index parsial pada `request_id` untuk transaksi `SUCCESS` -> aman untuk retry setelah koneksi putus.
- Locking terjadi di database, jadi tetap benar walaupun server dijalankan dengan banyak worker/replika.

## 9. Troubleshooting

| Gejala | Penyebab / solusi |
|---|---|
| `docker compose` bilang `env file .env not found` | Belum `cp .env.example .env` (Langkah 3) |
| Log `api`: `Network is unreachable` / `could not translate host name` | Kamu memakai *Direct connection* (IPv6). Ganti ke **Session pooler** |
| Log `api`: `password authentication failed` / `Tenant or user not found` | Password salah, atau username pooler harus `postgres.<PROJECT-REF>` (bukan `postgres`) |
| Log `api`: `relation "branches" does not exist` | `database/schema.sql` belum dijalankan di Supabase |
| `branch-*` tidak start / `api` unhealthy | `docker compose logs api` - biasanya masalah `DATABASE_URL` |
| `Pool overlaps with other one on this address space` | Subnet 172.28.0.0/24 bentrok. `docker network prune` atau ganti subnet + IP di `docker-compose.yml` |
| `port is already allocated` (8000) | Ubah mapping port `api` jadi mis. `"8001:8000"` lalu pakai `API_URL=http://localhost:8001` |
| Respons `503 LOCK_TIMEOUT` | Baris stok dikunci terlalu lama; naikkan `LOCK_TIMEOUT_MS` di `.env` |
| Password mengandung `@ # $ /` | Reset password jadi huruf+angka, atau URL-encode di `DATABASE_URL` |
| MySQL: `Can't connect to MySQL server` | Laragon belum **Start All**, atau port `3306` bentrok |
| MySQL: `Unknown database 'nuansa_wangi'` | `database/schema.mysql.sql` belum dijalankan |
| MySQL: `1045 Access denied` | `MYSQL_USER`/`MYSQL_PASSWORD` salah (Laragon default: `root`, kosong) |
| MySQL: `503 DB_UNAVAILABLE` sesaat setelah start | MySQL belum siap; tunggu sampai Laragon hijau, lalu ulangi |
| MySQL: `caching_sha2_password` | `pip install cryptography`, atau buat user dengan `mysql_native_password` |
| `401 API_KEY_REQUIRED` | Request data belum mengirim header `X-API-Key` |
| `401 API_KEY_INVALID` | Kunci salah, sudah dirotasi, atau cabang dinonaktifkan |
| `403 BRANCH_MISMATCH` | Kunci cabang dipakai untuk `branch_id`/log cabang lain |
| `403 ADMIN_REQUIRED` | Endpoint admin dipanggil dengan kunci cabang, bukan `ADMIN_API_KEY` |
| `429 RATE_LIMITED` | Tunggu sesuai `Retry-After`, atau naikkan `RATE_LIMIT_PER_MINUTE` / `RATE_LIMIT_BURST` |
| Browser memperingatkan sertifikat HTTPS | Wajar untuk self-signed; klik Advanced -> Proceed, atau pakai reverse proxy TLS |
| `ModuleNotFoundError: cryptography` | `pip install -r server/requirements-https.txt` (hanya untuk membuat sertifikat) |

Memakai **Transaction pooler** (port 6543) juga didukung; kode sudah mematikan prepared statement untuk itu.

## 10. Catatan untuk Laporan / Diagram

- Di PDF proposal, diagram arsitektur masih memuat node *Database Server PostgreSQL (172.28.0.3)*. Pada implementasi ini
  database diganti **Supabase (managed PostgreSQL, di luar jaringan Docker)**, jadi tidak ada container database
  dan alamat `172.28.0.3` tidak dipakai. Perbarui diagram/tabel IP-nya agar sesuai.
- Protokol: Client -> Server = HTTP/1.1 REST JSON di atas TCP port 8000; Server -> Database = koneksi PostgreSQL (TLS) lewat pooler Supabase.
- Stok bertipe integer (ml/pcs bulat). Kalau perlu desimal, ubah kolom `qty` menjadi `NUMERIC(12,2)` dan `quantity` di skema Pydantic.
- Autentikasi per cabang **sudah diimplementasikan** (API key + hash SHA-256, lihat bagian 11);
  pengembangan lanjutan: JWT berumur pendek atau OAuth untuk integrasi pihak ketiga.

## 11. Keamanan: API Key per Cabang

Database **hanya menyimpan SHA-256 hex** dari kunci (`branches.api_key_hash`).
Kunci mentah tidak pernah ditulis ke database, log, atau respons API. Kalau
database bocor, kunci asli tidak langsung bisa dipakai.

| Cabang / peran | Kunci demo | Hak |
|---|---|---|
| branch-a (Empang) | `nw-demo-branch-a-7f3a9c1e` | transaksi + log cabang-a |
| branch-b (Cilendek) | `nw-demo-branch-b-2b8d4f6a` | transaksi + log cabang-b |
| branch-c (Cibinong) | `nw-demo-branch-c-9c5e7a1d` | transaksi + log cabang-c |
| admin | `nw-demo-admin-4d2f8b6c` | semua cabang + `/branches` + `/summary` |

> **Kunci di atas hanya untuk latihan/penilaian.** Ganti sebelum dipakai
> sungguhan. Kunci admin diatur lewat environment `ADMIN_API_KEY`, bukan tabel.

Kelola kunci (dari root project):

```bash
python scripts/manage_keys.py list
python scripts/manage_keys.py rotate --branch branch-a
python scripts/manage_keys.py set --branch branch-b --key "kunci-pilihan-sendiri-min-16"
python scripts/manage_keys.py disable --branch branch-c
python scripts/manage_keys.py verify --key "nw-demo-branch-a-7f3a9c1e"
```

`rotate` menampilkan kunci baru **sekali saja**. Setelah itu:

1. Salin kunci baru ke `BRANCH_API_KEY` cabang terkait (`.env` atau
   `docker-compose.yml`; untuk Docker Compose bisa lewat `BRANCH_A_API_KEY` dll).
2. Restart client/container cabang tersebut.
3. Kunci lama langsung tidak berlaku (tanpa perlu restart server API).

Konfigurasi terkait di `.env`:

```dotenv
AUTH_REQUIRED=true
ADMIN_API_KEY=nw-demo-admin-4d2f8b6c   # kosongkan untuk mematikan endpoint admin
```

- `AUTH_REQUIRED=true` (default) mewajibkan `X-API-Key` di semua endpoint data.
- `AUTH_REQUIRED=false` hanya untuk debugging lokal: semua request diperlakukan
  sebagai admin. **Jangan** dipakai saat demo yang dinilai/diuji orang lain.
- Skema database aman dijalankan ulang: migrasi menambah kolom/index hanya jika
  belum ada, dan seed hash hanya mengisi cabang yang `api_key_hash IS NULL`
  sehingga rotasi tidak tertimpa.

## 12. Dashboard Web

Buka **http://localhost:8000/dashboard** (atau `https://localhost:8443/dashboard`
kalau memakai HTTPS). Dashboard adalah HTML/CSS/JS biasa dari `server/static/`,
tanpa build step dan tanpa CDN.

- **Login**: tempel kunci cabang atau admin. Kunci disimpan di `sessionStorage`
  (hilang saat tab ditutup) dan dikirim sebagai header `X-API-Key`.
- **Tampilan cabang**: kartu stok, form tambah/kurangi stok, dan riwayat yang
  otomatis dibatasi ke cabangnya. Tidak ada tombol/filter lintas cabang.
- **Tampilan admin**: ringkasan agregat, filter cabang pada riwayat, dan pilihan
  cabang pada form transaksi.
- Auto-refresh tiap 10 detik (berhenti saat tab tidak aktif); ada tombol
  **Refresh** dan **Keluar**.
- Status server/backend ditampilkan di header; kalau koneksi putus muncul badge
  `terputus` dan toast error.

Dashboard memakai endpoint yang sama dengan CLI, jadi aturan otorisasi tetap
berlaku: kunci cabang tidak bisa dipakai untuk mengubah cabang lain.

## 13. HTTPS & Rate Limiting

### HTTPS lokal (self-signed)

```bash
pip install -r server/requirements-https.txt
python scripts/make_cert.py                        # SAN: localhost, 127.0.0.1, ::1
python scripts/make_cert.py --host 192.168.1.10    # tambah IP LAN untuk demo dari HP
powershell -ExecutionPolicy Bypass -File scripts/run_https.ps1
```

Server HTTPS berjalan di **https://localhost:8443**. Browser akan menampilkan
peringatan karena sertifikat self-signed; pilih **Advanced -> Proceed** untuk
demo lokal. Sertifikat ada di `certs/` dan **diabaikan git**.

Untuk produksi jangan pakai self-signed. Jalankan uvicorn di `127.0.0.1` dan
letakkan reverse proxy (Caddy/Nginx) di depannya untuk TLS Let's Encrypt:

```
Caddy:  inventory.contoh.com { reverse_proxy 127.0.0.1:8000 }
Nginx:  proxy_pass http://127.0.0.1:8000;  (plus certbot)
```

### Rate limiting

Rate limiter token bucket in-process (tanpa Redis):

```dotenv
RATE_LIMIT_PER_MINUTE=600
RATE_LIMIT_BURST=120
```

- Bucket dipisah per hash API key (atau IP kalau tanpa kunci).
- Melebihi limit -> `429 RATE_LIMITED` + header `Retry-After` dan
  `X-RateLimit-Remaining`.
- `/health`, `/dashboard`, `/static`, `/docs` tidak dihitung.
- Catatan: hitungan hanya akurat untuk **satu proses** uvicorn. Kalau memakai
  `--workers N` atau beberapa container API, ganti dengan rate limiter berbasis
  Redis (mis. `slowapi` + Redis) supaya kuota dibagi bersama.
- Semua respons API juga diberi header keamanan: `X-Content-Type-Options`,
  `X-Frame-Options`, `Referrer-Policy`.

## 14. Backup, Restore & Monitoring

### Backup / restore JSON

Snapshot logis dibuat lewat facade `db.py`, jadi **portabel antar backend**
(PostgreSQL -> MySQL atau sebaliknya), tanpa `pg_dump`/`mysqldump`.

```bash
python scripts/backup.py                  # -> backups/nuansa-wangi-<backend>-<waktu>.json
python scripts/backup.py --keep 30        # simpan 30 backup terbaru
python scripts/backup.py --out backups/manual.json

python scripts/restore.py --latest        # konfirmasi interaktif
python scripts/restore.py --file backups/manual.json --yes
```

Snapshot berisi `branches` (termasuk hash kunci), `inventory_items`, dan
`stock_transactions` dengan **id eksplisit**. Setelah restore, sequence
PostgreSQL / `AUTO_INCREMENT` MySQL diperbaiki otomatis.

**Restore bersifat mengganti total** isi ketiga tabel. Selalu backup dulu.
Untuk jadwal otomatis di Windows, pakai Task Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File C:\path\ke\project\scripts\backup.ps1 -Keep 30
```

Wrapper `backup.ps1` menulis log ke `logs/backup.log`. Folder `backups/`,
`logs/`, dan `certs/` sudah masuk `.gitignore`.

### Monitoring

- `GET /metrics` menyajikan metrik format Prometheus:
  `nw_http_requests_total`, `nw_http_request_duration_seconds`,
  `nw_auth_failures_total`, `nw_rate_limited_total`,
  `nw_stock_updates_total`, `nw_items_count`, `nw_stock_total_qty`,
  `nw_uptime_seconds`, `nw_backend_info`.
- Metrik hanya **agregat** - tidak ada stok/nama per SKU yang bocor.
- Scrape config contoh ada di `monitoring/prometheus.yml`:

```bash
docker run --rm -p 9090:9090 ^
  -v %cd%/monitoring/prometheus.yml:/etc/prometheus/prometheus.yml ^
  prom/prometheus
```

Buka **http://localhost:9090** dan query mis. `rate(nw_http_requests_total[5m])`
atau `nw_stock_total_qty`.

- Pemeriksaan kesehatan sederhana tanpa Prometheus:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/watch_health.ps1
powershell -ExecutionPolicy Bypass -File scripts/watch_health.ps1 -Url https://localhost:8443/health -IntervalSeconds 10 -MaxFailures 3
```

Log ada di `logs/health.log`; setelah `MaxFailures` kegagalan berturut-turut
muncul baris `ALERT` (atau keluar dengan kode 1 kalau memakai `-ExitOnFailure`,
cocok untuk Task Scheduler/alerting).

## 15. Checklist Sebelum Dipakai Sungguhan

- [ ] Rotasi **semua** kunci demo dan `ADMIN_API_KEY`, lalu update `.env` +
      `BRANCH_API_KEY` tiap cabang dan restart client.
- [ ] Pastikan `AUTH_REQUIRED=true` dan `ADMIN_API_KEY` kuat (mis. 32+ karakter acak).
- [ ] Jangan commit `.env`; `certs/`, `backups/`, `logs/` sudah di-`.gitignore`.
- [ ] MySQL: buat user aplikasi (bukan `root`) dengan grant minimal; jangan buka
      port 3306 ke internet.
- [ ] PostgreSQL/Supabase: RLS tetap aktif; jangan bagikan service key ke client.
- [ ] Pasang HTTPS lewat reverse proxy + sertifikat tepercaya; jangan expose
      uvicorn HTTP langsung ke internet.
- [ ] Batasi akses `/metrics` dan `/docs` di reverse proxy kalau tidak perlu publik.
- [ ] Jadwalkan backup otomatis dan **uji restore** minimal sekali.
- [ ] Pasang Prometheus/health watcher + notifikasi untuk kegagalan API/database.
- [ ] Kalau API dijalankan multi-worker/multi-replika, pindahkan rate limiter ke Redis.

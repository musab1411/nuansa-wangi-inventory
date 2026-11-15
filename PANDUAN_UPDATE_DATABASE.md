# Panduan Update Database MySQL - Nuansa Wangi

Panduan ini untuk **teman/kolega yang sudah punya database `nuansa_wangi` versi lama**
(sebelum ada fitur API key per cabang) dan ingin menjalankan versi terbaru aplikasi.

Kalau database-nya **baru dari nol**, dia tetap ikut panduan ini — hasilnya sama.

> **Inti yang berubah:** tabel `branches` dapat satu kolom baru, `api_key_hash`,
> plus satu unique index. Data stok dan riwayat transaksi **tidak dihapus**.
> Script migrasinya aman dijalankan berulang.

> ### ⚠️ Ini panduan MySQL / Laragon — bukan Supabase
>
> Seluruh langkah 1–7, 9–12 memakai **MySQL 8.0 di Laragon** (phpMyAdmin, HeidiSQL,
> `mysqldump`, `schema.mysql.sql`). Supabase/PostgreSQL **tidak dipakai sama sekali**
> di alur utama. Bagian PostgreSQL hanya ada di **Lampiran di paling bawah** dan
> **boleh dilewati**.
>
> **Kalau temanmu merasa aplikasinya "jalan di Supabase", itu bukan dari panduan ini.**
> Penyebabnya hampir selalu file `.env`: kalau di dalamnya ada `DATABASE_URL` dan
> `DB_BACKEND` tidak diisi, aplikasi otomatis memilih PostgreSQL (lihat 0.1).

**Daftar isi:**

0. [Ringkasan 1 menit](#0-ringkasan-dalam-1-menit) · [0.1 Aplikasi masih nyambung Supabase?](#01-kalau-aplikasi-masih-nyambung-ke-supabase)
1. [Prasyarat](#1-prasyarat)
2. [Backup dulu](#2-wajib-backup-dulu)
3. [Jalankan migrasi](#3-jalankan-migrasi-databaseschemamysqlsql)
4. [Apa yang dilakukan script](#4-apa-yang-sebenarnya-dilakukan-script-itu)
5. [Verifikasi](#5-verifikasi-setelah-migrasi)
6. [Konfigurasi aplikasi](#6-setelah-migrasi-konfigurasi-aplikasi)
7. [Ganti kunci demo](#7-disarankan-ganti-kunci-demo)
8. [Lampiran PostgreSQL (opsional)](#8-lampiran-opsional--kalau-database-nya-postgresql--supabase)
9. [Troubleshooting](#9-troubleshooting)
10. [Rollback](#10-rollback-kalau-benar-benar-perlu)
11. [Checklist akhir](#11-checklist-akhir)
12. [FAQ](#12-faq-singkat)

---

## 0. Ringkasan dalam 1 menit

```text
0. Buang .env dari komputer lain -> DB_BACKEND=mysql (lihat 0.1)
1. Cek MySQL 8.0+            -> SELECT VERSION();
2. BACKUP dulu               -> mysqldump / export phpMyAdmin
3. Jalankan schema.mysql.sql -> phpMyAdmin Import / mysql CLI
4. Verifikasi                -> kolom api_key_hash ada + 3 cabang punya kunci
5. Set .env                  -> DB_BACKEND=mysql + ADMIN_API_KEY
6. Jalankan server + tes     -> target 54/54 lulus
```

---

## 0.1 Kalau aplikasi masih nyambung ke Supabase

Kalau `http://localhost:8000/health` menjawab `"backend":"postgres"` padahal
kamu sudah menyiapkan MySQL, hampir pasti karena **`.env` bawaan komputer lain**
ikut terbawa (misalnya dari file ZIP). Di dalamnya ada `DATABASE_URL`, dan
aplikasi memilih backend dengan aturan ini (`server/app/db.py`):

```python
DB_BACKEND = os.getenv("DB_BACKEND", "").strip().lower()
if not DB_BACKEND:
    DB_BACKEND = "postgres" if os.getenv("DATABASE_URL") else "mysql"
```

Artinya: **`DB_BACKEND` kosong + ada `DATABASE_URL` = PostgreSQL/Supabase.**
Migrasi MySQL yang sudah dijalankan jadi tidak terpakai, karena aplikasi membaca
database lain.

### Cara memastikan dan memperbaiki

1. Buka `.env` di root project.
2. Pastikan ada baris ini (tambahkan kalau belum ada):

   ```dotenv
   DB_BACKEND=mysql
   ```

3. `DATABASE_URL` **boleh dibiarkan** — akan diabaikan selama `DB_BACKEND=mysql`.
   Kalau ingin benar-benar bersih, hapus saja baris `DATABASE_URL` atau beri
   tanda komentar (`#`).
4. **Restart API** (Ctrl+C lalu jalankan ulang), karena backend dipilih saat start.
5. Cek ulang:

   ```bash
   curl http://localhost:8000/health
   ```

   Harus muncul:

   ```json
   {"status":"ok","database":"connected","backend":"mysql","version":"1.1.0",...}
   ```

6. Buka dashboard, login admin, lalu lihat stok/riwayat. Kalau angkanya sama
   dengan hasil migrasi MySQL, berarti sudah benar.

> Kalau `backend` sudah `mysql` tetapi data terasa aneh, cek database mana yang
> dibaca: `MYSQL_DATABASE` di `.env` harus `nuansa_wangi`, dan MySQL yang hidup
> harus yang di `MYSQL_PORT` (default `3306`).

---

## 1. Prasyarat

| Kebutuhan | Keterangan |
|---|---|
| **MySQL 8.0 atau lebih baru** | Wajib. Versi 5.7 **tidak bisa** (skema memakai `CHECK` constraint + generated column). Versi 8.0.30 sudah diuji. |
| MySQL client / phpMyAdmin / HeidiSQL | Salah satu saja. Laragon sudah menyediakan semuanya. |
| Python 3.10+ | Untuk menjalankan API. |
| Akses user yang boleh `ALTER TABLE` | Default Laragon: user `root`, password kosong. |

### Cek versi MySQL

**Lewat phpMyAdmin:** buka `http://localhost/phpmyadmin` -> tab **SQL** -> jalankan:

```sql
SELECT VERSION();
```

**Lewat CLI:**

```bat
mysql -u root -e "SELECT VERSION();"
```

Contoh output yang benar: `8.0.30`. Kalau `5.7.x` atau `5.6.x`, **stop** — minta dia
upgrade MySQL dulu (Laragon versi baru sudah membawa MySQL 8).

### Cek database-nya sudah ada

```sql
SHOW DATABASES LIKE 'nuansa_wangi';
```

- Ada hasilnya -> database lama, lanjut ke langkah 2 (backup).
- Kosong -> database belum ada; script tetap bisa membuatnya, tapi tetap backup
  kalau ada database lain yang bernama mirip.

---

## 2. WAJIB: Backup dulu

Walaupun migrasinya aditif (cuma menambah kolom + index), backup itu murah dan
menghindari penyesalan. Pilih **salah satu** cara.

### Cara A - `mysqldump` (paling praktis, CLI)

Laragon (sesuaikan versi foldernya):

```bat
cd C:\laragon\bin\mysql\mysql-8.0.30-winx64\bin
mysqldump.exe -u root nuansa_wangi > C:\backup-nuansa-sebelum-update.sql
```

Tanpa password (default Laragon). Kalau user `root` punya password:

```bat
mysqldump.exe -u root -p nuansa_wangi > C:\backup-nuansa-sebelum-update.sql
```

Cek file hasilnya ada dan ukurannya wajar (bukan 0 byte).

### Cara B - Export lewat phpMyAdmin

1. Buka `http://localhost/phpmyadmin`.
2. Klik database **`nuansa_wangi`** di sidebar kiri.
3. Tab **Export** -> metode **Quick** -> format **SQL** -> klik **Export**.
4. File `.sql` otomatis terunduh.

### Cara C - Script bawaan project (butuh `.env` sudah benar)

```bash
python scripts/backup.py
```

Hasilnya di folder `backups/*.json`. Cara ini juga bisa dipakai untuk restore
lintas backend (MySQL <-> PostgreSQL), tapi untuk sekadar backup sebelum migrasi,
Cara A/B lebih sederhana.

### Cara restore kalau ada apa-apa

```bat
mysql -u root nuansa_wangi < C:\backup-nuansa-sebelum-update.sql
```

---

## 3. Jalankan migrasi `database/schema.mysql.sql`

File ini ada di root project: `database/schema.mysql.sql`.
**Jalankan seluruh isinya, sekali saja.** Aman kalau tidak sengaja dijalankan dua kali.

### Cara A - phpMyAdmin (paling ramah untuk yang tidak suka CLI)

1. Buka `http://localhost/phpmyadmin`.
2. Klik tab **Import** (di menu atas; tidak perlu memilih database dulu —
   script sudah berisi `CREATE DATABASE` + `USE`).
3. Klik **Choose File** -> pilih `database/schema.mysql.sql`.
4. Format: **SQL**. Character set: biarkan default (`utf-8`).
5. Klik **Go** / **Import**.
6. Tunggu sampai muncul pesan sukses hijau.

Kalau muncul error **"No database selected"**, pilih dulu database apa saja
(misal `nuansa_wangi`) di sidebar kiri, lalu ulangi Import. Script tetap aman.

### Cara B - HeidiSQL

1. Connect ke MySQL Laragon (host `127.0.0.1`, user `root`, password kosong).
2. Menu **File -> Run SQL file...** -> pilih `database/schema.mysql.sql` -> **Open**.
3. Klik tombol **Run** (F9). Pastikan tidak ada baris merah di tab hasil.

### Cara C - Command line (CMD)

```bat
cd C:\path\ke\nuansa-wangi-inventory
mysql -u root < database\schema.mysql.sql
```

### Cara D - PowerShell (hati-hati!)

PowerShell **tidak mendukung** operator `<` seperti CMD. Jangan tulis
`mysql -u root < file.sql` di PowerShell — akan error. Pakai salah satu:

```powershell
# Paling aman: serahkan ke CMD
cmd /c "mysql -u root < database\schema.mysql.sql"
```

```powershell
# Alternatif: pipe isi file
Get-Content database\schema.mysql.sql -Raw | mysql -u root
```

Kalau `mysql` tidak dikenali, pakai path lengkap:

```powershell
cmd /c "C:\laragon\bin\mysql\mysql-8.0.30-winx64\bin\mysql.exe -u root < database\schema.mysql.sql"
```

### Kalau database-nya jalan di port/user lain

Tambahkan `-P 3307` (port), `-u namauser`, atau `-p` (password) sesuai setup dia:

```bat
mysql -h 127.0.0.1 -P 3306 -u root -p < database\schema.mysql.sql
```

---

## 4. Apa yang sebenarnya dilakukan script itu

Supaya dia paham (dan tidak panik kalau lihat output), ini isi pentingnya:

### 4.1 Membuat database & tabel kalau belum ada

```sql
CREATE DATABASE IF NOT EXISTS nuansa_wangi ...;
USE nuansa_wangi;
CREATE TABLE IF NOT EXISTS branches (...);
CREATE TABLE IF NOT EXISTS inventory_items (...);
CREATE TABLE IF NOT EXISTS stock_transactions (...);
```

`IF NOT EXISTS` = tabel yang sudah ada **tidak** dihapus atau dibuat ulang.

### 4.2 Menambah kolom `api_key_hash` hanya kalau belum ada

MySQL 8.0 belum punya `ADD COLUMN IF NOT EXISTS`, jadi script mengecek
`information_schema` dulu lalu menjalankan `ALTER TABLE` lewat prepared statement:

```sql
-- kalau kolom belum ada:
ALTER TABLE branches ADD COLUMN api_key_hash CHAR(64) NULL;
```

Isinya **SHA-256 hex** dari API key cabang. Kunci mentah tidak pernah disimpan
di database.

### 4.3 Menambah unique index hanya kalau belum ada

```sql
CREATE UNIQUE INDEX uq_branch_api_key ON branches (api_key_hash);
```

Fungsinya: satu kunci tidak bisa dipakai dua cabang, dan pencarian kunci saat
login jadi cepat.

### 4.4 Mengisi kunci demo hanya untuk cabang yang belum punya kunci

```sql
UPDATE branches SET api_key_hash = '...'
WHERE id = 'branch-a' AND api_key_hash IS NULL;
```

Karena ada syarat `api_key_hash IS NULL`:

- Database lama -> 3 cabang otomatis dapat kunci demo.
- Database yang kuncinya **sudah dirotasi** -> kunci baru **tidak tertimpa**.
- Cabang yang sengaja dinonaktifkan (`api_key_hash = NULL`) -> akan dapat
  kunci demo lagi kalau script dijalankan ulang. Kalau mau menonaktifkan,
  jalankan `python scripts/manage_keys.py disable --branch branch-x`
  **setelah** migrasi, bukan sebelumnya.

### 4.5 Yang TIDAK dilakukan script

- Tidak menghapus tabel.
- Tidak mengubah/menghapus stok di `inventory_items`.
- Tidak menghapus riwayat di `stock_transactions`.
- Tidak mengubah nama cabang.
- Tidak menyentuh database lain.

### 4.6 Output yang muncul di akhir

Script mencetak tabel verifikasi seperti ini:

```text
tabel                  jumlah
branches               3
inventory_items        4
stock_transactions     <jumlah riwayat lama>
branches_with_key      3
```

`stock_transactions` **boleh berapa saja** — itu riwayat lama dia, bukan error.
Yang penting `branches_with_key = 3`.

---

## 5. Verifikasi setelah migrasi

Jalankan query ini (phpMyAdmin -> SQL, atau CLI). Semua harus sesuai.

### 5.1 Kolom `api_key_hash` sudah ada

```sql
SHOW COLUMNS FROM branches LIKE 'api_key_hash';
```

Harus muncul satu baris, `Type = char(64)`, `Null = YES`.

### 5.2 Unique index sudah ada

```sql
SHOW INDEX FROM branches WHERE Key_name = 'uq_branch_api_key';
```

Harus ada satu baris dengan `Non_unique = 0`.

### 5.3 Semua cabang punya kunci

```sql
SELECT id, name, (api_key_hash IS NOT NULL) AS punya_kunci
FROM branches ORDER BY id;
```

Target: 3 baris, `punya_kunci = 1` semua.

### 5.4 Data lama masih utuh

```sql
SELECT sku, qty FROM inventory_items ORDER BY sku;
SELECT COUNT(*) AS total_riwayat FROM stock_transactions;
```

Bandingkan `qty` dengan sebelum migrasi. Harus sama persis.

---

## 6. Setelah migrasi: konfigurasi aplikasi

### 6.1 Buat `.env`

Copy `.env.example` menjadi `.env` di root project, lalu isi bagian MySQL:

```dotenv
DB_BACKEND=mysql
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=nuansa_wangi
```

`DATABASE_URL` boleh dibiarkan — diabaikan saat `DB_BACKEND=mysql`.

Lalu isi keamanan:

```dotenv
AUTH_REQUIRED=true
ADMIN_API_KEY=nw-demo-admin-4d2f8b6c
RATE_LIMIT_PER_MINUTE=600
RATE_LIMIT_BURST=120
```

> `ADMIN_API_KEY` **wajib diisi** kalau ingin bisa melihat semua cabang dan
> membuka `/branches` + `/summary`. Kalau dikosongkan, endpoint admin mati.

### 6.2 Install dependensi

```bash
# hanya MySQL:
pip install -r server/requirements-mysql.txt

# kalau ingin PostgreSQL + MySQL sekaligus:
pip install -r server/requirements.txt -r server/requirements-mysql.txt
```

### 6.3 Jalankan server

```bash
python -m uvicorn app.main:app --app-dir server --host 127.0.0.1 --port 8000
```

Cek:

```bash
curl http://localhost:8000/health
```

Harus membalas:

```json
{"status":"ok","database":"connected","backend":"mysql","version":"1.1.0",...}
```

### 6.4 Buka dashboard

- Dashboard: **http://localhost:8000/dashboard**
- Swagger: **http://localhost:8000/docs** (klik **Authorize**, isi `X-API-Key`)

Kunci demo (sama dengan yang di-seed script):

| Peran | Kunci |
|---|---|
| branch-a (Empang) | `nw-demo-branch-a-7f3a9c1e` |
| branch-b (Cilendek) | `nw-demo-branch-b-2b8d4f6a` |
| branch-c (Cibinong) | `nw-demo-branch-c-9c5e7a1d` |
| admin | `nw-demo-admin-4d2f8b6c` |

### 6.5 Jalankan pengujian

```bash
python client/test_scenarios.py
```

Target: **`Lulus : 54`, `Gagal : 0`**. Setelah selesai, script mengembalikan
stok ke angka awal.

---

## 7. Disarankan: ganti kunci demo

Kunci demo sengaja ditulis di repo untuk latihan/penilaian. Sebelum dipakai
sungguhan, rotasi:

```bash
python scripts/manage_keys.py list
python scripts/manage_keys.py rotate --branch branch-a
```

Kunci baru **hanya ditampilkan sekali**. Simpan, lalu:

1. Update `BRANCH_API_KEY` cabang tersebut di `.env` / `docker-compose.yml`.
2. Restart client cabang.
3. Kunci lama langsung tidak berlaku (server tidak perlu restart).

Ganti juga `ADMIN_API_KEY` di `.env` dengan nilai acak yang kuat (32+ karakter),
lalu restart server.

---

## 8. Lampiran (opsional) — kalau database-nya PostgreSQL / Supabase

> **Tidak perlu dibaca kalau kamu memakai MySQL/Laragon.** Bagian ini hanya
> catatan singkat untuk pemakaian Supabase, supaya panduan tetap lengkap.
> Kalau kamu mengikuti panduan ini dari awal, lewati saja.

Panduan utama di atas khusus MySQL. Untuk PostgreSQL, caranya berbeda sedikit:

1. Buka project Supabase -> **SQL Editor** -> **New query**.
2. Tempel seluruh isi `database/schema.sql` -> **Run**.
3. Verifikasi:

```sql
SELECT column_name FROM information_schema.columns
WHERE table_name = 'branches' ORDER BY ordinal_position;

SELECT COUNT(*) FILTER (WHERE api_key_hash IS NOT NULL) AS cabang_punya_kunci
FROM branches;
```

Harus muncul kolom `api_key_hash` dan `cabang_punya_kunci = 3`.
Sama seperti versi MySQL, script ini idempotent dan tidak menimpa kunci
yang sudah dirotasi.

---

## 9. Troubleshooting

| Gejala | Penyebab | Solusi |
|---|---|---|
| `ERROR 1064 ... near 'CHECK ...'` | MySQL 5.7 atau lebih lama | Upgrade ke MySQL 8.0+ (Laragon terbaru) |
| `ERROR 1045 (28000): Access denied` | User/password salah | Default Laragon: `-u root` tanpa password. Kalau ada password, tambah `-p` |
| `ERROR 2003 / Can't connect to MySQL server` | MySQL belum jalan / port beda | Laragon -> **Start All**. Cek port di Laragon, mis. `3307` -> pakai `-P 3307` |
| `Unknown column 'api_key_hash'` saat API jalan | Script migrasi belum dijalankan | Ulangi langkah 3, lalu restart API |
| `/health` bilang `"backend":"postgres"` padahal sudah setup MySQL | `.env` tidak punya `DB_BACKEND=mysql` dan masih ada `DATABASE_URL`, jadi aplikasi memilih PostgreSQL otomatis | Tambahkan `DB_BACKEND=mysql` di `.env`, restart API. Lihat [0.1](#01-kalau-aplikasi-masih-nyambung-ke-supabase) |
| Data di dashboard tidak berubah setelah migrasi MySQL | Aplikasi membaca database lain (Supabase) | Sama seperti di atas: pastikan `"backend":"mysql"` di `/health` |
| `No database selected` di phpMyAdmin | Import tanpa memilih DB | Pilih `nuansa_wangi` di sidebar lalu ulangi, atau pakai CLI |
| Duplicate key saat tes | Database sudah pernah dipakai tes | Jalankan `database/reset_demo.mysql.sql`, lalu tes lagi |
| PowerShell error `'<' operator is reserved` | `<` tidak didukung PowerShell | Pakai `cmd /c "mysql -u root < database\schema.mysql.sql"` |
| `caching_sha2_password` / auth plugin error | Driver butuh crypto | `pip install cryptography`, atau buat user dengan `mysql_native_password` |
| Import phpMyAdmin timeout / file terlalu besar | Batas upload PHP | Pakai CLI (langkah 3 Cara C/D) |
| `branches_with_key` kurang dari 3 | Ada cabang yang kuncinya `NULL` | Jalankan `python scripts/manage_keys.py rotate --branch <id>` |
| Dashboard cuma menampilkan toast lalu balik ke login | Cache CSS lama | Hard refresh **Ctrl+F5** |

---

## 10. Rollback (kalau benar-benar perlu)

Migrasi ini aditif, jadi rollback biasanya tidak perlu. Tapi kalau harus
kembali ke aplikasi versi lama:

```sql
USE nuansa_wangi;
ALTER TABLE branches DROP INDEX uq_branch_api_key;
ALTER TABLE branches DROP COLUMN api_key_hash;
```

> Setelah rollback, **API versi baru tidak akan jalan** (butuh kolom itu).
> Backup dari langkah 2 lebih disarankan daripada rollback manual.

---

## 11. Checklist akhir

- [ ] `SELECT VERSION();` -> 8.0+
- [ ] Backup sudah dibuat dan ukurannya wajar
- [ ] `database/schema.mysql.sql` dijalankan tanpa error
- [ ] `SHOW COLUMNS FROM branches LIKE 'api_key_hash';` -> ada
- [ ] `SHOW INDEX FROM branches WHERE Key_name='uq_branch_api_key';` -> ada
- [ ] `branches_with_key` = 3
- [ ] Stok & jumlah riwayat sama seperti sebelum migrasi
- [ ] `.env` berisi `DB_BACKEND=mysql`, kredensial MySQL, `ADMIN_API_KEY`
- [ ] `.env` **tidak** hasil copy dari komputer lain (atau sudah ditambah `DB_BACKEND=mysql`)
- [ ] `pip install -r server/requirements-mysql.txt`
- [ ] `/health` -> `"database":"connected","backend":"mysql"`
- [ ] Dashboard bisa login
- [ ] `python client/test_scenarios.py` -> **54/54 lulus**
- [ ] (Untuk pemakaian nyata) kunci demo sudah dirotasi

---

## 12. FAQ singkat

**Apakah data stok/riwayat hilang?**
Tidak. Script hanya menambah kolom + index di `branches` dan mengisi hash kunci
untuk cabang yang belum punya. Stok dan riwayat tidak disentuh.

**Aman dijalankan dua kali?**
Ya. Semua bagian migrasi mengecek dulu (`IF NOT EXISTS`, `information_schema`,
`WHERE api_key_hash IS NULL`).

**Kunci yang sudah saya ganti, tertimpa tidak?**
Tidak. Seed hanya mengisi baris yang `api_key_hash IS NULL`.

**Perlu stop server API dulu?**
Disarankan, supaya tidak ada request yang membaca skema saat `ALTER TABLE`.
Migrasinya cuma sedetik, tapi lebih rapi kalau API dimatikan dulu.

**Harus install ulang / buat database baru?**
Tidak. Cukup jalankan script di database yang sudah ada.

**Kalau teman saya belum pernah punya database `nuansa_wangi`?**
Tetap ikut panduan ini. Script akan membuat database, tabel, data awal, dan
kunci demo sekaligus.

**Apakah `.env` saya ikut terkirim kalau saya zip project?**
Bisa, kalau di-zip mentah. Kirim lewat `git clone` / `git archive`, atau
exclude `.env`, `certs/`, `backups/`, `logs/`, dan `.venv/`.

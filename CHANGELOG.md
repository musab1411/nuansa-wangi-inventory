# Changelog

Semua perubahan penting pada proyek ini dicatat di berkas ini.
Format mengikuti [Keep a Changelog](https://keepachangelog.com/id/1.1.0/),
dan penomoran versi mengikuti [Semantic Versioning](https://semver.org/lang/id/).

Versi yang berjalan dapat diperiksa dari `GET /health` atau berkas
`server/app/main.py` (`APP_VERSION`).

## [1.1.0] - 2026-11-16

Rilis yang dipakai pada laporan dan demonstrasi proyek.

### Ditambahkan
- Autentikasi kunci API per cabang (`branches.api_key_hash`, SHA-256) dan kunci
  admin melalui `ADMIN_API_KEY`, dengan pembatasan `branch_id` pada setiap
  endpoint data.
- Dashboard web di `/dashboard`: halaman login, tampilan operator cabang
  (stok, formulir mutasi, riwayat), dan tampilan admin (ringkasan agregat,
  filter cabang), auto-refresh 10 detik.
- Rate limiter token bucket in-process dengan HTTP 429 dan header `Retry-After`.
- HTTPS lokal (port 8443, sertifikat self-signed) melalui `scripts/run_https.ps1`.
- Backup dan restore JSON (`scripts/backup.py`, `scripts/restore.py`) beserta
  pembungkus PowerShell dan log.
- Metrik Prometheus ringan di `/metrics`, plus `scripts/watch_health.ps1`.
- Manajemen kunci API: `scripts/manage_keys.py` (`list`, `rotate`, `set`,
  `disable`, `verify`).
- Backend MySQL 8.0 sebagai alternatif PostgreSQL/Supabase, dipilih lewat
  `DB_BACKEND`, dengan migrasi idempoten `database/schema.mysql.sql`.
- Pengujian otomatis 54 pemeriksaan (`client/test_scenarios.py`) untuk empat
  skenario: update berurutan, request paralel, penanganan error dan retry, serta
  keamanan kunci API.

### Diperbaiki
- Halaman login dashboard tidak pernah tertutup setelah masuk karena atribut
  `hidden` kalah oleh `display: grid`; ditambahkan aturan
  `[hidden] { display: none !important; }`.
- Default `API_URL` klien diubah dari `http://localhost:8000` menjadi
  `http://127.0.0.1:8000`. Di Windows, `localhost` dipetakan ke IPv6 lebih
  dahulu sehingga setiap request tertunda sekitar 2 detik; waktu pengujian
  MySQL turun dari sekitar 137 detik menjadi 5,2 detik.
- Konsistensi versi aplikasi dan README diseragamkan pada `1.1.0`.

### Dokumentasi
- `README.md`: panduan instalasi untuk Supabase dan MySQL, troubleshooting, dan
  tangkapan layar dashboard.
- `PANDUAN_UPDATE_DATABASE.md`: panduan migrasi basis data untuk mesin atau
  salinan proyek lain, termasuk pemisahan alur MySQL/Laragon dan Supabase.

## [1.0.0] - 2026-11-07

Versi awal (baseline) sebelum fitur operasional ditambahkan.

### Ditambahkan
- Arsitektur client-server: satu API FastAPI sebagai koordinator dan tiga node
  cabang (Empang, Cilendek, Cibinong) dengan empat SKU.
- Pengendalian konkurensi dengan `SELECT ... FOR UPDATE`, transaksi atomik,
  rollback, dan constraint `CHECK (qty >= 0)`.
- Idempotensi `request_id` dengan unique index, termasuk pengembalian hasil lama
  (`idempotent_replay`) dan penolakan `REQUEST_ID_MISMATCH`.
- Riwayat mutasi stok (`stock_transactions`) sebagai jejak audit, termasuk baris
  `REJECTED`.
- Skema PostgreSQL/Supabase (`database/schema.sql`) beserta data awal dan
  `reset_demo.sql`.
- Docker Compose untuk API dan tiga klien pada jaringan `jmk48-net`.

-- =====================================================================
--  Nuansa Wangi - Sistem Inventaris Multi-Cabang
--  VERSI MySQL 8.0+ (Laragon / XAMPP / MySQL Community)
--
--  Cara pakai:
--    1. Nyalakan MySQL (Laragon -> Start All)
--    2. Buka phpMyAdmin / HeidiSQL / MySQL client
--    3. Jalankan SELURUH isi file ini
--       (phpMyAdmin: tab "Import" -> pilih file ini -> Go)
--       (CLI: mysql -u root < database/schema.mysql.sql)
--
--  Catatan: ini versi portabel dari schema.sql (PostgreSQL).
--  Perbedaan teknis dijelaskan di komentar tiap bagian.
-- =====================================================================

CREATE DATABASE IF NOT EXISTS nuansa_wangi
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE nuansa_wangi;

-- 1) Master cabang -----------------------------------------------------
--  api_key_hash = SHA-256 (hex) dari API key cabang. Kunci mentah tidak
--  disimpan di database. NULL = cabang dinonaktifkan (tidak bisa transaksi).
CREATE TABLE IF NOT EXISTS branches (
    id            VARCHAR(50)  NOT NULL,
    name          VARCHAR(100) NOT NULL,
    api_key_hash  CHAR(64)     NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_branch_api_key (api_key_hash)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

--  Migrasi untuk database yang dibuat sebelum fitur API key ada.
--  MySQL 8.0 tidak mendukung ADD COLUMN IF NOT EXISTS, jadi dicek lewat
--  information_schema lalu dijalankan sebagai prepared statement.
SET @has_api_key_hash := (
    SELECT COUNT(*) FROM information_schema.columns
    WHERE table_schema = DATABASE()
      AND table_name = 'branches'
      AND column_name = 'api_key_hash'
);
SET @ddl := IF(@has_api_key_hash = 0,
    'ALTER TABLE branches ADD COLUMN api_key_hash CHAR(64) NULL',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @has_key_index := (
    SELECT COUNT(*) FROM information_schema.statistics
    WHERE table_schema = DATABASE()
      AND table_name = 'branches'
      AND index_name = 'uq_branch_api_key'
);
SET @ddl := IF(@has_key_index = 0,
    'CREATE UNIQUE INDEX uq_branch_api_key ON branches (api_key_hash)',
    'SELECT 1');
PREPARE stmt FROM @ddl; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2) Stok terpusat -----------------------------------------------------
--  PostgreSQL: TIMESTAMPTZ + now(). MySQL: TIMESTAMP(6) + CURRENT_TIMESTAMP(6).
--  ON UPDATE membuat updated_at otomatis berubah saat qty di-update.
CREATE TABLE IF NOT EXISTS inventory_items (
    sku         VARCHAR(64)  NOT NULL,
    name        VARCHAR(120) NOT NULL,
    category    ENUM('BIBIT','BOTOL') NOT NULL,
    unit        ENUM('ml','pcs')      NOT NULL,
    qty         INT          NOT NULL DEFAULT 0,
    updated_at  TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                             ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (sku),
    -- Pengaman lapis kedua (lapis pertama = validasi + row lock di API)
    CONSTRAINT ck_qty_non_negative CHECK (qty >= 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3) Riwayat mutasi stok ----------------------------------------------
--  PostgreSQL: BIGINT GENERATED ALWAYS AS IDENTITY
--  MySQL     : BIGINT UNSIGNED AUTO_INCREMENT
--
--  Idempotensi: PostgreSQL memakai PARTIAL UNIQUE INDEX
--      (request_id) WHERE status = 'SUCCESS'
--  MySQL tidak punya partial index, jadi dipakai trik GENERATED COLUMN:
--      request_id_success = request_id bila SUCCESS, NULL bila REJECTED
--      lalu UNIQUE INDEX pada kolom itu.
--  MySQL mengizinkan banyak NULL pada UNIQUE index, sehingga request yang
--  DITOLAK tetap boleh dicoba ulang (sesuai perilaku versi PostgreSQL).
CREATE TABLE IF NOT EXISTS stock_transactions (
    id          BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    request_id  VARCHAR(100) NOT NULL,
    branch_id   VARCHAR(50)  NOT NULL,
    sku         VARCHAR(64)  NOT NULL,
    action      ENUM('ADD','SUBTRACT') NOT NULL,
    delta       INT          NOT NULL,          -- bertanda: + tambah, - kurang
    qty_before  INT          NOT NULL,
    qty_after   INT          NOT NULL,
    status      ENUM('SUCCESS','REJECTED') NOT NULL DEFAULT 'SUCCESS',
    note        VARCHAR(255) NULL,
    created_at  TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    request_id_success VARCHAR(100)
        GENERATED ALWAYS AS (CASE WHEN status = 'SUCCESS' THEN request_id ELSE NULL END) STORED,

    PRIMARY KEY (id),
    UNIQUE KEY uq_tx_request_success (request_id_success),
    KEY idx_tx_sku_id    (sku, id),
    KEY idx_tx_branch_id (branch_id, id),

    CONSTRAINT fk_tx_branch FOREIGN KEY (branch_id) REFERENCES branches(id),
    CONSTRAINT fk_tx_sku    FOREIGN KEY (sku)       REFERENCES inventory_items(sku),

    CONSTRAINT ck_delta_nonzero    CHECK (delta <> 0),
    CONSTRAINT ck_qty_before_nonneg CHECK (qty_before >= 0),
    CONSTRAINT ck_qty_after_nonneg  CHECK (qty_after  >= 0),
    -- Transaksi sukses harus konsisten secara matematis
    CONSTRAINT ck_success_math CHECK (status <> 'SUCCESS' OR qty_after = qty_before + delta)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 4) Keamanan ----------------------------------------------------------
--  MySQL tidak punya Row Level Security (RLS) seperti PostgreSQL/Supabase.
--  Karena itu:
--    - JANGAN buka port MySQL ke internet.
--    - Untuk pemakaian lokal, cukup bind ke 127.0.0.1 (default Laragon).
--    - Buat user khusus aplikasi, jangan pakai root untuk produksi:
--        CREATE USER 'nuansa'@'localhost' IDENTIFIED BY 'password_kuat';
--        GRANT SELECT, INSERT, UPDATE ON nuansa_wangi.* TO 'nuansa'@'localhost';
--  API tetap menjadi satu-satunya jalur resmi perubahan stok.

-- 5) Data awal ---------------------------------------------------------
INSERT IGNORE INTO branches (id, name) VALUES
    ('branch-a', 'Cabang Empang'),
    ('branch-b', 'Cabang Cilendek'),
    ('branch-c', 'Cabang Cibinong');

INSERT IGNORE INTO inventory_items (sku, name, category, unit, qty) VALUES
    ('BIBIT-BACCARAT', 'Bibit Parfum Baccarat', 'BIBIT', 'ml',  100),
    ('BIBIT-OUD',      'Bibit Parfum Oud',      'BIBIT', 'ml',  250),
    ('BOTOL-KACA-30',  'Botol Kaca 30ml',       'BOTOL', 'pcs',  50),
    ('BOTOL-KACA-50',  'Botol Kaca 50ml',       'BOTOL', 'pcs',  80);

-- 6) API key per cabang ------------------------------------------------
--  Kunci DEMO di bawah hanya untuk latihan/penilaian. Ganti sebelum dipakai
--  sungguhan dengan:  python scripts/manage_keys.py rotate --branch branch-a
--
--  branch-a : nw-demo-branch-a-7f3a9c1e
--  branch-b : nw-demo-branch-b-2b8d4f6a
--  branch-c : nw-demo-branch-c-9c5e7a1d
--
--  `AND api_key_hash IS NULL` membuat rotasi kunci tidak tertimpa saat
--  file schema dijalankan ulang.
UPDATE branches SET api_key_hash = '8e3fa6c1a9d1a7b36eacb26d62766b4eefc94ec20bc4f2535a0a3caa0d3292e9'
    WHERE id = 'branch-a' AND api_key_hash IS NULL;
UPDATE branches SET api_key_hash = 'e7930c4870255c3f02d2428243c045ae980bd001d5e265950acf8a61f9e8fa9e'
    WHERE id = 'branch-b' AND api_key_hash IS NULL;
UPDATE branches SET api_key_hash = 'a3bca45c4c6159ae7bb13c2542990fdcc3251ba1b955026d48397c656c0aad2b'
    WHERE id = 'branch-c' AND api_key_hash IS NULL;

-- Verifikasi cepat
SELECT 'branches' AS tabel, COUNT(*) AS jumlah FROM branches
UNION ALL
SELECT 'inventory_items', COUNT(*) FROM inventory_items
UNION ALL
SELECT 'stock_transactions', COUNT(*) FROM stock_transactions
UNION ALL
SELECT 'branches_with_key', COUNT(*) FROM branches WHERE api_key_hash IS NOT NULL;

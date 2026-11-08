-- =====================================================================
--  Nuansa Wangi - Sistem Inventaris Multi-Cabang
--  Jalankan file ini SEKALI di Supabase: Dashboard -> SQL Editor -> New query -> Run
--  Aman dijalankan ulang (idempotent).
-- =====================================================================

-- 1) Master cabang (id disamakan dengan diagram arsitektur: branch-a/b/c)
CREATE TABLE IF NOT EXISTS branches (
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL
);

-- 2) Stok terpusat: 1 baris = 1 barang (dipakai bersama oleh semua cabang)
--    Bibit Parfum -> satuan ml, Botol Parfum -> satuan pcs
CREATE TABLE IF NOT EXISTS inventory_items (
    sku         TEXT PRIMARY KEY,
    name        TEXT        NOT NULL,
    category    TEXT        NOT NULL CHECK (category IN ('BIBIT', 'BOTOL')),
    unit        TEXT        NOT NULL CHECK (unit IN ('ml', 'pcs')),
    qty         INTEGER     NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Pengaman lapis kedua (lapis pertama = validasi + row lock di API Server)
    CONSTRAINT ck_qty_non_negative CHECK (qty >= 0)
);

-- 3) Riwayat mutasi stok (audit log per cabang)
CREATE TABLE IF NOT EXISTS stock_transactions (
    id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    request_id  TEXT        NOT NULL,                      -- idempotency key
    branch_id   TEXT        NOT NULL REFERENCES branches(id),
    sku         TEXT        NOT NULL REFERENCES inventory_items(sku),
    action      TEXT        NOT NULL CHECK (action IN ('ADD', 'SUBTRACT')),
    delta       INTEGER     NOT NULL CHECK (delta <> 0),   -- bertanda: + tambah, - kurang
    qty_before  INTEGER     NOT NULL CHECK (qty_before >= 0),
    qty_after   INTEGER     NOT NULL CHECK (qty_after >= 0),
    status      TEXT        NOT NULL DEFAULT 'SUCCESS' CHECK (status IN ('SUCCESS', 'REJECTED')),
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Transaksi sukses harus konsisten secara matematis
    CONSTRAINT ck_success_math CHECK (status <> 'SUCCESS' OR qty_after = qty_before + delta)
);

-- Idempotensi: satu request_id hanya boleh sukses satu kali.
-- (Request yang DITOLAK boleh dicoba ulang, karena stok belum berubah.)
CREATE UNIQUE INDEX IF NOT EXISTS uq_tx_request_success
    ON stock_transactions (request_id) WHERE status = 'SUCCESS';
CREATE INDEX IF NOT EXISTS idx_tx_sku_id    ON stock_transactions (sku, id DESC);
CREATE INDEX IF NOT EXISTS idx_tx_branch_id ON stock_transactions (branch_id, id DESC);

-- 4) Keamanan Supabase: tabel di schema public otomatis terekspos lewat Data API.
--    API Server kita konek langsung via Postgres (role postgres, bypass RLS),
--    jadi RLS dinyalakan TANPA policy = akses anon/authenticated via REST Supabase ditutup.
ALTER TABLE branches           ENABLE ROW LEVEL SECURITY;
ALTER TABLE inventory_items    ENABLE ROW LEVEL SECURITY;
ALTER TABLE stock_transactions ENABLE ROW LEVEL SECURITY;

-- 5) Data awal (contoh, silakan sesuaikan)
INSERT INTO branches (id, name) VALUES
    ('branch-a', 'Cabang Empang'),
    ('branch-b', 'Cabang Cilendek'),
    ('branch-c', 'Cabang Cibinong')
ON CONFLICT (id) DO NOTHING;

INSERT INTO inventory_items (sku, name, category, unit, qty) VALUES
    ('BIBIT-BACCARAT', 'Bibit Parfum Baccarat', 'BIBIT', 'ml',  100),
    ('BIBIT-OUD',      'Bibit Parfum Oud',      'BIBIT', 'ml',  250),
    ('BOTOL-KACA-30',  'Botol Kaca 30ml',       'BOTOL', 'pcs',  50),
    ('BOTOL-KACA-50',  'Botol Kaca 50ml',       'BOTOL', 'pcs',  80)
ON CONFLICT (sku) DO NOTHING;

-- 6) API key per cabang ------------------------------------------------
--  Kolom ini menyimpan SHA-256 (hex) dari API key. Kunci mentah TIDAK
--  disimpan di database, jadi kalau database bocor tidak langsung terpakai.
--
--  Migrasi aman untuk database lama: kolom & index hanya ditambah sekali.
ALTER TABLE branches ADD COLUMN IF NOT EXISTS api_key_hash TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS uq_branch_api_key
    ON branches (api_key_hash) WHERE api_key_hash IS NOT NULL;

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

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

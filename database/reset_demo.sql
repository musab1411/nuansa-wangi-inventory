-- Reset data demo: hapus semua riwayat & kembalikan stok ke angka awal.
-- Jalankan di Supabase SQL Editor kalau mau mulai demo dari nol.
TRUNCATE stock_transactions RESTART IDENTITY;
UPDATE inventory_items SET qty = 100, updated_at = now() WHERE sku = 'BIBIT-BACCARAT';
UPDATE inventory_items SET qty = 250, updated_at = now() WHERE sku = 'BIBIT-OUD';
UPDATE inventory_items SET qty =  50, updated_at = now() WHERE sku = 'BOTOL-KACA-30';
UPDATE inventory_items SET qty =  80, updated_at = now() WHERE sku = 'BOTOL-KACA-50';

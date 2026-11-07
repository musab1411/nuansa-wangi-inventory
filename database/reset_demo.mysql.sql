-- Reset data demo (versi MySQL 8.0+):
-- hapus semua riwayat & kembalikan stok ke angka awal.
--
-- Jalankan di phpMyAdmin / HeidiSQL / MySQL client:
--   mysql -u root nuansa_wangi < database/reset_demo.mysql.sql
--
-- Catatan: TRUNCATE tidak bisa dipakai pada tabel yang direferensikan
-- FOREIGN KEY, jadi dipakai DELETE + ALTER TABLE untuk reset AUTO_INCREMENT.

USE nuansa_wangi;

DELETE FROM stock_transactions;
ALTER TABLE stock_transactions AUTO_INCREMENT = 1;

UPDATE inventory_items SET qty = 100 WHERE sku = 'BIBIT-BACCARAT';
UPDATE inventory_items SET qty = 250 WHERE sku = 'BIBIT-OUD';
UPDATE inventory_items SET qty =  50 WHERE sku = 'BOTOL-KACA-30';
UPDATE inventory_items SET qty =  80 WHERE sku = 'BOTOL-KACA-50';

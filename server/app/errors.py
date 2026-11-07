"""
Exception domain yang dipakai bersama oleh backend PostgreSQL dan MySQL.

Tujuannya: main.py tidak perlu tahu driver apa yang dipakai. Setiap backend
menerjemahkan error drivernya sendiri menjadi exception di file ini, lalu
main.py memetakan semuanya ke status HTTP yang sama.
"""


class BranchNotFound(Exception):
    def __init__(self, branch_id):
        super().__init__(branch_id)
        self.branch_id = branch_id


class ItemNotFound(Exception):
    def __init__(self, sku):
        super().__init__(sku)
        self.sku = sku


class InsufficientStock(Exception):
    def __init__(self, sku, unit, available, requested):
        super().__init__(f"{sku}: tersedia {available} {unit}, diminta {requested} {unit}")
        self.sku = sku
        self.unit = unit
        self.available = available
        self.requested = requested


class RequestIdMismatch(Exception):
    def __init__(self, request_id):
        super().__init__(request_id)
        self.request_id = request_id


class RequestIdConflict(Exception):
    """request_id yang sama dipakai dua request bersamaan (unique index)."""


class ConstraintViolation(Exception):
    """Database menolak data karena melanggar CHECK/FK (pengaman lapis kedua)."""


class LockTimeout(Exception):
    """Baris stok dikunci transaksi lain lebih lama dari batas -> aman di-retry."""


class DatabaseUnavailable(Exception):
    """Database tidak bisa dijangkau / koneksi habis."""

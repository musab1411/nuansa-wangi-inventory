"""
Rate limiter in-process (token bucket) tanpa dependensi tambahan.

Cukup untuk satu proses uvicorn. Kalau nanti server dijalankan multi-worker
atau multi-container, ganti dengan Redis (mis. slowapi + redis) supaya
hitungannya dibagi bersama.
"""
import math
import os
import threading
import time

RATE_LIMIT_PER_MINUTE = float(os.getenv("RATE_LIMIT_PER_MINUTE", "600"))
RATE_LIMIT_BURST = int(os.getenv("RATE_LIMIT_BURST", "120"))
RATE_LIMIT_ENABLED = RATE_LIMIT_PER_MINUTE > 0 and RATE_LIMIT_BURST > 0


class RateLimiter:
    def __init__(self, per_minute: float, burst: int):
        self.rate_per_second = per_minute / 60.0
        self.burst = max(1, burst)
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._checks = 0

    def check(self, key: str) -> tuple[bool, int, int]:
        """Kembalikan (allowed, remaining, retry_after_detik)."""
        if not RATE_LIMIT_ENABLED or self.rate_per_second <= 0:
            return True, self.burst, 0

        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = [float(self.burst), now]
                self._buckets[key] = bucket
            else:
                elapsed = max(0.0, now - bucket[1])
                bucket[0] = min(float(self.burst), bucket[0] + elapsed * self.rate_per_second)
                bucket[1] = now

            if bucket[0] >= 1.0:
                bucket[0] -= 1.0
                allowed = True
                retry_after = 0
            else:
                allowed = False
                retry_after = max(1, math.ceil((1.0 - bucket[0]) / self.rate_per_second))

            remaining = int(bucket[0])
            self._checks += 1
            if self._checks % 2000 == 0:
                self._cleanup(now)

        return allowed, remaining, retry_after

    def _cleanup(self, now: float) -> None:
        """Buang bucket yang sudah penuh kembali (tidak aktif)."""
        stale = [
            key for key, (tokens, last) in self._buckets.items()
            if tokens >= self.burst and now - last > 300
        ]
        for key in stale:
            del self._buckets[key]

    @property
    def describe(self) -> dict:
        return {
            "enabled": RATE_LIMIT_ENABLED,
            "per_minute": RATE_LIMIT_PER_MINUTE,
            "burst": RATE_LIMIT_BURST,
        }


LIMITER = RateLimiter(RATE_LIMIT_PER_MINUTE, RATE_LIMIT_BURST)

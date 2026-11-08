"""
Metrik ringan dalam format Prometheus (tanpa dependensi prometheus_client).

Contoh scrape:
    curl http://localhost:8000/metrics
"""
import threading
import time
from collections import Counter, defaultdict

_HELP = {
    "nw_http_requests_total": "Total request HTTP yang diterima.",
    "nw_http_request_duration_seconds": "Durasi request HTTP dalam detik.",
    "nw_auth_failures_total": "Total kegagalan autentikasi.",
    "nw_rate_limited_total": "Total request yang ditolak rate limiter.",
    "nw_stock_updates_total": "Total percobaan update stok.",
}


def _escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(labels: dict) -> str:
    if not labels:
        return ""
    inner = ",".join(f'{k}="{_escape(v)}"' for k, v in sorted(labels.items()))
    return "{" + inner + "}"


class Metrics:
    def __init__(self):
        self.started = time.time()
        self._lock = threading.Lock()
        self._counters: Counter = Counter()
        self._gauges: dict[tuple, float] = {}
        self._duration_sum: defaultdict = defaultdict(float)
        self._duration_count: defaultdict = defaultdict(int)

    def inc(self, name: str, **labels) -> None:
        with self._lock:
            self._counters[(name, tuple(sorted(labels.items())))] += 1

    def set_gauge(self, name: str, value: float, **labels) -> None:
        with self._lock:
            self._gauges[(name, tuple(sorted(labels.items())))] = float(value)

    def observe(self, name: str, seconds: float, **labels) -> None:
        key = (name, tuple(sorted(labels.items())))
        with self._lock:
            self._duration_sum[key] += seconds
            self._duration_count[key] += 1

    @property
    def uptime(self) -> float:
        return max(0.0, time.time() - self.started)

    def render(self, backend: str) -> str:
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            dur_sum = dict(self._duration_sum)
            dur_count = dict(self._duration_count)

        emitted = set()
        for (name, labels), value in sorted(counters.items()):
            if name not in emitted:
                lines.append(f"# HELP {name} {_HELP.get(name, name)}")
                lines.append(f"# TYPE {name} counter")
                emitted.add(name)
            lines.append(f"{name}{_labels(dict(labels))} {value}")

        # Durasi disajikan sebagai _sum dan _count (pola histogram).
        for (name, labels), total in sorted(dur_sum.items()):
            label_dict = dict(labels)
            base = name.removesuffix("_seconds")
            lines.append(f"{base}_seconds_sum{_labels(label_dict)} {total:.6f}")
            lines.append(
                f"{base}_seconds_count{_labels(label_dict)} {dur_count[(name, labels)]}"
            )

        for (name, labels), value in sorted(gauges.items()):
            lines.append(f"{name}{_labels(dict(labels))} {value}")

        lines.append(f"nw_uptime_seconds {self.uptime:.1f}")
        lines.append(f'nw_backend_info{{backend="{_escape(backend)}"}} 1')
        return "\n".join(lines) + "\n"


METRICS = Metrics()

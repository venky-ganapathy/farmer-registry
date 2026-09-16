"""Pin each Locust user to one staff-api pod IP (keep-alive, even CPU).

STAFF_API_HEADLESS must be a headless Service DNS name. ClusterIP has one A
record, so kube-proxy hashing of 32 connections stays uneven.
"""

from __future__ import annotations

import itertools
import os
import socket
import threading
import time

from shared.in_cluster import in_cluster_soak

_lock = threading.Lock()
_cycle = None
_bases: list[str] = []


def resolve_pod_bases(dns_name: str) -> list[str]:
    last: Exception | None = None
    for _ in range(8):
        try:
            infos = socket.getaddrinfo(dns_name, 80, type=socket.SOCK_STREAM)
            ips = sorted({item[4][0] for item in infos})
            if ips:
                port = os.environ.get("STAFF_API_POD_PORT", "8000")
                return [f"http://{ip}:{port}" for ip in ips]
        except OSError as exc:
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"could not resolve {dns_name}: {last}")


def next_pod_base() -> str | None:
    if not in_cluster_soak():
        return None
    dns = os.environ.get("STAFF_API_HEADLESS", "").strip()
    if not dns:
        return None
    global _cycle, _bases
    with _lock:
        if _cycle is None:
            _bases = resolve_pod_bases(dns)
            print(f"[soak] pinning users round-robin onto {_bases}", flush=True)
            _cycle = itertools.cycle(_bases)
        return next(_cycle)

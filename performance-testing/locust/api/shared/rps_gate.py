"""In-cluster soak HTTP rate limit only.

No-ops unless IN_CLUSTER_SOAK=1 (k8s/soak-job.yaml). Laptop Step 1/2 never
waits here, even if SOAK_MAX_RPS is set in the shell.
"""

from __future__ import annotations

import os
import threading
import time

from shared.in_cluster import in_cluster_soak

_max_rps = float(os.environ.get("SOAK_MAX_RPS", "0") or 0) if in_cluster_soak() else 0.0
_lock = threading.Lock()
_next_slot = 0.0


def wait_for_rps_slot() -> None:
    if not in_cluster_soak() or _max_rps <= 0:
        return
    global _next_slot
    interval = 1.0 / _max_rps
    with _lock:
        now = time.time()
        slot = now if now > _next_slot else _next_slot
        _next_slot = slot + interval
        delay = slot - now
    if delay > 0:
        time.sleep(delay)

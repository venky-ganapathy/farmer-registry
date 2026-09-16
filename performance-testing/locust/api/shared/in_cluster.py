"""True only in the Kubernetes soak Job (k8s/soak-job.yaml).

Laptop isolated/blended via locust-staff-api.sh must leave this unset so
RPS cap, pod pinning, and Connection: close never apply to Step 1/2.
"""

from __future__ import annotations

import builtins
import os

_quiet_installed = False


def in_cluster_soak() -> bool:
    return os.environ.get("IN_CLUSTER_SOAK", "").strip().lower() in ("1", "true", "yes")


def quiet_debug_prints() -> None:
    """Drop locustfile DEBUG prints in the Job. Keep Locust stats and [soak] lines."""
    global _quiet_installed
    if _quiet_installed or not in_cluster_soak():
        return
    _quiet_installed = True
    orig = builtins.print

    def _filtered(*args, **kwargs):
        if args and isinstance(args[0], str) and args[0].lstrip().startswith("DEBUG"):
            return
        orig(*args, **kwargs)

    builtins.print = _filtered

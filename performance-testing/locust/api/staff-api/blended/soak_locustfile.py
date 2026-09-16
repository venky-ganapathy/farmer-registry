"""Step 3 soak: same 80:20 mix as blended, no LoadTestShape.

Locust -u/-r/-t apply. Hold a fixed user count for SOAK_RUN_TIME (8h).
HTTP rate is capped with SOAK_MAX_RPS so CPU does not climb back to the
closed-loop ceiling when latency drops.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_STAFF_API = Path(__file__).resolve().parent.parent
for _scenario in (
    "register_read",
    "cr_create",
    "cr_read_and_approve",
    "intake_create",
    "intake_read_and_approve",
):
    _dir = str(_STAFF_API / _scenario)
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

from locust import events

from cr_create_locustfile import CrCreateUser
from cr_read_and_approve_locustfile import CrReadAndApproveUser
from intake_create_locustfile import IntakeCreateUser
from intake_read_and_approve_locustfile import IntakeReadAndApproveUser
from register_read_locustfile import RegisterUser
from shared.in_cluster import in_cluster_soak, quiet_debug_prints
from shared.token_cache import get_access_token

quiet_debug_prints()

RegisterUser.weight = 40
CrReadAndApproveUser.weight = 20
IntakeReadAndApproveUser.weight = 20
CrCreateUser.weight = 10
IntakeCreateUser.weight = 10


@events.test_start.add_listener
def _prefetch_oidc_token(environment, **_kwargs):
    # One token fetch before users spawn so 38 password grants do not hit
    # Keycloak in the same second (that is what caused the ReadTimeouts).
    print(
        f"[soak] IN_CLUSTER_SOAK={os.environ.get('IN_CLUSTER_SOAK', '')} "
        f"SOAK_MAX_RPS={os.environ.get('SOAK_MAX_RPS', '0')} "
        f"DISABLE_HTTP_KEEPALIVE={os.environ.get('DISABLE_HTTP_KEEPALIVE', '')} "
        f"STAFF_API_HEADLESS={os.environ.get('STAFF_API_HEADLESS', '')}",
        flush=True,
    )
    if in_cluster_soak():
        get_access_token()

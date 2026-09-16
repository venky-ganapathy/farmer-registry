"""Shared config + auth helpers for the Farmer Registry load tests.

Laptop (Step 1/2): one OIDC token per Locust user, 10s timeout, original
refresh. In-cluster soak (IN_CLUSTER_SOAK=1): one process-wide token with
a lock and retries so 32 users do not stampede Keycloak.
"""

from __future__ import annotations

import os
import threading
import time

import requests

from shared.in_cluster import in_cluster_soak

KEYCLOAK_BASE = os.environ.get("KEYCLOAK_BASE", "https://keycloak.perftest.openg2p.org")
KEYCLOAK_REALM = os.environ.get("KEYCLOAK_REALM", "staff")
OIDC_CLIENT_ID = os.environ.get("OIDC_CLIENT_ID", "farmer-registry-staff-portal")
OIDC_CLIENT_SECRET = os.environ.get("OIDC_CLIENT_SECRET", "")
OIDC_USERNAME = os.environ.get("OIDC_USERNAME", "admin")
OIDC_PASSWORD = os.environ.get("OIDC_PASSWORD", "")

_lock = threading.Lock()
_shared_token: str | None = None
_shared_exp = 0.0


def _token_url() -> str:
    return f"{KEYCLOAK_BASE}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"


def _grant_body() -> dict:
    data = {
        "grant_type": "password",
        "client_id": OIDC_CLIENT_ID,
        "username": OIDC_USERNAME,
        "password": OIDC_PASSWORD,
    }
    if OIDC_CLIENT_SECRET:
        data["client_secret"] = OIDC_CLIENT_SECRET
    return data


def _post_token_once(timeout: float) -> dict:
    resp = requests.post(_token_url(), data=_grant_body(), timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _post_token_cluster() -> dict:
    last_error: Exception | None = None
    timeout = int(os.environ.get("TOKEN_TIMEOUT", "30"))
    retries = int(os.environ.get("TOKEN_RETRIES", "6"))
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(_token_url(), data=_grant_body(), timeout=timeout)
            if resp.status_code in (429, 503) or resp.status_code >= 500:
                last_error = requests.HTTPError(
                    f"token endpoint HTTP {resp.status_code}", response=resp
                )
                time.sleep(min(8, 0.5 * (2 ** (attempt - 1))))
                continue
            resp.raise_for_status()
            return resp.json()
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            time.sleep(min(8, 0.5 * (2 ** (attempt - 1))))
    raise last_error or RuntimeError("token fetch failed")


def _refresh_shared_locked() -> str:
    global _shared_token, _shared_exp
    now = time.time()
    if _shared_token and now < _shared_exp - 60:
        return _shared_token
    body = _post_token_cluster()
    _shared_token = body["access_token"]
    _shared_exp = time.time() + int(body.get("expires_in", 300))
    return _shared_token


def get_access_token() -> str:
    """Process-wide token. In-cluster soak only; laptop uses TokenCache per user."""
    if not in_cluster_soak():
        raise RuntimeError("get_access_token is in-cluster soak only")
    now = time.time()
    if _shared_token and now < _shared_exp - 60:
        return _shared_token
    with _lock:
        return _refresh_shared_locked()


class TokenCache:
    """Per-user cache on laptop; process-wide cache when IN_CLUSTER_SOAK=1."""

    def __init__(self):
        self._token = None
        self._exp = 0.0

    def token(self) -> str:
        if in_cluster_soak():
            return get_access_token()
        now = time.time()
        if self._token and now < self._exp - 30:
            return self._token
        self._fetch()
        return self._token

    def _fetch(self):
        body = _post_token_once(timeout=10)
        time.sleep(0.5)
        self._token = body["access_token"]
        self._exp = time.time() + int(body.get("expires_in", 300))

    def auth_header(self) -> dict:
        return {"Authorization": f"Bearer {self.token()}"}

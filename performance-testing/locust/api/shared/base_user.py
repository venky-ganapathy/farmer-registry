from __future__ import annotations

import os

from locust import HttpUser, between

from shared.token_cache import TokenCache
from shared.config import STAFF_API_BASE
from shared.request_builder import build_g2p_request
from shared.response_utils import is_expected_business_error, safe_json
from shared.rps_gate import wait_for_rps_slot
from shared.pod_pin import next_pod_base
from shared.in_cluster import in_cluster_soak

# Only the in-cluster Job sets IN_CLUSTER_SOAK=1. Laptop Step 1/2 never
# disable keep-alive or pin to pod IPs.
DISABLE_HTTP_KEEPALIVE = in_cluster_soak() and os.environ.get(
    "DISABLE_HTTP_KEEPALIVE", ""
).lower() in ("1", "true", "yes")


class LocustUser(HttpUser):
    """Shared base class for Farmer Registry Locust workloads."""

    abstract = True
    wait_time = between(0.5, 2.0)
    host = STAFF_API_BASE

    def on_start(self):
        self.tokens = TokenCache()
        self._pin_base = next_pod_base()
        if self._pin_base:
            self.host = self._pin_base
        if DISABLE_HTTP_KEEPALIVE:
            self.client.headers["Connection"] = "close"

    def _url(self, base, path) -> str:
        # Laptop locustfiles pass STAFF_API_BASE; only in-cluster soak replaces it.
        root = self._pin_base if getattr(self, "_pin_base", None) else base
        return f"{root.rstrip('/')}{path}"

    def _auth_headers(self) -> dict:
        headers = self.tokens.auth_header()
        if DISABLE_HTTP_KEEPALIVE:
            headers["Connection"] = "close"
        return headers

    def build_request(self, request_payload: dict, pagination_request: dict | None = None) -> dict:
        return build_g2p_request(
            request_payload=request_payload,
            pagination_request=pagination_request,
            sender_app_url=self.host,
        )

    def _post(self, base, path, payload, name, debug=False):
        if in_cluster_soak():
            wait_for_rps_slot()
        with self.client.post(
            f"{self._url(base, path)}",
            json=payload,
            headers=self._auth_headers(),
            name=name,
            catch_response=True,
        ) as response:
            return self._finalize_response(response, path, debug)

    def _post_multipart(self, base, path, files, data, name, debug=False):
        """For endpoints taking raw multipart/form-data (File/Form params),
        not the usual JSON G2PRequest envelope -- e.g. /documents/upload_documents."""
        if in_cluster_soak():
            wait_for_rps_slot()
        with self.client.post(
            f"{self._url(base, path)}",
            files=files,
            data=data,
            headers=self._auth_headers(),
            name=name,
            catch_response=True,
        ) as response:
            return self._finalize_response(response, path, debug)

    @staticmethod
    def _finalize_response(response, path, debug):
        if debug and not in_cluster_soak():
            try:
                body = response.json()
            except ValueError:
                body = response.text
            print(f"\nDEBUG {path} -> {response.status_code}: {body}\n")

        if response.status_code >= 400:
            response.failure(f"{response.status_code}")
        else:
            header = safe_json(response).get("response_header", {})
            if header.get("response_status") == "ERROR":
                if is_expected_business_error(header):
                    # Sequence-check / already-decided task: HTTP 200, domain
                    # rejection. Latency is a valid sample; do not mark fail.
                    response.success()
                else:
                    response.failure(
                        f"{header.get('response_error_code')}: {header.get('response_error_message')}"
                    )
            else:
                response.success()

        return response

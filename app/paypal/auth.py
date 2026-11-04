"""OAuth client-credentials tokens, one cache per PayPal REST app."""
from __future__ import annotations

import threading
import time

import httpx

# Refresh this many seconds before PayPal says the token expires.
EXPIRY_MARGIN_SECONDS = 60


class TokenProvider:
    """Fetches an access token from /v1/oauth2/token and reuses it until it expires."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        http: httpx.Client,
        clock=time.monotonic,
    ) -> None:
        self._base_url = base_url
        self._auth = (client_id, client_secret)
        self._http = http
        self._clock = clock
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def get(self) -> str:
        with self._lock:
            if self._token is None or self._clock() >= self._expires_at:
                self._refresh()
            assert self._token is not None
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None

    def _refresh(self) -> None:
        resp = self._http.post(
            f"{self._base_url}/v1/oauth2/token",
            auth=self._auth,
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            # Import here to avoid a cycle; client.py imports this module.
            from app.paypal.client import PayPalError

            raise PayPalError.from_response(resp)
        body = resp.json()
        self._token = body["access_token"]
        lifetime = int(body.get("expires_in", 0))
        self._expires_at = self._clock() + max(lifetime - EXPIRY_MARGIN_SECONDS, 0)

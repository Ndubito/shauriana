"""Thin authenticated HTTP client for one PayPal REST app."""
from __future__ import annotations

import ssl
from typing import Any

import httpx
import truststore

from app.paypal.auth import TokenProvider


class PayPalError(RuntimeError):
    """A non-2xx response from PayPal, with its error name and debug ID."""

    def __init__(self, status: int, name: str, message: str, debug_id: str | None, details: Any):
        self.status = status
        self.name = name
        self.message = message
        self.debug_id = debug_id
        self.details = details
        super().__init__(f"PayPal {status} {name}: {message} (debug_id={debug_id})")

    @classmethod
    def from_response(cls, resp: httpx.Response) -> "PayPalError":
        try:
            body = resp.json()
        except ValueError:
            body = {}
        return cls(
            status=resp.status_code,
            # OAuth errors use "error"/"error_description"; REST errors use "name"/"message".
            name=body.get("name") or body.get("error") or "UNKNOWN",
            message=body.get("message") or body.get("error_description") or resp.text[:300],
            debug_id=body.get("debug_id") or resp.headers.get("paypal-debug-id"),
            details=body.get("details"),
        )


class PayPalClient:
    def __init__(self, base_url: str, client_id: str, client_secret: str, timeout: float = 30.0):
        self.base_url = base_url
        # Verify TLS against the OS trust store, so machines with HTTPS-scanning
        # antivirus (e.g. Avast) or corporate proxies work without disabling checks.
        ssl_ctx = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        self._http = httpx.Client(timeout=timeout, verify=ssl_ctx)
        self.tokens = TokenProvider(base_url, client_id, client_secret, self._http)

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        headers: dict | None = None,
    ) -> dict:
        resp = self._send(method, path, json, params, headers)
        if resp.status_code == 401:
            # Token revoked or expired early: refresh once and retry.
            self.tokens.invalidate()
            resp = self._send(method, path, json, params, headers)
        if resp.status_code >= 400:
            raise PayPalError.from_response(resp)
        if not resp.content:
            return {}
        return resp.json()

    def _send(self, method, path, json, params, headers) -> httpx.Response:
        all_headers = {
            "Authorization": f"Bearer {self.tokens.get()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            **(headers or {}),
        }
        return self._http.request(
            method, f"{self.base_url}{path}", json=json, params=params, headers=all_headers
        )

    def close(self) -> None:
        self._http.close()

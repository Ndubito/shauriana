"""Settings loaded from environment variables (and a local .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

BASE_URLS = {
    "sandbox": "https://api-m.sandbox.paypal.com",
    "live": "https://api-m.paypal.com",
}


class ConfigError(RuntimeError):
    pass


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing environment variable {name} (see .env.example)")
    return value


@dataclass(frozen=True)
class AppCredentials:
    client_id: str
    client_secret: str
    email: str


@dataclass(frozen=True)
class Settings:
    paypal_env: str
    base_url: str
    shop: AppCredentials
    supplier: AppCredentials


def load_settings() -> Settings:
    load_dotenv()
    env = os.getenv("PAYPAL_ENV", "sandbox").strip().lower()
    if env not in BASE_URLS:
        raise ConfigError(f"PAYPAL_ENV must be one of {sorted(BASE_URLS)}, got {env!r}")
    return Settings(
        paypal_env=env,
        base_url=BASE_URLS[env],
        shop=AppCredentials(
            client_id=_require("SHOP_CLIENT_ID"),
            client_secret=_require("SHOP_CLIENT_SECRET"),
            email=_require("SHOP_SANDBOX_EMAIL"),
        ),
        supplier=AppCredentials(
            client_id=_require("SUPPLIER_CLIENT_ID"),
            client_secret=_require("SUPPLIER_CLIENT_SECRET"),
            email=_require("SUPPLIER_SANDBOX_EMAIL"),
        ),
    )

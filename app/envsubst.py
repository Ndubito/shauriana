"""Replace ${VAR} placeholders in loaded JSON with environment values."""
from __future__ import annotations

import os
import re

_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


def substitute(value):
    if isinstance(value, str):
        def repl(m: re.Match) -> str:
            name = m.group(1)
            got = os.getenv(name, "").strip()
            if not got:
                raise KeyError(f"Environment variable {name} is not set (needed by a config file)")
            return got

        return _PATTERN.sub(repl, value)
    if isinstance(value, list):
        return [substitute(v) for v in value]
    if isinstance(value, dict):
        return {k: substitute(v) for k, v in value.items()}
    return value

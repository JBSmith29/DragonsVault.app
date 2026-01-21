from __future__ import annotations

import os
from pathlib import Path


def _load_secret_key() -> str | None:
    """Load the Flask secret key from env or an optional file path."""
    secret = os.getenv("SECRET_KEY")
    if secret:
        return secret
    secret_file = os.getenv("SECRET_KEY_FILE")
    if secret_file:
        try:
            secret = Path(secret_file).read_text(encoding="utf-8").strip()
            if secret:
                return secret
        except OSError:
            # Fall back to default handling if the secret file cannot be read.
            pass
    return None


BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_INSTANCE_DIR = BASE_DIR / "instance"
LEGACY_INSTANCE_DIR = BASE_DIR.parent / "instance"
_env_instance = os.getenv("INSTANCE_DIR")
if _env_instance:
    INSTANCE_DIR = Path(_env_instance).resolve()
elif LEGACY_INSTANCE_DIR.exists():
    INSTANCE_DIR = LEGACY_INSTANCE_DIR.resolve()
else:
    INSTANCE_DIR = DEFAULT_INSTANCE_DIR.resolve()

SECRET_KEY_VALUE = _load_secret_key()

"""PII protection.

- phone_hmac: keyed HMAC-SHA256 of the canonical phone. Deterministic, so we can join
  events to leads on it, but useless without the key (no rainbow tables over 10-digit numbers).
- encrypt(): Fernet (AES + HMAC) for PII values we may need back (name, phone).

The key comes from the AU_PII_KEY env var. If it's not set we read/create it in a local
`.env` file (gitignored). In production this would come from a KMS / secrets manager.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from pathlib import Path

from cryptography.fernet import Fernet

ENV_VAR = "AU_PII_KEY"
# repo root, not the current folder: running from elsewhere must not silently create a new key
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def _load_or_create_master_key(env_file: Path = ENV_FILE) -> bytes:
    value = os.environ.get(ENV_VAR)
    if not value and env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if line.startswith(f"{ENV_VAR}="):
                value = line.split("=", 1)[1].strip()
    if not value:
        value = secrets.token_urlsafe(32)
        with open(env_file, "a", encoding="utf-8") as f:
            f.write(f"{ENV_VAR}={value}\n")
        print(f"[ledger] generated a new PII key in {env_file} (keep it; the DB needs it)")
    return value.encode()


class Pii:
    def __init__(self, master_key: bytes | None = None):
        master = master_key or _load_or_create_master_key()
        # derive two independent sub-keys from one secret
        self._mac_key = hashlib.sha256(b"mac:" + master).digest()
        self._fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"enc:" + master).digest()))

    def phone_hmac(self, canonical_phone: str) -> str:
        return hmac.new(self._mac_key, canonical_phone.encode(), hashlib.sha256).hexdigest()

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

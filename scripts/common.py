"""Yhteiset apufunktiot: salaus, loki ja kirjastojen hiljentäminen.

Salaus: AES-256-GCM, avain PBKDF2-SHA256 (600 000 kierrosta), sisältö gzip-pakattu.
Kaikki tiedostot salataan samalla suolalla (symbols.enc:n suola), jolloin sovellus voi
johtaa avaimen kerran ja tallentaa sen laitteelle salasanan sijaan.
"""
from __future__ import annotations

import base64
import contextlib
import gzip
import io
import json
import logging
import os
import sys
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ROOT = Path(__file__).resolve().parent.parent
ITERATIONS = 600_000
LOG = sys.__stderr__

# yfinance kirjoittaa virheisiin tunnuksia -> hiljennetään, ettei mitään näy julkisessa lokissa
logging.getLogger("yfinance").disabled = True


def log(*a):
    print(*a, file=LOG, flush=True)


@contextlib.contextmanager
def quiet():
    """Estää kirjastoja tulostamasta mitään lokiin."""
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        yield


b64e = lambda b: base64.b64encode(b).decode()
b64d = base64.b64decode


class Crypto:
    """Salaa ja purkaa salasanalla. Johdetut avaimet muistetaan, koska PBKDF2 on hidas."""

    def __init__(self, password: str):
        self.password = password
        self._keys: dict[tuple[str, int], bytes] = {}

    def key(self, salt_b64: str, iterations: int) -> bytes:
        k = (salt_b64, int(iterations))
        if k not in self._keys:
            self._keys[k] = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=b64d(salt_b64),
                                       iterations=int(iterations)).derive(self.password.encode("utf-8"))
        return self._keys[k]

    def decrypt(self, env: dict):
        """Heittää InvalidTag-poikkeuksen, jos salasana on väärä."""
        data = AESGCM(self.key(env["salt"], env["iter"])).decrypt(b64d(env["iv"]), b64d(env["ct"]), None)
        if env.get("z") == "gzip":
            data = gzip.decompress(data)
        return json.loads(data.decode("utf-8"))

    def encrypt(self, obj, salt_b64: str | None = None, iterations: int = ITERATIONS) -> dict:
        salt_b64 = salt_b64 or b64e(os.urandom(16))
        iv = os.urandom(12)
        data = gzip.compress(json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        ct = AESGCM(self.key(salt_b64, iterations)).encrypt(iv, data, None)
        return {"v": 1, "z": "gzip", "iter": iterations, "salt": salt_b64, "iv": b64e(iv), "ct": b64e(ct)}

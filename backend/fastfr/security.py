from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import os
import secrets
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .config import settings


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_ulong), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _as_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte))), buffer


def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return data
    source, source_buffer = _as_blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(  # type: ignore[attr-defined]
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)  # type: ignore[attr-defined]
        del source_buffer


def _dpapi_unprotect(data: bytes) -> bytes:
    if sys.platform != "win32":
        return data
    source, source_buffer = _as_blob(data)
    output = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(  # type: ignore[attr-defined]
        ctypes.byref(source), None, None, None, None, 0, ctypes.byref(output)
    ):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(output.pbData, output.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(output.pbData)  # type: ignore[attr-defined]
        del source_buffer


class CryptoManager:
    prefix = b"FFR1"

    def __init__(self, key: bytes | None = None, key_path: Path | None = None) -> None:
        self.key_path = key_path or settings.master_key_path
        self.key = key or self._load_or_create_key()
        self.aes = AESGCM(self.key)

    def _load_or_create_key(self) -> bytes:
        self.key_path.parent.mkdir(parents=True, exist_ok=True)
        if self.key_path.exists():
            return _dpapi_unprotect(self.key_path.read_bytes())
        key = AESGCM.generate_key(bit_length=256)
        protected = _dpapi_protect(key)
        self.key_path.write_bytes(protected)
        try:
            os.chmod(self.key_path, 0o600)
        except OSError:
            pass
        return key

    def encrypt(self, data: bytes, *, context: bytes = b"fast-fr") -> bytes:
        nonce = os.urandom(12)
        return self.prefix + nonce + self.aes.encrypt(nonce, data, context)

    def decrypt(self, data: bytes, *, context: bytes = b"fast-fr") -> bytes:
        if not data.startswith(self.prefix) or len(data) < 17:
            raise ValueError("Invalid encrypted payload")
        nonce = data[4:16]
        return self.aes.decrypt(nonce, data[16:], context)

    def encrypt_text(self, value: str, *, context: bytes = b"fast-fr-text") -> bytes:
        return self.encrypt(value.encode("utf-8"), context=context)

    def decrypt_text(self, value: bytes, *, context: bytes = b"fast-fr-text") -> str:
        return self.decrypt(value, context=context).decode("utf-8")


password_hasher = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    try:
        return password_hasher.verify(stored_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


@dataclass(slots=True)
class AppSession:
    token: str
    user_id: int
    csrf_token: str
    expires_at: datetime


class SessionStore:
    def __init__(self, lifetime_hours: int = settings.session_hours) -> None:
        self.lifetime = timedelta(hours=lifetime_hours)
        self._sessions: dict[str, AppSession] = {}
        self._lock = threading.Lock()

    def create(self, user_id: int) -> AppSession:
        now = datetime.now(timezone.utc)
        session = AppSession(
            token=secrets.token_urlsafe(32),
            user_id=user_id,
            csrf_token=secrets.token_urlsafe(24),
            expires_at=now + self.lifetime,
        )
        with self._lock:
            self._purge(now)
            self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> AppSession | None:
        if not token:
            return None
        now = datetime.now(timezone.utc)
        with self._lock:
            session = self._sessions.get(token)
            if not session or session.expires_at <= now:
                self._sessions.pop(token, None)
                return None
            return session

    def delete(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def _purge(self, now: datetime) -> None:
        expired = [token for token, item in self._sessions.items() if item.expires_at <= now]
        for token in expired:
            self._sessions.pop(token, None)


def stable_secret(key: bytes, purpose: str) -> str:
    digest = hmac.new(key, purpose.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


crypto = CryptoManager()
sessions = SessionStore()


from __future__ import annotations

import secrets
from pathlib import Path

from .config import settings
from .security import CryptoManager, crypto


class EncryptedBlobStore:
    def __init__(self, root: Path | None = None, manager: CryptoManager | None = None) -> None:
        self.root = root or settings.blob_dir
        self.crypto = manager or crypto
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, *, namespace: str) -> str:
        key = f"{namespace}-{secrets.token_hex(16)}.bin"
        path = self._path(key)
        path.write_bytes(self.crypto.encrypt(data, context=key.encode()))
        return key

    def get(self, key: str) -> bytes:
        path = self._path(key)
        return self.crypto.decrypt(path.read_bytes(), context=key.encode())

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.exists():
            path.unlink()

    def size_bytes(self) -> int:
        return sum(path.stat().st_size for path in self.root.glob("*.bin") if path.is_file())

    def _path(self, key: str) -> Path:
        if Path(key).name != key or not key.endswith(".bin"):
            raise ValueError("Invalid blob key")
        return self.root / key


blobs = EncryptedBlobStore()

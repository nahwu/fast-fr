from __future__ import annotations

import io
from pathlib import Path

import numpy as np
import pytest
from cryptography.exceptions import InvalidTag
from PIL import Image
from sqlalchemy.exc import DatabaseError
from sqlalchemy.orm import sessionmaker

from fastfr.cameras import (
    CameraRuntime,
    CameraService,
    classify_match,
    passage_is_suppressed,
)
from fastfr.db import build_engine, init_database
from fastfr.enrollment import decode_pages, suggest_name
from fastfr.models import AuditLog, EventType
from fastfr.recognition import IndexItem, MatchResult, RecognitionIndex, normalize_embedding
from fastfr.security import CryptoManager, hash_password, verify_password
from fastfr.storage import EncryptedBlobStore


def vector(*values: float) -> np.ndarray:
    result = np.zeros(128, dtype=np.float32)
    result[: len(values)] = values
    return normalize_embedding(result)


def test_filename_and_upload_validation() -> None:
    assert suggest_name("0123_jane-doe.PNG") == "0123 jane doe"
    assert suggest_name("___.jpg") == "New identity"
    with pytest.raises(ValueError, match="Only JPG"):
        decode_pages("face.gif", b"not-an-image")
    with pytest.raises(ValueError, match="could not be decoded"):
        decode_pages("broken.jpg", b"not-an-image")

    image = Image.new("RGB", (40, 30), "white")
    content = io.BytesIO()
    image.save(content, "PNG")
    decoded = decode_pages("candidate.png", content.getvalue())
    assert len(decoded) == 1
    assert decoded[0].shape == (30, 40, 3)


def test_crypto_passwords_and_encrypted_blob_store(tmp_path: Path) -> None:
    manager = CryptoManager(key=b"k" * 32, key_path=tmp_path / "unused")
    encrypted = manager.encrypt(b"passport-face", context=b"sample:7")
    assert encrypted != b"passport-face"
    assert manager.decrypt(encrypted, context=b"sample:7") == b"passport-face"
    with pytest.raises(InvalidTag):
        manager.decrypt(encrypted, context=b"sample:8")

    store = EncryptedBlobStore(tmp_path / "blobs", manager)
    key = store.put(b"captured-face", namespace="event")
    assert store.get(key) == b"captured-face"
    assert b"captured-face" not in (tmp_path / "blobs" / key).read_bytes()
    with pytest.raises(ValueError, match="Invalid blob key"):
        store.get("../master.key")

    password_hash = hash_password("a sufficiently long password")
    assert verify_password(password_hash, "a sufficiently long password")
    assert not verify_password(password_hash, "wrong password")


def test_similarity_threshold_multiple_samples_and_blacklist_precedence() -> None:
    index = RecognitionIndex()
    index.replace(
        [vector(1, 0), vector(0.95, 0.31), vector(0.9, 0.43589)],
        [
            IndexItem(1, "Candidate A", "A1", "whitelist"),
            IndexItem(1, "Candidate A", "A1", "whitelist"),
            IndexItem(2, "Blocked person", None, "blacklist"),
        ],
    )
    result = index.search(vector(1, 0), threshold=0.45)
    assert result.identity_id == 2
    assert result.category == "blacklist"

    result = index.search(vector(0, 1), threshold=0.95)
    assert result.identity_id is None
    assert result.score is not None
    assert index.sample_count == 3
    with pytest.raises(ValueError, match="zero-length"):
        normalize_embedding(np.zeros(128, dtype=np.float32))


@pytest.mark.parametrize(
    ("match", "members", "expected"),
    [
        (MatchResult(9, "Blocked", None, "blacklist", 0.8), {9}, EventType.BLACKLIST.value),
        (MatchResult(2, "Roster", "C2", "whitelist", 0.8), {2}, EventType.MATCH.value),
        (
            MatchResult(3, "Other candidate", "C3", "whitelist", 0.8),
            {2},
            EventType.NOT_REGISTERED.value,
        ),
        (MatchResult(None, None, None, None, 0.2), {2}, EventType.UNKNOWN.value),
    ],
)
def test_policy_precedence(match: MatchResult, members: set[int], expected: str) -> None:
    assert classify_match(match, members) == expected


def test_passage_suppression_and_frame_utilities() -> None:
    runtime = CameraRuntime(1, "Gate 1", "rtsp://example", 0, [], 1)
    runtime.recent_decisions["identity:7"] = 100.0
    assert passage_is_suppressed(runtime, "identity:7", 114.99)
    assert not passage_is_suppressed(runtime, "identity:7", 115.0)
    assert not passage_is_suppressed(runtime, "identity:8", 101.0)

    image = np.zeros((10, 20, 3), dtype=np.uint8)
    assert CameraService._rotate(image, 90).shape == (20, 10, 3)
    image[:] = 255
    masked = CameraService._apply_roi(image, [[0, 0], [0.5, 0], [0.5, 1], [0, 1]])
    assert masked[:, :8].mean() > 200
    assert masked[:, 15:].mean() == 0


def test_sqlite_wal_and_append_only_audit(tmp_path: Path) -> None:
    database = tmp_path / "test.sqlite3"
    engine = build_engine(f"sqlite:///{database.as_posix()}")
    init_database(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as db:
        row = AuditLog(action="test.created", entity_type="test", detail_json="{}")
        db.add(row)
        db.commit()
        row.action = "test.changed"
        with pytest.raises(DatabaseError, match="append-only"):
            db.commit()
        db.rollback()
    with engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar().lower() == "wal"

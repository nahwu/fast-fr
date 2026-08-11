from __future__ import annotations

import base64
import io
import re
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from .config import settings
from .recognition import FaceAnalysis, FaceEngine, face_engine


@dataclass(slots=True)
class StagedFace:
    page: int
    crop_jpeg: bytes
    embedding: np.ndarray
    quality_score: float
    detection_score: float
    duplicate_name: str | None = None
    duplicate_score: float | None = None


@dataclass(slots=True)
class StagedFile:
    filename: str
    suggested_name: str
    faces: list[StagedFace]
    expires_at: float


class EnrollmentStaging:
    def __init__(self, engine: FaceEngine | None = None, ttl_seconds: int = 1800) -> None:
        self.engine = engine or face_engine
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, StagedFile] = {}
        self._lock = threading.Lock()

    def analyze(self, filename: str, content: bytes) -> dict:
        if len(content) > settings.max_upload_bytes:
            raise ValueError("File exceeds the 25 MB limit")
        pages = decode_pages(filename, content)
        faces: list[StagedFace] = []
        for page_no, image in enumerate(pages, start=1):
            for analysis in self.engine.analyze_all(image, include_ineligible=True):
                faces.append(self._to_staged(page_no, analysis))
        token = secrets.token_urlsafe(24)
        item = StagedFile(
            filename=filename,
            suggested_name=suggest_name(filename),
            faces=faces,
            expires_at=time.time() + self.ttl_seconds,
        )
        with self._lock:
            self._purge()
            self._items[token] = item
        return {
            "token": token,
            "filename": filename,
            "suggestedName": item.suggested_name,
            "status": "ready" if any(face.quality_score >= 0.55 for face in faces) else "review",
            "faces": [
                {
                    "index": index,
                    "page": face.page,
                    "quality": round(face.quality_score, 3),
                    "eligible": face.quality_score >= 0.55,
                    "possibleDuplicate": face.duplicate_name,
                    "duplicateScore": round(face.duplicate_score, 3)
                    if face.duplicate_score is not None
                    else None,
                    "preview": "data:image/jpeg;base64,"
                    + base64.b64encode(face.crop_jpeg).decode("ascii"),
                }
                for index, face in enumerate(faces)
            ],
            "message": "No face detected" if not faces else None,
        }

    def take(self, token: str, face_index: int) -> tuple[StagedFile, StagedFace]:
        with self._lock:
            self._purge()
            item = self._items.pop(token, None)
        if not item:
            raise ValueError("Enrollment review expired; upload the file again")
        if face_index < 0 or face_index >= len(item.faces):
            raise ValueError("Select a valid detected face")
        face = item.faces[face_index]
        if face.quality_score < 0.55:
            raise ValueError("Selected face does not meet the enrollment quality requirement")
        return item, face

    def _purge(self) -> None:
        now = time.time()
        for token in [key for key, value in self._items.items() if value.expires_at <= now]:
            self._items.pop(token, None)

    def _to_staged(self, page: int, analysis: FaceAnalysis) -> StagedFace:
        duplicate = self.engine.index.search(analysis.embedding, 0.55)
        return StagedFace(
            page=page,
            crop_jpeg=analysis.crop_jpeg,
            embedding=analysis.embedding,
            quality_score=analysis.detection.quality_score,
            detection_score=analysis.detection.score,
            duplicate_name=duplicate.display_name if duplicate.identity_id else None,
            duplicate_score=duplicate.score if duplicate.identity_id else None,
        )


def suggest_name(filename: str) -> str:
    stem = Path(filename).stem
    cleaned = re.sub(r"[_-]+", " ", stem)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or "New identity"


def decode_pages(filename: str, content: bytes) -> list[np.ndarray]:
    suffix = Path(filename).suffix.lower()
    if suffix in {".jpg", ".jpeg", ".png"}:
        try:
            image = Image.open(io.BytesIO(content))
            if image.width * image.height > settings.max_image_pixels:
                raise ValueError("Image dimensions exceed the 40-megapixel limit")
            image = ImageOps.exif_transpose(image).convert("RGB")
            return [cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Image file could not be decoded") from exc
    if suffix == ".pdf":
        try:
            document = pymupdf.open(stream=content, filetype="pdf")
        except Exception as exc:
            raise ValueError("PDF file could not be opened") from exc
        if document.page_count > settings.max_pdf_pages:
            document.close()
            raise ValueError(f"PDF exceeds the {settings.max_pdf_pages}-page limit")
        pages: list[np.ndarray] = []
        total_pixels = 0
        try:
            for page in document:
                pixmap = page.get_pixmap(matrix=pymupdf.Matrix(2, 2), alpha=False)
                total_pixels += pixmap.width * pixmap.height
                if total_pixels > settings.max_pdf_total_pixels:
                    raise ValueError("Rasterized PDF exceeds the 60-megapixel total limit")
                array = np.frombuffer(pixmap.samples, dtype=np.uint8).reshape(
                    pixmap.height, pixmap.width, pixmap.n
                )
                if pixmap.n == 4:
                    array = cv2.cvtColor(array, cv2.COLOR_RGBA2BGR)
                else:
                    array = cv2.cvtColor(array, cv2.COLOR_RGB2BGR)
                pages.append(array)
        finally:
            document.close()
        return pages
    raise ValueError("Only JPG, PNG, and PDF files are supported")


staging = EnrollmentStaging()

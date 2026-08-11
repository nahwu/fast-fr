from __future__ import annotations

import logging
import math
import ctypes
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from .config import settings

logger = logging.getLogger(__name__)

try:
    import onnxruntime as ort
except ImportError:  # pragma: no cover - packaging guard
    ort = None  # type: ignore[assignment]


def _cuda_runtime_detected() -> bool:
    if sys.platform != "win32":
        return True
    for library in ("cublasLt64_13.dll", "cublasLt64_12.dll", "cublasLt64_11.dll"):
        try:
            ctypes.WinDLL(library)  # type: ignore[attr-defined]
            return True
        except OSError:
            continue
    return False


@dataclass(slots=True)
class FaceDetection:
    box: tuple[float, float, float, float]
    landmarks: np.ndarray
    score: float
    quality_score: float
    eligible: bool
    quality_message: str | None = None


@dataclass(slots=True)
class FaceAnalysis:
    detection: FaceDetection
    aligned_bgr: np.ndarray
    crop_jpeg: bytes
    embedding: np.ndarray


@dataclass(slots=True)
class MatchResult:
    identity_id: int | None
    display_name: str | None
    candidate_no: str | None
    category: str | None
    score: float | None


@dataclass(slots=True)
class IndexItem:
    identity_id: int
    display_name: str
    candidate_no: str | None
    category: str


class RecognitionIndex:
    """Thread-safe exact cosine index; 20k x 128 stays small and predictable."""

    def __init__(self) -> None:
        self._matrix = np.empty((0, 128), dtype=np.float32)
        self._blacklist_mask = np.empty((0,), dtype=np.bool_)
        self._items: list[IndexItem] = []
        self._lock = threading.RLock()

    def replace(self, embeddings: list[np.ndarray], items: list[IndexItem]) -> None:
        if len(embeddings) != len(items):
            raise ValueError("Embedding metadata length mismatch")
        matrix = (
            np.vstack([normalize_embedding(value) for value in embeddings]).astype(np.float32)
            if embeddings
            else np.empty((0, 128), dtype=np.float32)
        )
        with self._lock:
            self._matrix = matrix
            self._items = list(items)
            self._blacklist_mask = np.asarray(
                [item.category == "blacklist" for item in items], dtype=np.bool_
            )

    def search(self, embedding: np.ndarray, threshold: float) -> MatchResult:
        query = normalize_embedding(embedding)
        with self._lock:
            if not self._items:
                return MatchResult(None, None, None, None, None)
            scores = self._matrix @ query
            blacklist_indexes = np.flatnonzero(self._blacklist_mask & (scores >= threshold))
            index = (
                int(blacklist_indexes[int(np.argmax(scores[blacklist_indexes]))])
                if blacklist_indexes.size
                else int(np.argmax(scores))
            )
            score = float(scores[index])
            if score < threshold:
                return MatchResult(None, None, None, None, score)
            item = self._items[index]
            return MatchResult(
                item.identity_id,
                item.display_name,
                item.candidate_no,
                item.category,
                score,
            )

    @property
    def sample_count(self) -> int:
        with self._lock:
            return len(self._items)


def normalize_embedding(value: np.ndarray) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(vector))
    if not math.isfinite(norm) or norm < 1e-9:
        raise ValueError("Invalid zero-length embedding")
    return vector / norm


class FaceEngine:
    input_size = 640
    strides = (8, 16, 32)
    output_names = (
        "cls_8", "cls_16", "cls_32",
        "obj_8", "obj_16", "obj_32",
        "bbox_8", "bbox_16", "bbox_32",
        "kps_8", "kps_16", "kps_32",
    )
    landmark_template = np.array(
        [
            [38.2946, 51.6963],
            [73.5318, 51.5014],
            [56.0252, 71.7366],
            [41.5493, 92.3655],
            [70.7299, 92.2041],
        ],
        dtype=np.float32,
    )

    def __init__(
        self,
        yunet_path: Path | None = None,
        sface_path: Path | None = None,
        detection_threshold: float = 0.9,
    ) -> None:
        self.yunet_path = yunet_path or settings.yunet_path
        self.sface_path = sface_path or settings.sface_path
        self.detection_threshold = detection_threshold
        self.index = RecognitionIndex()
        self.provider = "unavailable"
        self.cuda_available = False
        self.error: str | None = None
        self._detector = None
        self._recognizer = None
        self._inference_lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if ort is None:
            self.error = "ONNX Runtime is not installed"
            return
        if not self.yunet_path.exists() or not self.sface_path.exists():
            self.error = "Recognition model files are missing"
            return

        session_options = ort.SessionOptions()
        session_options.log_severity_level = 3
        session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        available = ort.get_available_providers()
        preferred: list[str] = []
        if "CUDAExecutionProvider" in available and _cuda_runtime_detected():
            preferred.append("CUDAExecutionProvider")
        preferred.append("CPUExecutionProvider")
        try:
            self._detector = ort.InferenceSession(
                str(self.yunet_path), sess_options=session_options, providers=preferred
            )
            self._recognizer = ort.InferenceSession(
                str(self.sface_path), sess_options=session_options, providers=preferred
            )
            self._self_test()
            active = self._recognizer.get_providers()[0]
            self.cuda_available = active == "CUDAExecutionProvider"
            self.provider = "NVIDIA CUDA" if self.cuda_available else "CPU fallback"
        except Exception as exc:  # pragma: no cover - hardware dependent
            logger.warning("GPU model initialization failed, trying CPU: %s", exc)
            try:
                self._detector = ort.InferenceSession(
                    str(self.yunet_path), sess_options=session_options, providers=["CPUExecutionProvider"]
                )
                self._recognizer = ort.InferenceSession(
                    str(self.sface_path), sess_options=session_options, providers=["CPUExecutionProvider"]
                )
                self._self_test()
                self.provider = "CPU fallback"
            except Exception as cpu_exc:
                self.error = f"Model initialization failed: {cpu_exc}"

    def _self_test(self) -> None:
        detector_input = self._detector.get_inputs()[0].name
        detector_outputs = self._detector.run(
            None, {detector_input: np.zeros((1, 3, self.input_size, self.input_size), np.float32)}
        )
        if len(detector_outputs) != 12:
            raise RuntimeError("YuNet self-test returned unexpected outputs")
        recognizer_input = self._recognizer.get_inputs()[0].name
        embedding = np.asarray(
            self._recognizer.run(
                None, {recognizer_input: np.zeros((1, 3, 112, 112), np.float32)}
            )[0]
        ).reshape(-1)
        if embedding.size != 128 or not np.isfinite(embedding).all():
            raise RuntimeError("SFace self-test returned an invalid embedding")

    @property
    def ready(self) -> bool:
        return self._detector is not None and self._recognizer is not None

    def detect(self, image_bgr: np.ndarray) -> list[FaceDetection]:
        if not self.ready:
            raise RuntimeError(self.error or "Recognition engine is unavailable")
        if image_bgr is None or image_bgr.size == 0:
            return []

        height, width = image_bgr.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized_w, resized_h = max(1, int(width * scale)), max(1, int(height * scale))
        resized = cv2.resize(image_bgr, (resized_w, resized_h), interpolation=cv2.INTER_LINEAR)
        padded = np.zeros((self.input_size, self.input_size, 3), dtype=np.uint8)
        padded[:resized_h, :resized_w] = resized
        blob = cv2.dnn.blobFromImage(padded).astype(np.float32)

        with self._inference_lock:
            outputs = self._detector.run(list(self.output_names), {"input": blob})
        raw = self._postprocess(outputs)
        detections: list[FaceDetection] = []
        for face in raw:
            face[:14] /= scale
            x, y, box_w, box_h = face[:4]
            landmarks = face[4:14].reshape(5, 2)
            quality, eligible, message = self._quality(image_bgr, x, y, box_w, box_h, landmarks)
            detections.append(
                FaceDetection(
                    box=(float(x), float(y), float(box_w), float(box_h)),
                    landmarks=landmarks.astype(np.float32),
                    score=float(face[14]),
                    quality_score=quality,
                    eligible=eligible,
                    quality_message=message,
                )
            )
        return detections

    def analyze(self, image_bgr: np.ndarray, detection: FaceDetection) -> FaceAnalysis:
        aligned = self.align(image_bgr, detection.landmarks)
        embedding = self.embed_aligned(aligned)
        preview = cv2.resize(aligned, (256, 256), interpolation=cv2.INTER_CUBIC)
        ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 90])
        if not ok:
            raise RuntimeError("Could not encode face crop")
        return FaceAnalysis(detection, aligned, encoded.tobytes(), embedding)

    def analyze_all(self, image_bgr: np.ndarray, *, include_ineligible: bool = False) -> list[FaceAnalysis]:
        detections = self.detect(image_bgr)
        analyses: list[FaceAnalysis] = []
        for detection in detections:
            if detection.eligible or include_ineligible:
                analyses.append(self.analyze(image_bgr, detection))
        return analyses

    def align(self, image_bgr: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        transform, _inliers = cv2.estimateAffinePartial2D(
            np.asarray(landmarks, dtype=np.float32),
            self.landmark_template,
            method=cv2.LMEDS,
        )
        if transform is None:
            raise ValueError("Face landmarks could not be aligned")
        return cv2.warpAffine(
            image_bgr,
            transform,
            (112, 112),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )

    def embed_aligned(self, aligned_bgr: np.ndarray) -> np.ndarray:
        blob = cv2.dnn.blobFromImage(
            aligned_bgr,
            scalefactor=1.0,
            size=(112, 112),
            mean=(0, 0, 0),
            swapRB=True,
            crop=False,
        ).astype(np.float32)
        with self._inference_lock:
            result = self._recognizer.run(["fc1"], {"data": blob})[0]
        return normalize_embedding(result)

    def _postprocess(self, outputs: list[np.ndarray]) -> np.ndarray:
        faces: list[list[float]] = []
        for index, stride in enumerate(self.strides):
            cols = self.input_size // stride
            cls = outputs[index].reshape(-1)
            obj = outputs[index + 3].reshape(-1)
            bbox = outputs[index + 6].reshape(-1, 4)
            kps = outputs[index + 9].reshape(-1, 10)
            scores = np.sqrt(np.clip(cls, 0, 1) * np.clip(obj, 0, 1))
            candidates = np.flatnonzero(scores >= self.detection_threshold)
            for flat_index in candidates:
                row, col = divmod(int(flat_index), cols)
                cx = (col + bbox[flat_index, 0]) * stride
                cy = (row + bbox[flat_index, 1]) * stride
                box_w = math.exp(float(bbox[flat_index, 2])) * stride
                box_h = math.exp(float(bbox[flat_index, 3])) * stride
                values = [cx - box_w / 2, cy - box_h / 2, box_w, box_h]
                for point in range(5):
                    values.extend(
                        [
                            (kps[flat_index, point * 2] + col) * stride,
                            (kps[flat_index, point * 2 + 1] + row) * stride,
                        ]
                    )
                values.append(float(scores[flat_index]))
                faces.append(values)
        if not faces:
            return np.empty((0, 15), dtype=np.float32)
        array = np.asarray(faces, dtype=np.float32)
        keep = cv2.dnn.NMSBoxes(
            array[:, :4].tolist(),
            array[:, 14].tolist(),
            self.detection_threshold,
            0.3,
            eta=1.0,
            top_k=500,
        )
        return array[np.asarray(keep, dtype=np.int32).reshape(-1)] if len(keep) else array[:0]

    @staticmethod
    def _quality(
        image: np.ndarray,
        x: float,
        y: float,
        width: float,
        height: float,
        landmarks: np.ndarray,
    ) -> tuple[float, bool, str | None]:
        if width < 80 or height < 80:
            return 0.2, False, "Move closer to the camera"
        x1, y1 = max(0, int(x)), max(0, int(y))
        x2, y2 = min(image.shape[1], int(x + width)), min(image.shape[0], int(y + height))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            return 0.0, False, "Position face inside the guide"
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
        if blur < 55:
            return min(0.45, blur / 120), False, "Hold still for a clear image"
        eye_distance = float(np.linalg.norm(landmarks[0] - landmarks[1]))
        eye_tilt = abs(float(landmarks[0, 1] - landmarks[1, 1])) / max(height, 1)
        if eye_distance / max(width, 1) < 0.2 or eye_tilt > 0.14:
            return 0.5, False, "Look directly at the camera"
        quality = min(1.0, 0.55 + min(blur, 250) / 700 + min(width, 240) / 800)
        return quality, True, None


face_engine = FaceEngine()


def refresh_index_from_database() -> int:
    """Rebuild the exact in-memory index after enrollment or identity changes."""
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import FaceSample, Identity
    from .security import crypto

    embeddings: list[np.ndarray] = []
    items: list[IndexItem] = []
    with SessionLocal() as db:
        rows = db.execute(
            select(FaceSample, Identity)
            .join(Identity, Identity.id == FaceSample.identity_id)
            .where(Identity.active.is_(True))
            .order_by(FaceSample.id)
        ).all()
        for sample, identity in rows:
            try:
                raw = crypto.decrypt(
                    sample.embedding_enc,
                    context=f"embedding:{sample.id}".encode(),
                )
                embedding = np.frombuffer(raw, dtype=np.float32).copy()
                embeddings.append(embedding)
                items.append(
                    IndexItem(
                        identity_id=identity.id,
                        display_name=identity.display_name,
                        candidate_no=identity.candidate_no,
                        category=identity.category,
                    )
                )
            except Exception:
                logger.exception("Skipping unreadable face sample %s", sample.id)
    face_engine.index.replace(embeddings, items)
    return len(items)

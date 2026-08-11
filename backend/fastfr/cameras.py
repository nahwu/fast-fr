from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

import cv2
import numpy as np
from sqlalchemy import select

from .db import SessionLocal
from .models import (
    Attendance,
    AttendanceSource,
    Camera,
    EventType,
    ExamSession,
    IdentityCategory,
    RecognitionEvent,
    SessionMember,
    SessionStatus,
)
from .recognition import FaceAnalysis, MatchResult, face_engine
from .realtime import event_hub
from .security import crypto
from .storage import blobs
from .telegram import telegram_service

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class CameraRuntime:
    camera_id: int
    name: str
    url: str
    rotation: int
    roi: list[list[float]]
    session_id: int | None
    stop: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    frame: np.ndarray | None = None
    frame_received_at: float = 0.0
    frame_received_utc: datetime | None = None
    stream_read_ms: float = 0.0
    preview_jpeg: bytes | None = None
    connected: bool = False
    status: str = "connecting"
    message: str = "Connecting"
    fps: float = 0.0
    last_inference_at: float = 0.0
    last_face_at: float = 0.0
    passage_created: bool = False
    votes: deque[str] = field(default_factory=lambda: deque(maxlen=3))
    vote_results: dict[str, tuple[MatchResult, FaceAnalysis, float]] = field(default_factory=dict)
    recent_decisions: dict[str, float] = field(default_factory=dict)
    thread: threading.Thread | None = None


def classify_match(match: MatchResult, member_ids: set[int]) -> str:
    """Apply recognition policy in its safety-critical order."""
    if match.identity_id and match.category == IdentityCategory.BLACKLIST.value:
        return EventType.BLACKLIST.value
    if match.identity_id and match.identity_id in member_ids:
        return EventType.MATCH.value
    if match.identity_id:
        return EventType.NOT_REGISTERED.value
    return EventType.UNKNOWN.value


def passage_is_suppressed(
    runtime: CameraRuntime,
    vote_key: str,
    now: float,
    window_seconds: float = 15.0,
) -> bool:
    return now - runtime.recent_decisions.get(vote_key, -window_seconds * 2) < window_seconds


class CameraService:
    def __init__(self) -> None:
        self._runtimes: dict[int, CameraRuntime] = {}
        self._lock = threading.RLock()
        self._scheduler: threading.Thread | None = None
        self._stop = threading.Event()
        self._event_callbacks: list[Callable[[int], None]] = []
        self._blank_jpeg = self._make_blank_preview()

    def start(self) -> None:
        os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
        self._stop.clear()
        self.reload()
        if not self._scheduler or not self._scheduler.is_alive():
            self._scheduler = threading.Thread(
                target=self._inference_loop, name="recognition-scheduler", daemon=True
            )
            self._scheduler.start()

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            runtimes = list(self._runtimes.values())
            self._runtimes.clear()
        for runtime in runtimes:
            runtime.stop.set()
            if runtime.thread:
                runtime.thread.join(timeout=2)
        if self._scheduler:
            self._scheduler.join(timeout=3)

    def reload(self) -> None:
        with SessionLocal() as db:
            rows = db.scalars(select(Camera).where(Camera.enabled.is_(True)).limit(4)).all()
            desired: dict[int, CameraRuntime] = {}
            for row in rows:
                try:
                    url = crypto.decrypt_text(row.rtsp_url_enc, context=f"camera:{row.id}".encode())
                except Exception:
                    logger.exception("Could not decrypt camera %s URL", row.id)
                    continue
                desired[row.id] = CameraRuntime(
                    camera_id=row.id,
                    name=row.name,
                    url=url,
                    rotation=row.rotation,
                    roi=json.loads(row.roi_json or "[]"),
                    session_id=row.session_id,
                )

        with self._lock:
            old = list(self._runtimes.values())
            self._runtimes = desired
        for runtime in old:
            runtime.stop.set()
            if runtime.thread:
                runtime.thread.join(timeout=1)
        for runtime in desired.values():
            runtime.thread = threading.Thread(
                target=self._capture_loop,
                args=(runtime,),
                name=f"camera-{runtime.camera_id}",
                daemon=True,
            )
            runtime.thread.start()

    def add_event_callback(self, callback: Callable[[int], None]) -> None:
        self._event_callbacks.append(callback)

    def get_preview(self, camera_id: int) -> bytes:
        with self._lock:
            runtime = self._runtimes.get(camera_id)
        if not runtime:
            return self._blank_jpeg
        with runtime.lock:
            return runtime.preview_jpeg or self._blank_jpeg

    def health(self) -> list[dict]:
        with self._lock:
            runtimes = list(self._runtimes.values())
        return [self._health_payload(runtime) for runtime in runtimes]

    def test_connection(self, url: str, timeout_seconds: int = 8) -> dict:
        started = time.perf_counter()
        capture = cv2.VideoCapture()
        try:
            capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, timeout_seconds * 1000)
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, timeout_seconds * 1000)
            capture.open(url, cv2.CAP_FFMPEG)
            ok, frame = capture.read()
            if not ok or frame is None:
                return {"ok": False, "message": "No video frame received"}
            return {
                "ok": True,
                "message": "Camera connected",
                "width": int(frame.shape[1]),
                "height": int(frame.shape[0]),
                "latencyMs": round((time.perf_counter() - started) * 1000),
            }
        finally:
            capture.release()

    def _capture_loop(self, runtime: CameraRuntime) -> None:
        retry = 1.0
        while not self._stop.is_set() and not runtime.stop.is_set():
            capture = cv2.VideoCapture()
            capture.set(cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, 5000)
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 5000)
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            capture.open(runtime.url, cv2.CAP_FFMPEG)
            if not capture.isOpened():
                self._set_health(runtime, False, "offline", "Could not open RTSP stream")
                runtime.stop.wait(retry)
                retry = min(retry * 2, 15)
                continue
            retry = 1.0
            self._set_health(runtime, True, "online", "Live")
            count = 0
            window_started = time.perf_counter()
            last_preview = 0.0
            while not self._stop.is_set() and not runtime.stop.is_set():
                read_started = time.perf_counter()
                ok, frame = capture.read()
                runtime.stream_read_ms = (time.perf_counter() - read_started) * 1000
                if not ok or frame is None:
                    self._set_health(runtime, False, "reconnecting", "Stream interrupted")
                    break
                frame = self._rotate(frame, runtime.rotation)
                now = time.perf_counter()
                count += 1
                if now - window_started >= 2:
                    runtime.fps = count / (now - window_started)
                    count = 0
                    window_started = now
                preview = None
                if now - last_preview >= 0.125:
                    preview = self._encode_preview(frame, runtime.roi)
                    last_preview = now
                with runtime.lock:
                    runtime.frame = frame
                    runtime.frame_received_at = now
                    runtime.frame_received_utc = datetime.now(timezone.utc)
                    if preview:
                        runtime.preview_jpeg = preview
            capture.release()
        self._set_health(runtime, False, "stopped", "Camera stopped")

    def _inference_loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                runtimes = list(self._runtimes.values())
            now = time.perf_counter()
            for runtime in runtimes:
                if not runtime.connected or now - runtime.last_inference_at < 0.2:
                    continue
                runtime.last_inference_at = now
                try:
                    self._process_latest(runtime)
                except Exception:
                    logger.exception("Recognition failed for camera %s", runtime.camera_id)
                    self._set_health(runtime, runtime.connected, "degraded", "Recognition error")
            self._stop.wait(0.025)

    def _process_latest(self, runtime: CameraRuntime) -> None:
        with runtime.lock:
            frame = None if runtime.frame is None else runtime.frame.copy()
            received_at = runtime.frame_received_at
        if frame is None or runtime.session_id is None:
            return
        with SessionLocal() as db:
            session = db.get(ExamSession, runtime.session_id)
            if not session or session.status != SessionStatus.ACTIVE.value:
                runtime.status = "waiting"
                runtime.message = "Session is not active"
                return
        roi_frame = self._apply_roi(frame, runtime.roi)
        detections = face_engine.detect(roi_frame)
        eligible = [detection for detection in detections if detection.eligible]
        if not eligible:
            if time.perf_counter() - runtime.last_face_at > 1.0:
                runtime.votes.clear()
                runtime.vote_results.clear()
                runtime.passage_created = False
            if detections:
                runtime.status = "position"
                runtime.message = detections[0].quality_message or "Position face"
            else:
                runtime.status = "ready"
                runtime.message = "Ready for next person"
            return
        runtime.last_face_at = time.perf_counter()
        if len(eligible) > 1:
            runtime.status = "multiple"
            runtime.message = "One person at a time"
            runtime.votes.clear()
            return
        if runtime.passage_created:
            return
        detection = max(eligible, key=lambda item: item.box[2] * item.box[3])
        analysis = face_engine.analyze(roi_frame, detection)
        threshold = self._recognition_threshold()
        match = face_engine.index.search(analysis.embedding, threshold)
        vote_key = f"identity:{match.identity_id}" if match.identity_id else "unknown"
        runtime.votes.append(vote_key)
        runtime.vote_results.setdefault(vote_key, (match, analysis, received_at))
        if list(runtime.votes).count(vote_key) < 2:
            runtime.status = "checking"
            runtime.message = "Checking identity"
            return
        now = time.perf_counter()
        if passage_is_suppressed(runtime, vote_key, now):
            runtime.status = "suppressed"
            runtime.message = "Passage already recorded"
            runtime.passage_created = True
            return
        event_id = self._create_event(runtime, *runtime.vote_results[vote_key])
        runtime.recent_decisions[vote_key] = now
        runtime.recent_decisions = {
            key: created_at
            for key, created_at in runtime.recent_decisions.items()
            if now - created_at < 30.0
        }
        runtime.passage_created = True
        for callback in self._event_callbacks:
            callback(event_id)

    def _create_event(
        self,
        runtime: CameraRuntime,
        match: MatchResult,
        analysis: FaceAnalysis,
        received_at: float,
    ) -> int:
        captured_at = datetime.now(timezone.utc)
        with SessionLocal() as db:
            member_ids = set(
                db.scalars(
                    select(SessionMember.identity_id).where(
                        SessionMember.session_id == runtime.session_id
                    )
                ).all()
            )
            event_type = classify_match(match, member_ids)
            snapshot_key = blobs.put(analysis.crop_jpeg, namespace="event")
            event = RecognitionEvent(
                event_type=event_type,
                camera_id=runtime.camera_id,
                session_id=runtime.session_id,
                identity_id=match.identity_id,
                identity_name_snapshot=match.display_name,
                candidate_no_snapshot=match.candidate_no,
                identity_category_snapshot=match.category,
                confidence=match.score,
                captured_at=captured_at,
                decision_latency_ms=None,
                snapshot_blob_key=snapshot_key,
            )
            db.add(event)
            db.flush()
            if event_type == EventType.MATCH.value and match.identity_id:
                attendance = db.scalar(
                    select(Attendance).where(
                        Attendance.session_id == runtime.session_id,
                        Attendance.identity_id == match.identity_id,
                    )
                )
                if attendance:
                    attendance.last_seen_at = captured_at
                    attendance.confidence = max(attendance.confidence or 0, match.score or 0)
                else:
                    db.add(
                        Attendance(
                            session_id=runtime.session_id,
                            identity_id=match.identity_id,
                            first_seen_at=captured_at,
                            last_seen_at=captured_at,
                            source=AttendanceSource.AUTO.value,
                            first_event_id=event.id,
                            camera_id=runtime.camera_id,
                            confidence=match.score,
                        )
                    )
            db.commit()
            event.decision_latency_ms = (time.perf_counter() - received_at) * 1000
            db.commit()
            payload = self._event_payload(db, event)
            event_id = event.id
        runtime.status = event_type
        runtime.message = self._event_message(event_type, match.display_name)
        event_hub.publish("recognition.event", payload)
        if telegram_service.should_send(event_type):
            telegram_service.enqueue(event_id)
        return event_id

    @staticmethod
    def _event_payload(db, event: RecognitionEvent) -> dict:  # type: ignore[no-untyped-def]
        camera = db.get(Camera, event.camera_id)
        session = db.get(ExamSession, event.session_id) if event.session_id else None
        return {
            "id": event.id,
            "eventType": event.event_type,
            "cameraId": event.camera_id,
            "cameraName": camera.name if camera else None,
            "sessionId": event.session_id,
            "sessionName": session.name if session else None,
            "identityId": event.identity_id,
            "identityName": event.identity_name_snapshot,
            "candidateNo": event.candidate_no_snapshot,
            "identityCategory": event.identity_category_snapshot,
            "identityFaceUrl": f"/api/identities/{event.identity_id}/face"
            if event.identity_id
            else None,
            "confidence": event.confidence,
            "capturedAt": event.captured_at.isoformat(),
            "decisionLatencyMs": event.decision_latency_ms,
            "snapshotUrl": f"/api/events/{event.id}/snapshot",
            "resolvedIdentityId": event.resolved_identity_id,
            "telegramStatus": event.telegram_status,
        }

    @staticmethod
    def _event_message(event_type: str, name: str | None) -> str:
        if event_type == EventType.MATCH.value:
            return f"MATCH — {name}"
        if event_type == EventType.BLACKLIST.value:
            return f"BLACKLIST — {name}"
        if event_type == EventType.NOT_REGISTERED.value:
            return f"NOT REGISTERED — {name}"
        return "NO MATCH"

    @staticmethod
    def _recognition_threshold() -> float:
        from .models import Setting

        with SessionLocal() as db:
            row = db.get(Setting, "recognition_threshold")
            try:
                return float(row.value) if row and row.value else 0.45
            except ValueError:
                return 0.45

    def _set_health(self, runtime: CameraRuntime, connected: bool, status: str, message: str) -> None:
        changed = runtime.status != status or runtime.connected != connected
        runtime.connected = connected
        runtime.status = status
        runtime.message = message
        if changed:
            event_hub.publish("camera.health", self._health_payload(runtime))

    @staticmethod
    def _health_payload(runtime: CameraRuntime) -> dict:
        return {
            "cameraId": runtime.camera_id,
            "name": runtime.name,
            "connected": runtime.connected,
            "status": runtime.status,
            "message": runtime.message,
            "fps": round(runtime.fps, 1),
            "streamReadMs": round(runtime.stream_read_ms, 1),
            "frameBufferAgeMs": round(
                (time.perf_counter() - runtime.frame_received_at) * 1000, 1
            )
            if runtime.frame_received_at
            else None,
            "sessionId": runtime.session_id,
            "lastFrameAt": runtime.frame_received_utc.isoformat()
            if runtime.frame_received_utc
            else None,
        }

    @staticmethod
    def _rotate(frame: np.ndarray, rotation: int) -> np.ndarray:
        if rotation == 90:
            return cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        if rotation == 180:
            return cv2.rotate(frame, cv2.ROTATE_180)
        if rotation == 270:
            return cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return frame

    @staticmethod
    def _apply_roi(frame: np.ndarray, roi: list[list[float]]) -> np.ndarray:
        if len(roi) < 3:
            return frame
        height, width = frame.shape[:2]
        points = np.asarray(
            [[int(x * width), int(y * height)] for x, y in roi], dtype=np.int32
        )
        mask = np.zeros((height, width), dtype=np.uint8)
        cv2.fillPoly(mask, [points], 255)
        return cv2.bitwise_and(frame, frame, mask=mask)

    @staticmethod
    def _encode_preview(frame: np.ndarray, roi: list[list[float]]) -> bytes | None:
        preview = frame
        if preview.shape[1] > 960:
            scale = 960 / preview.shape[1]
            preview = cv2.resize(preview, (960, int(preview.shape[0] * scale)))
        if len(roi) >= 3:
            height, width = preview.shape[:2]
            points = np.asarray(
                [[int(x * width), int(y * height)] for x, y in roi], dtype=np.int32
            )
            cv2.polylines(preview, [points], True, (63, 226, 168), 2)
        ok, encoded = cv2.imencode(".jpg", preview, [cv2.IMWRITE_JPEG_QUALITY, 72])
        return encoded.tobytes() if ok else None

    @staticmethod
    def _make_blank_preview() -> bytes:
        image = np.zeros((360, 640, 3), dtype=np.uint8)
        image[:] = (20, 24, 31)
        cv2.putText(
            image,
            "CAMERA OFFLINE",
            (185, 185),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.9,
            (160, 170, 185),
            2,
            cv2.LINE_AA,
        )
        ok, encoded = cv2.imencode(".jpg", image)
        return encoded.tobytes() if ok else b""


camera_service = CameraService()
camera_service.add_event_callback(
    lambda event_id: logger.info("Recognition event created: %s", event_id)
)

from __future__ import annotations

import json
import logging
import queue
import threading
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from .db import SessionLocal
from .models import Camera, ExamSession, RecognitionEvent, Setting
from .realtime import event_hub
from .security import crypto
from .storage import blobs

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TelegramJob:
    event_id: int


class TelegramService:
    def __init__(self) -> None:
        self._queue: queue.Queue[TelegramJob | None] = queue.Queue(maxsize=1000)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._worker, name="telegram-alerts", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if self._thread:
            self._thread.join(timeout=3)

    def enqueue(self, event_id: int) -> None:
        try:
            self._queue.put_nowait(TelegramJob(event_id))
            self._set_status(event_id, "queued")
        except queue.Full:
            self._set_status(event_id, "failed")

    def configured(self) -> dict:
        with SessionLocal() as db:
            values = {row.key: row for row in db.scalars(select(Setting)).all()}
            return {
                "enabled": values.get("telegram_enabled", Setting(value="false")).value == "true",
                "tokenSet": bool(values.get("telegram_token") and values["telegram_token"].value_enc),
                "chatIdSet": bool(values.get("telegram_chat_id") and values["telegram_chat_id"].value_enc),
                "alertTypes": json.loads(
                    values.get("telegram_alert_types", Setting(value='["unknown"]')).value
                    or '["unknown"]'
                ),
            }

    def should_send(self, event_type: str) -> bool:
        config = self.configured()
        return bool(config["enabled"] and event_type in config["alertTypes"])

    def send_test(self) -> None:
        token, chat_id = self._credentials()
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": "Fast-FR Telegram alerts are connected."},
            timeout=10,
        )
        response.raise_for_status()

    def _credentials(self) -> tuple[str, str]:
        with SessionLocal() as db:
            token_row = db.get(Setting, "telegram_token")
            chat_row = db.get(Setting, "telegram_chat_id")
            if not token_row or not token_row.value_enc or not chat_row or not chat_row.value_enc:
                raise RuntimeError("Telegram token and chat ID are required")
            return (
                crypto.decrypt_text(token_row.value_enc, context=b"setting:telegram_token"),
                crypto.decrypt_text(chat_row.value_enc, context=b"setting:telegram_chat_id"),
            )

    def _worker(self) -> None:
        while not self._stop.is_set():
            try:
                job = self._queue.get(timeout=1)
            except queue.Empty:
                continue
            if job is None:
                break
            for attempt in range(3):
                try:
                    self._send_event(job.event_id)
                    self._set_status(job.event_id, "sent")
                    break
                except Exception as exc:  # pragma: no cover - external network
                    logger.warning("Telegram delivery attempt %s failed: %s", attempt + 1, exc)
                    if attempt == 2:
                        self._set_status(job.event_id, "failed")
                    elif self._stop.wait(2 ** attempt):
                        break

    def _send_event(self, event_id: int) -> None:
        token, chat_id = self._credentials()
        with SessionLocal() as db:
            event = db.get(RecognitionEvent, event_id)
            if not event:
                return
            camera = db.get(Camera, event.camera_id)
            session = db.get(ExamSession, event.session_id) if event.session_id else None
            image = blobs.get(event.snapshot_blob_key)
            caption = "\n".join(
                [
                    f"FAST-FR ALERT: {event.event_type.replace('_', ' ').upper()}",
                    f"Time: {event.captured_at.astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')}",
                    f"Session: {session.name if session else 'None'}",
                    f"Camera: {camera.name if camera else event.camera_id}",
                ]
            )
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data={"chat_id": chat_id, "caption": caption},
            files={"photo": ("unknown-face.jpg", image, "image/jpeg")},
            timeout=15,
        )
        response.raise_for_status()

    def _set_status(self, event_id: int, status: str) -> None:
        with SessionLocal() as db:
            event = db.get(RecognitionEvent, event_id)
            if event:
                event.telegram_status = status
                db.commit()
        event_hub.publish("telegram.delivery", {"eventId": event_id, "status": status})


telegram_service = TelegramService()


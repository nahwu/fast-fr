from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Annotated, Literal

import numpy as np
from fastapi import (
    APIRouter,
    Depends,
    File,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from .audit import record_audit
from .cameras import camera_service
from .config import settings
from .db import SessionLocal, get_db
from .enrollment import staging
from .models import (
    Attendance,
    AttendanceSource,
    AuditLog,
    Camera,
    EventType,
    ExamSession,
    FaceSample,
    Identity,
    IdentityCategory,
    RecognitionEvent,
    Role,
    SessionMember,
    SessionStatus,
    Setting,
    User,
    utcnow,
)
from .recognition import face_engine, refresh_index_from_database
from .realtime import event_hub
from .reporting import build_session_report
from .security import AppSession, crypto, hash_password, sessions, verify_password
from .storage import blobs
from .telegram import telegram_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")
SESSION_COOKIE = "fastfr_session"


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SetupAdminRequest(ApiModel):
    username: str = Field(min_length=3, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    displayName: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=200)


class LoginRequest(ApiModel):
    username: str
    password: str


class UserCreateRequest(SetupAdminRequest):
    role: Literal["admin", "operator"] = "operator"


class UserUpdateRequest(ApiModel):
    displayName: str | None = Field(default=None, min_length=2, max_length=120)
    role: Literal["admin", "operator"] | None = None
    active: bool | None = None
    password: str | None = Field(default=None, min_length=12, max_length=200)


class IdentityCreateRequest(ApiModel):
    displayName: str = Field(min_length=1, max_length=200)
    candidateNo: str | None = Field(default=None, max_length=100)
    category: Literal["whitelist", "blacklist"] = "whitelist"


class IdentityUpdateRequest(ApiModel):
    displayName: str | None = Field(default=None, min_length=1, max_length=200)
    candidateNo: str | None = Field(default=None, max_length=100)
    category: Literal["whitelist", "blacklist"] | None = None
    active: bool | None = None


class EnrollmentCommitItem(ApiModel):
    token: str
    faceIndex: int = Field(ge=0)
    displayName: str = Field(min_length=1, max_length=200)
    candidateNo: str | None = Field(default=None, max_length=100)
    category: Literal["whitelist", "blacklist"] = "whitelist"
    sessionId: int | None = None


class EnrollmentCommitRequest(ApiModel):
    items: list[EnrollmentCommitItem] = Field(min_length=1, max_length=100)


class SessionRequest(ApiModel):
    name: str = Field(min_length=2, max_length=200)
    code: str = Field(min_length=1, max_length=100)
    startsAt: datetime
    endsAt: datetime

    @field_validator("endsAt")
    @classmethod
    def validate_end(cls, value: datetime, info):  # type: ignore[no-untyped-def]
        start = info.data.get("startsAt")
        if start and value <= start:
            raise ValueError("End time must be after start time")
        return value


class SessionUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    code: str | None = Field(default=None, min_length=1, max_length=100)
    startsAt: datetime | None = None
    endsAt: datetime | None = None


class SessionMembersRequest(ApiModel):
    identityIds: list[int] = Field(max_length=2500)


class CameraRequest(ApiModel):
    name: str = Field(min_length=1, max_length=120)
    rtspUrl: str = Field(min_length=5, max_length=2000)
    enabled: bool = True
    rotation: Literal[0, 90, 180, 270] = 0
    roi: list[list[float]] = Field(default_factory=list, max_length=12)
    sessionId: int | None = None


class CameraUpdateRequest(ApiModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    rtspUrl: str | None = Field(default=None, min_length=5, max_length=2000)
    enabled: bool | None = None
    rotation: Literal[0, 90, 180, 270] | None = None
    roi: list[list[float]] | None = Field(default=None, max_length=12)
    sessionId: int | None = None
    clearSession: bool = False


class CameraTestRequest(ApiModel):
    rtspUrl: str | None = Field(default=None, max_length=2000)


class ResolveEventRequest(ApiModel):
    identityId: int
    note: str | None = Field(default=None, max_length=1000)


class EventBulkDeleteRequest(ApiModel):
    sessionId: int | None = None
    eventTypes: list[Literal["match", "unknown", "not_registered", "blacklist"]] = Field(
        default_factory=list
    )
    before: datetime | None = None
    unresolvedOnly: bool = False
    confirmation: Literal["DELETE"]


class ThresholdRequest(ApiModel):
    threshold: float = Field(ge=0.36, le=0.60)


class TelegramSettingsRequest(ApiModel):
    enabled: bool
    botToken: str | None = Field(default=None, max_length=300)
    chatId: str | None = Field(default=None, max_length=100)
    alertTypes: list[Literal["unknown", "not_registered", "blacklist"]] = Field(
        default_factory=lambda: ["unknown"]
    )


def _serialize_user(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name,
        "role": user.role,
        "active": user.active,
        "createdAt": user.created_at.isoformat(),
    }


def _serialize_identity(identity: Identity) -> dict:
    return {
        "id": identity.id,
        "candidateNo": identity.candidate_no,
        "displayName": identity.display_name,
        "category": identity.category,
        "active": identity.active,
        "faceSampleCount": len(identity.face_samples),
        "faceUrl": f"/api/identities/{identity.id}/face" if identity.face_samples else None,
        "createdAt": identity.created_at.isoformat(),
    }


def _serialize_session(db: Session, row: ExamSession) -> dict:
    member_count = db.scalar(
        select(func.count(SessionMember.id)).where(SessionMember.session_id == row.id)
    )
    camera_count = db.scalar(select(func.count(Camera.id)).where(Camera.session_id == row.id))
    present_count = db.scalar(select(func.count(Attendance.id)).where(Attendance.session_id == row.id))
    return {
        "id": row.id,
        "name": row.name,
        "code": row.code,
        "startsAt": row.starts_at.isoformat(),
        "endsAt": row.ends_at.isoformat(),
        "status": row.status,
        "memberCount": member_count or 0,
        "cameraCount": camera_count or 0,
        "presentCount": present_count or 0,
    }


def _serialize_camera(db: Session, row: Camera) -> dict:
    session = db.get(ExamSession, row.session_id) if row.session_id else None
    health = next(
        (item for item in camera_service.health() if item["cameraId"] == row.id), None
    )
    return {
        "id": row.id,
        "name": row.name,
        "rtspUrlSet": bool(row.rtsp_url_enc),
        "enabled": row.enabled,
        "rotation": row.rotation,
        "roi": json.loads(row.roi_json or "[]"),
        "sessionId": row.session_id,
        "sessionName": session.name if session else None,
        "previewUrl": f"/api/cameras/{row.id}/preview",
        "health": health,
    }


def _serialize_event(db: Session, row: RecognitionEvent) -> dict:
    camera = db.get(Camera, row.camera_id)
    session = db.get(ExamSession, row.session_id) if row.session_id else None
    resolved = db.get(Identity, row.resolved_identity_id) if row.resolved_identity_id else None
    resolver = db.get(User, row.resolved_by_user_id) if row.resolved_by_user_id else None
    return {
        "id": row.id,
        "eventType": row.event_type,
        "cameraId": row.camera_id,
        "cameraName": camera.name if camera else None,
        "sessionId": row.session_id,
        "sessionName": session.name if session else None,
        "identityId": row.identity_id,
        "identityName": row.identity_name_snapshot,
        "candidateNo": row.candidate_no_snapshot,
        "identityCategory": row.identity_category_snapshot,
        "identityFaceUrl": f"/api/identities/{row.identity_id}/face" if row.identity_id else None,
        "confidence": row.confidence,
        "capturedAt": row.captured_at.isoformat(),
        "decisionLatencyMs": row.decision_latency_ms,
        "snapshotUrl": f"/api/events/{row.id}/snapshot",
        "resolvedIdentityId": row.resolved_identity_id,
        "resolvedIdentityName": resolved.display_name if resolved else None,
        "resolvedBy": resolver.display_name if resolver else None,
        "resolvedAt": row.resolved_at.isoformat() if row.resolved_at else None,
        "resolutionNote": row.resolution_note,
        "telegramStatus": row.telegram_status,
    }


def _auth_context(
    request: Request,
    db: Session = Depends(get_db),
) -> tuple[User, AppSession]:
    token = request.cookies.get(SESSION_COOKIE)
    app_session = sessions.get(token)
    if not app_session:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    user = db.get(User, app_session.user_id)
    if not user or not user.active:
        sessions.delete(token)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account is inactive")
    return user, app_session


def current_user(context: tuple[User, AppSession] = Depends(_auth_context)) -> User:
    return context[0]


def mutation_user(
    x_csrf_token: Annotated[str | None, Header()] = None,
    context: tuple[User, AppSession] = Depends(_auth_context),
) -> User:
    if not x_csrf_token or x_csrf_token != context[1].csrf_token:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Invalid CSRF token")
    return context[0]


def admin_user(user: User = Depends(mutation_user)) -> User:
    if user.role != Role.ADMIN.value:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Administrator access required")
    return user


def _set_session_cookie(response: Response, app_session: AppSession) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        app_session.token,
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=False,
        samesite="strict",
        path="/",
    )


@router.get("/setup/status")
def setup_status(db: Session = Depends(get_db)) -> dict:
    return {"required": (db.scalar(select(func.count(User.id))) or 0) == 0}


@router.post("/setup/admin", status_code=201)
def setup_admin(payload: SetupAdminRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    if (db.scalar(select(func.count(User.id))) or 0) > 0:
        raise HTTPException(status.HTTP_409_CONFLICT, "Initial setup is already complete")
    user = User(
        username=payload.username.lower(),
        display_name=payload.displayName,
        password_hash=hash_password(payload.password),
        role=Role.ADMIN.value,
    )
    db.add(user)
    db.flush()
    record_audit(
        db,
        user_id=user.id,
        action="setup.complete",
        entity_type="user",
        entity_id=user.id,
    )
    db.commit()
    app_session = sessions.create(user.id)
    _set_session_cookie(response, app_session)
    return {"user": _serialize_user(user), "csrfToken": app_session.csrf_token}


@router.post("/auth/login")
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> dict:
    user = db.scalar(select(User).where(User.username == payload.username.lower()))
    if not user or not user.active or not verify_password(user.password_hash, payload.password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")
    app_session = sessions.create(user.id)
    _set_session_cookie(response, app_session)
    record_audit(db, user_id=user.id, action="auth.login", entity_type="user", entity_id=user.id)
    db.commit()
    return {"user": _serialize_user(user), "csrfToken": app_session.csrf_token}


@router.get("/auth/me")
def me(context: tuple[User, AppSession] = Depends(_auth_context)) -> dict:
    return {"user": _serialize_user(context[0]), "csrfToken": context[1].csrf_token}


@router.post("/auth/logout", status_code=204)
def logout(
    request: Request,
    response: Response,
    user: User = Depends(mutation_user),
    db: Session = Depends(get_db),
) -> Response:
    sessions.delete(request.cookies.get(SESSION_COOKIE))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    record_audit(db, user_id=user.id, action="auth.logout", entity_type="user", entity_id=user.id)
    db.commit()
    return response


@router.get("/system/health")
def system_health(db: Session = Depends(get_db)) -> dict:
    threshold_row = db.get(Setting, "recognition_threshold")
    return {
        "status": "ready" if face_engine.ready else "degraded",
        "recognitionReady": face_engine.ready,
        "inferenceProvider": face_engine.provider,
        "cudaAvailable": face_engine.cuda_available,
        "modelError": face_engine.error,
        "modelSamples": face_engine.index.sample_count,
        "recognitionThreshold": float(threshold_row.value or 0.45) if threshold_row else 0.45,
        "cameraHealth": camera_service.health(),
        "storageBytes": blobs.size_bytes() + (settings.database_path.stat().st_size if settings.database_path.exists() else 0),
        "version": "0.1.0",
    }


@router.get("/dashboard")
def dashboard(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    active_sessions = db.scalars(
        select(ExamSession).where(ExamSession.status == SessionStatus.ACTIVE.value)
    ).all()
    latest_event = db.scalar(select(RecognitionEvent).order_by(RecognitionEvent.id.desc()).limit(1))
    recent = db.scalars(
        select(RecognitionEvent).order_by(RecognitionEvent.id.desc()).limit(20)
    ).all()
    return {
        "activeSessions": [_serialize_session(db, row) for row in active_sessions],
        "cameras": [_serialize_camera(db, row) for row in db.scalars(select(Camera)).all()],
        "latestEvent": _serialize_event(db, latest_event) if latest_event else None,
        "recentEvents": [_serialize_event(db, row) for row in recent],
        "counts": {
            "identities": db.scalar(select(func.count(Identity.id)).where(Identity.active.is_(True))) or 0,
            "present": db.scalar(select(func.count(Attendance.id))) or 0,
            "unresolved": db.scalar(
                select(func.count(RecognitionEvent.id)).where(
                    RecognitionEvent.event_type == EventType.UNKNOWN.value,
                    RecognitionEvent.resolved_at.is_(None),
                )
            )
            or 0,
        },
    }


@router.get("/users")
def list_users(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_serialize_user(row) for row in db.scalars(select(User).order_by(User.username)).all()]


@router.post("/users", status_code=201)
def create_user(
    payload: UserCreateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = User(
        username=payload.username.lower(),
        display_name=payload.displayName,
        password_hash=hash_password(payload.password),
        role=payload.role,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists") from exc
    record_audit(db, user_id=admin.id, action="user.create", entity_type="user", entity_id=row.id)
    db.commit()
    return _serialize_user(row)


@router.patch("/users/{user_id}")
def update_user(
    user_id: int,
    payload: UserUpdateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(User, user_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    changes = payload.model_dump(exclude_unset=True)
    if "displayName" in changes:
        row.display_name = changes["displayName"]
    if "role" in changes:
        row.role = changes["role"]
    if "active" in changes:
        if row.id == admin.id and not changes["active"]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate yourself")
        row.active = changes["active"]
    if changes.get("password"):
        row.password_hash = hash_password(changes["password"])
    record_audit(db, user_id=admin.id, action="user.update", entity_type="user", entity_id=row.id)
    db.commit()
    return _serialize_user(row)


@router.get("/identities")
def list_identities(
    category: str | None = None,
    q: str | None = None,
    includeArchived: bool = False,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(Identity).order_by(Identity.display_name)
    if not includeArchived:
        statement = statement.where(Identity.active.is_(True))
    if category in {"whitelist", "blacklist"}:
        statement = statement.where(Identity.category == category)
    if q:
        statement = statement.where(
            or_(
                Identity.display_name.ilike(f"%{q}%"),
                Identity.candidate_no.ilike(f"%{q}%"),
            )
        )
    return [_serialize_identity(row) for row in db.scalars(statement).unique().all()]


@router.post("/identities", status_code=201)
def create_identity(
    payload: IdentityCreateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = Identity(
        display_name=payload.displayName,
        candidate_no=payload.candidateNo or None,
        category=payload.category,
    )
    db.add(row)
    db.flush()
    record_audit(db, user_id=admin.id, action="identity.create", entity_type="identity", entity_id=row.id)
    db.commit()
    return _serialize_identity(row)


@router.patch("/identities/{identity_id}")
def update_identity(
    identity_id: int,
    payload: IdentityUpdateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Identity, identity_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity not found")
    changes = payload.model_dump(exclude_unset=True)
    if "displayName" in changes:
        row.display_name = changes["displayName"]
    if "candidateNo" in changes:
        row.candidate_no = changes["candidateNo"] or None
    if "active" in changes:
        row.active = changes["active"]
    if "category" in changes:
        row.category = changes["category"]
        if row.category == IdentityCategory.BLACKLIST.value:
            db.execute(delete(SessionMember).where(SessionMember.identity_id == row.id))
    row.updated_at = utcnow()
    record_audit(
        db,
        user_id=admin.id,
        action="identity.update",
        entity_type="identity",
        entity_id=row.id,
        detail={"fields": sorted(changes)},
    )
    db.commit()
    refresh_index_from_database()
    return _serialize_identity(row)


@router.delete("/identities/{identity_id}")
def remove_identity(
    identity_id: int,
    permanent: bool = False,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Identity, identity_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity not found")
    row.active = False
    db.execute(delete(SessionMember).where(SessionMember.identity_id == row.id))
    if permanent:
        for sample in list(row.face_samples):
            blobs.delete(sample.crop_blob_key)
            db.delete(sample)
        row.display_name = "[Deleted identity]"
        row.candidate_no = None
    record_audit(
        db,
        user_id=admin.id,
        action="identity.purge" if permanent else "identity.archive",
        entity_type="identity",
        entity_id=row.id,
    )
    db.commit()
    refresh_index_from_database()
    return {"ok": True, "permanent": permanent}


@router.get("/identities/{identity_id}/face")
def identity_face(
    identity_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    sample = db.scalar(
        select(FaceSample).where(FaceSample.identity_id == identity_id).order_by(FaceSample.id).limit(1)
    )
    if not sample:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Face sample not found")
    return Response(blobs.get(sample.crop_blob_key), media_type="image/jpeg")


@router.post("/enrollments/analyze")
async def analyze_enrollments(
    files: Annotated[list[UploadFile], File()],
    _admin: User = Depends(admin_user),
) -> dict:
    if len(files) > 100:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Upload at most 100 files")
    results = []
    for upload in files:
        content = await upload.read(settings.max_upload_bytes + 1)
        try:
            result = await run_in_threadpool(staging.analyze, upload.filename or "upload", content)
        except ValueError as exc:
            result = {
                "filename": upload.filename,
                "status": "rejected",
                "faces": [],
                "message": str(exc),
            }
        results.append(result)
    return {"items": results}


@router.post("/enrollments/commit", status_code=201)
def commit_enrollments(
    payload: EnrollmentCommitRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    created: list[Identity] = []
    for item in payload.items:
        _staged_file, face = staging.take(item.token, item.faceIndex)
        if item.category == IdentityCategory.BLACKLIST.value and item.sessionId:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Blacklist identities cannot join a roster")
        row = Identity(
            display_name=item.displayName,
            candidate_no=item.candidateNo or None,
            category=item.category,
        )
        db.add(row)
        db.flush()
        crop_key = blobs.put(face.crop_jpeg, namespace="face")
        sample = FaceSample(
            identity_id=row.id,
            embedding_enc=b"pending",
            crop_blob_key=crop_key,
            quality_score=face.quality_score,
        )
        db.add(sample)
        db.flush()
        sample.embedding_enc = crypto.encrypt(
            face.embedding.astype(np.float32).tobytes(),
            context=f"embedding:{sample.id}".encode(),
        )
        if item.sessionId:
            session = db.get(ExamSession, item.sessionId)
            if not session:
                raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
            db.add(SessionMember(session_id=session.id, identity_id=row.id))
        record_audit(
            db,
            user_id=admin.id,
            action="identity.enroll",
            entity_type="identity",
            entity_id=row.id,
            detail={"quality": face.quality_score},
        )
        created.append(row)
    db.commit()
    refresh_index_from_database()
    return {"items": [_serialize_identity(row) for row in created]}


@router.get("/sessions")
def list_sessions(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.scalars(select(ExamSession).order_by(ExamSession.starts_at.desc())).all()
    return [_serialize_session(db, row) for row in rows]


@router.post("/sessions", status_code=201)
def create_session(
    payload: SessionRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = ExamSession(
        name=payload.name,
        code=payload.code,
        starts_at=payload.startsAt,
        ends_at=payload.endsAt,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session code already exists") from exc
    record_audit(db, user_id=admin.id, action="session.create", entity_type="session", entity_id=row.id)
    db.commit()
    return _serialize_session(db, row)


@router.patch("/sessions/{session_id}")
def update_session(
    session_id: int,
    payload: SessionUpdateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ExamSession, session_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
    changes = payload.model_dump(exclude_unset=True)
    mapping = {"name": "name", "code": "code", "startsAt": "starts_at", "endsAt": "ends_at"}
    for key, value in changes.items():
        setattr(row, mapping[key], value)
    if row.ends_at <= row.starts_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "End time must be after start time")
    record_audit(db, user_id=admin.id, action="session.update", entity_type="session", entity_id=row.id)
    try:
        db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Session code already exists") from exc
    return _serialize_session(db, row)


@router.get("/sessions/{session_id}/members")
def session_members(
    session_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(
        select(Identity)
        .join(SessionMember, SessionMember.identity_id == Identity.id)
        .where(SessionMember.session_id == session_id)
        .order_by(Identity.display_name)
    ).unique().all()
    return [_serialize_identity(row) for row in rows]


@router.put("/sessions/{session_id}/members")
def replace_session_members(
    session_id: int,
    payload: SessionMembersRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    session = db.get(ExamSession, session_id)
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
    unique_ids = list(dict.fromkeys(payload.identityIds))
    identities = db.scalars(select(Identity).where(Identity.id.in_(unique_ids))).all() if unique_ids else []
    if len(identities) != len(unique_ids):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "One or more identities do not exist")
    if any(not row.active or row.category != IdentityCategory.WHITELIST.value for row in identities):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Roster members must be active whitelist identities")
    db.execute(delete(SessionMember).where(SessionMember.session_id == session_id))
    db.add_all(SessionMember(session_id=session_id, identity_id=row.id) for row in identities)
    record_audit(
        db,
        user_id=admin.id,
        action="session.roster.replace",
        entity_type="session",
        entity_id=session_id,
        detail={"memberCount": len(identities)},
    )
    db.commit()
    return {"memberCount": len(identities)}


@router.post("/sessions/{session_id}/start")
def start_session(
    session_id: int,
    user: User = Depends(mutation_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ExamSession, session_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
    if (db.scalar(select(func.count(SessionMember.id)).where(SessionMember.session_id == row.id)) or 0) == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Add at least one candidate before starting")
    row.status = SessionStatus.ACTIVE.value
    record_audit(db, user_id=user.id, action="session.start", entity_type="session", entity_id=row.id)
    db.commit()
    return _serialize_session(db, row)


@router.post("/sessions/{session_id}/close")
def close_session(
    session_id: int,
    user: User = Depends(mutation_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(ExamSession, session_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
    row.status = SessionStatus.CLOSED.value
    record_audit(db, user_id=user.id, action="session.close", entity_type="session", entity_id=row.id)
    db.commit()
    return _serialize_session(db, row)


@router.get("/cameras")
def list_cameras(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    return [_serialize_camera(db, row) for row in db.scalars(select(Camera).order_by(Camera.id)).all()]


@router.post("/cameras", status_code=201)
def create_camera(
    payload: CameraRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    if payload.enabled and (db.scalar(select(func.count(Camera.id)).where(Camera.enabled.is_(True))) or 0) >= 4:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "A maximum of four cameras can be enabled")
    if payload.sessionId and not db.get(ExamSession, payload.sessionId):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
    row = Camera(
        name=payload.name,
        rtsp_url_enc=b"pending",
        enabled=payload.enabled,
        rotation=payload.rotation,
        roi_json=json.dumps(payload.roi),
        session_id=payload.sessionId,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Camera name already exists") from exc
    row.rtsp_url_enc = crypto.encrypt_text(payload.rtspUrl, context=f"camera:{row.id}".encode())
    record_audit(db, user_id=admin.id, action="camera.create", entity_type="camera", entity_id=row.id)
    db.commit()
    camera_service.reload()
    return _serialize_camera(db, row)


@router.patch("/cameras/{camera_id}")
def update_camera(
    camera_id: int,
    payload: CameraUpdateRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Camera, camera_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("enabled") and not row.enabled:
        enabled_count = db.scalar(select(func.count(Camera.id)).where(Camera.enabled.is_(True))) or 0
        if enabled_count >= 4:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "A maximum of four cameras can be enabled")
    if "name" in changes:
        row.name = changes["name"]
    if changes.get("rtspUrl"):
        row.rtsp_url_enc = crypto.encrypt_text(
            changes["rtspUrl"], context=f"camera:{row.id}".encode()
        )
    if "enabled" in changes:
        row.enabled = changes["enabled"]
    if "rotation" in changes:
        row.rotation = changes["rotation"]
    if "roi" in changes:
        row.roi_json = json.dumps(changes["roi"])
    if changes.get("clearSession"):
        row.session_id = None
    elif "sessionId" in changes:
        if changes["sessionId"] and not db.get(ExamSession, changes["sessionId"]):
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Exam session not found")
        row.session_id = changes["sessionId"]
    record_audit(db, user_id=admin.id, action="camera.update", entity_type="camera", entity_id=row.id)
    try:
        db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Camera name already exists") from exc
    camera_service.reload()
    return _serialize_camera(db, row)


@router.delete("/cameras/{camera_id}", status_code=204)
def delete_camera(
    camera_id: int,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(Camera, camera_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    if db.scalar(select(func.count(RecognitionEvent.id)).where(RecognitionEvent.camera_id == row.id)):
        row.enabled = False
        row.session_id = None
        action = "camera.archive"
    else:
        db.delete(row)
        action = "camera.delete"
    record_audit(db, user_id=admin.id, action=action, entity_type="camera", entity_id=camera_id)
    db.commit()
    camera_service.reload()
    return Response(status_code=204)


@router.post("/cameras/{camera_id}/test")
async def test_camera(
    camera_id: int,
    payload: CameraTestRequest,
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Camera, camera_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Camera not found")
    url = payload.rtspUrl or crypto.decrypt_text(row.rtsp_url_enc, context=f"camera:{row.id}".encode())
    return await run_in_threadpool(camera_service.test_connection, url)


@router.get("/cameras/{camera_id}/preview")
def camera_preview(camera_id: int, _user: User = Depends(current_user)) -> StreamingResponse:
    def frames():  # type: ignore[no-untyped-def]
        while True:
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nCache-Control: no-cache\r\n\r\n"
                + camera_service.get_preview(camera_id)
                + b"\r\n"
            )
            time.sleep(0.125)

    return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")


@router.get("/events")
def list_events(
    sessionId: int | None = None,
    eventType: str | None = None,
    unresolvedOnly: bool = False,
    limit: int = Query(default=100, ge=1, le=1000),
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    statement = select(RecognitionEvent).order_by(RecognitionEvent.captured_at.desc()).limit(limit)
    if sessionId:
        statement = statement.where(RecognitionEvent.session_id == sessionId)
    if eventType:
        statement = statement.where(RecognitionEvent.event_type == eventType)
    if unresolvedOnly:
        statement = statement.where(RecognitionEvent.resolved_at.is_(None))
    return [_serialize_event(db, row) for row in db.scalars(statement).all()]


@router.post("/events/bulk-delete")
def bulk_delete_events(
    payload: EventBulkDeleteRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    if not (payload.sessionId or payload.eventTypes or payload.before or payload.unresolvedOnly):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Choose at least one event filter before bulk deletion",
        )
    statement = select(RecognitionEvent)
    if payload.sessionId:
        statement = statement.where(RecognitionEvent.session_id == payload.sessionId)
    if payload.eventTypes:
        statement = statement.where(RecognitionEvent.event_type.in_(payload.eventTypes))
    if payload.before:
        statement = statement.where(RecognitionEvent.captured_at < payload.before)
    if payload.unresolvedOnly:
        statement = statement.where(RecognitionEvent.resolved_at.is_(None))
    rows = db.scalars(statement).all()
    blob_keys = [row.snapshot_blob_key for row in rows]
    for row in rows:
        db.delete(row)
    record_audit(
        db,
        user_id=admin.id,
        action="event.bulk_delete",
        entity_type="recognition_event",
        detail={
            "count": len(rows),
            "sessionId": payload.sessionId,
            "eventTypes": payload.eventTypes,
            "before": payload.before.isoformat() if payload.before else None,
            "unresolvedOnly": payload.unresolvedOnly,
        },
    )
    db.commit()
    for blob_key in blob_keys:
        blobs.delete(blob_key)
    return {"deleted": len(rows)}


@router.get("/events/{event_id}/snapshot")
def event_snapshot(
    event_id: int,
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    row = db.get(RecognitionEvent, event_id)
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    return Response(blobs.get(row.snapshot_blob_key), media_type="image/jpeg")


@router.post("/events/{event_id}/resolve")
def resolve_event(
    event_id: int,
    payload: ResolveEventRequest,
    user: User = Depends(mutation_user),
    db: Session = Depends(get_db),
) -> dict:
    event = db.get(RecognitionEvent, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    if event.event_type != EventType.UNKNOWN.value or event.resolved_at:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Only unresolved unknown events can be assigned")
    membership = db.scalar(
        select(SessionMember).where(
            SessionMember.session_id == event.session_id,
            SessionMember.identity_id == payload.identityId,
        )
    )
    if not membership:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Candidate is not on this exam roster")
    identity = db.get(Identity, payload.identityId)
    if not identity:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Identity not found")
    now = datetime.now(timezone.utc)
    event.resolved_identity_id = identity.id
    event.resolved_by_user_id = user.id
    event.resolved_at = now
    remark = f"Manually matched by {user.display_name}"
    if payload.note:
        remark += f": {payload.note}"
    event.resolution_note = remark
    attendance = db.scalar(
        select(Attendance).where(
            Attendance.session_id == event.session_id,
            Attendance.identity_id == identity.id,
        )
    )
    if attendance:
        attendance.first_seen_at = min(attendance.first_seen_at, event.captured_at)
        attendance.last_seen_at = max(attendance.last_seen_at, event.captured_at)
        attendance.source = AttendanceSource.MANUAL.value
        attendance.operator_user_id = user.id
        attendance.remarks = remark
    else:
        db.add(
            Attendance(
                session_id=event.session_id,
                identity_id=identity.id,
                first_seen_at=event.captured_at,
                last_seen_at=event.captured_at,
                source=AttendanceSource.MANUAL.value,
                first_event_id=event.id,
                camera_id=event.camera_id,
                confidence=None,
                operator_user_id=user.id,
                remarks=remark,
            )
        )
    record_audit(
        db,
        user_id=user.id,
        action="event.manual_match",
        entity_type="recognition_event",
        entity_id=event.id,
        detail={"identityId": identity.id},
    )
    db.commit()
    result = _serialize_event(db, event)
    event_hub.publish("recognition.resolved", result)
    return result


@router.delete("/events/{event_id}", status_code=204)
def delete_event(
    event_id: int,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> Response:
    event = db.get(RecognitionEvent, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Event not found")
    blob_key = event.snapshot_blob_key
    db.delete(event)
    record_audit(
        db,
        user_id=admin.id,
        action="event.delete",
        entity_type="recognition_event",
        entity_id=event_id,
    )
    db.commit()
    blobs.delete(blob_key)
    return Response(status_code=204)


@router.get("/reports/sessions/{session_id}.xlsx")
async def session_report(
    session_id: int,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> Response:
    def render_report() -> tuple[str, bytes]:
        with SessionLocal() as report_db:
            return build_session_report(report_db, session_id)

    try:
        filename, content = await run_in_threadpool(render_report)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    record_audit(
        db,
        user_id=user.id,
        action="report.export",
        entity_type="session",
        entity_id=session_id,
    )
    db.commit()
    return Response(
        content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/settings")
def get_settings(_user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    threshold = db.get(Setting, "recognition_threshold")
    return {
        "recognitionThreshold": float(threshold.value or 0.45) if threshold else 0.45,
        "telegram": telegram_service.configured(),
        "storageBytes": blobs.size_bytes()
        + (settings.database_path.stat().st_size if settings.database_path.exists() else 0),
        "dataDirectory": str(settings.data_dir),
    }


@router.put("/settings/threshold")
def update_threshold(
    payload: ThresholdRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    row = db.get(Setting, "recognition_threshold") or Setting(key="recognition_threshold")
    previous = row.value
    row.value = f"{payload.threshold:.3f}"
    row.updated_at = utcnow()
    db.add(row)
    record_audit(
        db,
        user_id=admin.id,
        action="settings.threshold",
        entity_type="setting",
        entity_id=row.key,
        detail={"from": previous, "to": row.value},
    )
    db.commit()
    return {"recognitionThreshold": payload.threshold}


@router.put("/settings/telegram")
def update_telegram(
    payload: TelegramSettingsRequest,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> dict:
    plain_values = {
        "telegram_enabled": "true" if payload.enabled else "false",
        "telegram_alert_types": json.dumps(list(dict.fromkeys(payload.alertTypes))),
    }
    for key, value in plain_values.items():
        row = db.get(Setting, key) or Setting(key=key)
        row.value = value
        row.updated_at = utcnow()
        db.add(row)
    for key, value in {"telegram_token": payload.botToken, "telegram_chat_id": payload.chatId}.items():
        if value:
            row = db.get(Setting, key) or Setting(key=key)
            row.value_enc = crypto.encrypt_text(value, context=f"setting:{key}".encode())
            row.updated_at = utcnow()
            db.add(row)
    record_audit(
        db,
        user_id=admin.id,
        action="settings.telegram",
        entity_type="setting",
        entity_id="telegram",
        detail={"enabled": payload.enabled, "alertTypes": payload.alertTypes},
    )
    db.commit()
    return telegram_service.configured()


@router.post("/settings/telegram/test")
async def test_telegram(_admin: User = Depends(admin_user)) -> dict:
    try:
        await run_in_threadpool(telegram_service.send_test)
    except Exception as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Telegram test failed: {exc}") from exc
    return {"ok": True}


@router.get("/audit")
def audit_log(
    limit: int = Query(default=200, ge=1, le=1000),
    _user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(limit)).all()
    users_by_id = {
        row.id: row.display_name for row in db.scalars(select(User)).all()
    }
    return [
        {
            "id": row.id,
            "user": users_by_id.get(row.user_id, "System"),
            "action": row.action,
            "entityType": row.entity_type,
            "entityId": row.entity_id,
            "detail": json.loads(row.detail_json),
            "createdAt": row.created_at.isoformat(),
        }
        for row in rows
    ]


@router.websocket("/ws/events")
async def events_socket(websocket: WebSocket) -> None:
    token = websocket.cookies.get(SESSION_COOKIE)
    app_session = sessions.get(token)
    if not app_session:
        await websocket.close(code=4401)
        return
    with SessionLocal() as db:
        user = db.get(User, app_session.user_id)
        if not user or not user.active:
            await websocket.close(code=4401)
            return
    await event_hub.connect(websocket)
    try:
        await websocket.send_json({"type": "connected", "payload": {"at": utcnow().isoformat()}})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await event_hub.disconnect(websocket)

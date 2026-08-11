from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from .api import router
from .cameras import camera_service
from .config import settings
from .db import SessionLocal, init_database
from .models import Setting
from .recognition import refresh_index_from_database
from .realtime import event_hub
from .telegram import telegram_service


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.ensure_directories()
    init_database()
    with SessionLocal() as db:
        if not db.get(Setting, "recognition_threshold"):
            db.add(Setting(key="recognition_threshold", value="0.450"))
        if not db.get(Setting, "telegram_enabled"):
            db.add(Setting(key="telegram_enabled", value="false"))
        if not db.get(Setting, "telegram_alert_types"):
            db.add(Setting(key="telegram_alert_types", value='["unknown"]'))
        db.commit()
    sample_count = refresh_index_from_database()
    event_hub.bind_loop(asyncio.get_running_loop())
    telegram_service.start()
    camera_service.start()
    logger.info("Fast-FR started with %s recognition samples", sample_count)
    yield
    camera_service.stop()
    telegram_service.stop()


app = FastAPI(
    title="Fast-FR",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # type: ignore[no-untyped-def]
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; img-src 'self' data: blob:; script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; connect-src 'self' ws://127.0.0.1:* "
        "ws://localhost:*; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)


@app.get("/{path:path}", include_in_schema=False)
async def spa(path: str) -> FileResponse:
    dist = settings.ui_dist
    candidate = (dist / path).resolve()
    try:
        candidate.relative_to(dist.resolve())
    except ValueError as exc:
        raise HTTPException(404) from exc
    if path and candidate.is_file():
        return FileResponse(candidate)
    index = dist / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(404, "UI build not found. Run npm run build.")

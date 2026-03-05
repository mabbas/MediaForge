"""GrabItDown API — FastAPI application entry point.

Run with:
  uvicorn api.main:app --reload

Or with make:
  make api-dev
"""

from __future__ import annotations

import asyncio
import logging

# Load .env from project root so GID_FFMPEG_LOCATION and other vars are available
from src.env_loader import load_project_dotenv
load_project_dotenv()

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.config import get_api_settings
from api.database.connection import close_database, init_database
from api.dependencies import shutdown_app
from api.middleware.error_handler import (
    generic_exception_handler,
    grabitdown_exception_handler,
)
from api.middleware.rate_limiter import RateLimiterMiddleware
from api.middleware.request_logger import RequestLoggerMiddleware
from api.routers import (
    clips,
    clips_merge,
    config,
    downloads,
    features,
    health,
    history,
    providers,
    queue,
    resolve,
    transcripts,
    usage,
    ws,
)
from api.services.engine_sync import get_engine_sync, reset_engine_sync
from api.services.progress_bridge import get_progress_bridge, reset_progress_bridge
from api.services.seed import seed_defaults
from src import __app_name__, __version__
from src.exceptions import GrabItDownError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_api_settings()

    logger.info(f"{__app_name__} API v{__version__} starting...")

    await init_database(
        database_url=settings.database_url,
        schema=settings.database_schema,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        echo=settings.database_echo,
    )

    # Seed defaults (unless disabled for testing)
    from api.database.connection import async_session_factory

    if async_session_factory and not settings.skip_seed:
        async with async_session_factory() as session:
            await seed_defaults(session)

    # Start progress bridge and DB sync (DB sync must use main loop for asyncpg)
    bridge = get_progress_bridge()
    bridge.start()
    db_sync = get_engine_sync()
    db_sync.set_main_loop(asyncio.get_running_loop())
    db_sync.start()

    logger.info(
        f"{__app_name__} API ready - "
        f"Swagger UI at http://{settings.host}:{settings.port}/docs"
    )

    yield

    logger.info(f"{__app_name__} API shutting down...")
    bridge = get_progress_bridge()
    bridge.stop()
    reset_progress_bridge()
    db_sync = get_engine_sync()
    db_sync.stop()
    reset_engine_sync()
    await shutdown_app()
    await close_database()
    logger.info(f"{__app_name__} API shut down")


settings = get_api_settings()

app = FastAPI(
    title=f"{__app_name__} API",
    description=(
        "Production-grade media downloader platform API. "
        "Download videos, audio, and playlists from "
        "YouTube and 1000+ other sites.\n\n"
        "## Features\n"
        "- Video & audio downloads with quality selection\n"
        "- Playlist & batch downloads\n"
        "- Real-time progress via WebSocket\n"
        "- Transcript extraction\n"
        "- Resumable downloads\n"
        "- Multi-tenant architecture\n"
        "- Feature flags & tier-based access\n"
        "\n## Authentication\n"
        "Currently running in **personal mode** — "
        "all endpoints accessible without auth.\n"
    ),
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
    openapi_tags=[
        {"name": "Health", "description": "Health & readiness checks"},
        {"name": "Videos", "description": "Video metadata & info"},
        {"name": "Downloads", "description": "Download management"},
        {"name": "Playlists", "description": "Playlist operations"},
        {"name": "Queue", "description": "Download queue management"},
        {"name": "Transcripts", "description": "Transcript extraction"},
        {"name": "Providers", "description": "Provider management"},
        {"name": "Features", "description": "Feature flags & tiers"},
        {"name": "Usage", "description": "Usage tracking & stats"},
        {"name": "History", "description": "Download history"},
        {"name": "Config", "description": "Configuration"},
        {"name": "WebSocket", "description": "Real-time progress"},
        {"name": "Auth", "description": "Authentication (future)"},
        {"name": "clips", "description": "Clip extraction from video"},
    ],
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggerMiddleware)
app.add_middleware(
    RateLimiterMiddleware,
    requests_per_minute=settings.rate_limit_per_minute,
    burst=settings.rate_limit_burst,
)

app.add_exception_handler(GrabItDownError, grabitdown_exception_handler)
app.add_exception_handler(Exception, generic_exception_handler)

app.include_router(health.router, prefix=settings.api_prefix)
app.include_router(features.router, prefix=settings.api_prefix)
app.include_router(usage.router, prefix=settings.api_prefix)
app.include_router(history.router, prefix=settings.api_prefix)
app.include_router(config.router, prefix=settings.api_prefix)
app.include_router(downloads.router, prefix=settings.api_prefix)
app.include_router(queue.router, prefix=settings.api_prefix)
app.include_router(providers.router, prefix=settings.api_prefix)
app.include_router(resolve.router, prefix=settings.api_prefix)
app.include_router(transcripts.router, prefix=settings.api_prefix)
app.include_router(clips_merge.router, prefix=settings.api_prefix)
app.include_router(clips.router, prefix=settings.api_prefix)
app.include_router(ws.router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
async def root():
    from fastapi.responses import RedirectResponse

    return RedirectResponse(url="/docs")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    from fastapi.responses import Response

    return Response(status_code=204)

"""FastAPI app factory for the local review dashboard."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from clipfarm import db
from clipfarm.review.routes import router

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"


@asynccontextmanager
async def _lifespan(app: FastAPI):
    from clipfarm.config import get_settings
    from clipfarm.jobs.cleanup import start_reconcile_sweep
    from clipfarm.jobs.rematch import start_background_sweep
    from clipfarm.jobs.scheduler import start_autopost_scheduler
    from clipfarm.metrics.poller import start_stats_poller

    db.init_db()
    logger.info("Database initialized")
    with db.get_conn() as conn:
        recovered = db.recover_interrupted_uploads(conn)
    if recovered:
        logger.warning("Recovered %d upload(s) interrupted by the last shutdown; they will auto-retry", recovered)
    # Auto-retry items that were recorded before their game finished (mid-game clips etc.).
    start_background_sweep(get_settings().riot.rematch_interval_seconds)
    # Drip-post the best clips (and long-form) on a cadence when auto-post is toggled on.
    start_autopost_scheduler()
    # Archive items whose source recording Ascent deleted, freeing our caches but keeping stats.
    start_reconcile_sweep()
    # Poll YouTube performance stats into the metrics dashboard.
    start_stats_poller()
    logger.info("Background workers started (scheduler, reconcile, stats poller)")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="LeagueClipFarm Review Dashboard", lifespan=_lifespan)

    @app.middleware("http")
    async def _no_cache_assets(request, call_next):
        # The dashboard's HTML/JS/CSS change as the tool is developed; without this the
        # browser serves a stale dashboard.js and new buttons appear but do nothing.
        response = await call_next(request)
        path = request.url.path
        if path in ("/", "/metrics") or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(str(_TEMPLATES_DIR / "index.html"))

    @app.get("/metrics")
    def metrics_page() -> FileResponse:
        return FileResponse(str(_TEMPLATES_DIR / "metrics.html"))

    return app


app = create_app()

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1 import browse, content, imports, processing, system
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.workers.dispatch import ImportDispatcher, build_dispatcher


def create_app(
    session_factory: sessionmaker[Session] | None = None,
    dispatcher: ImportDispatcher | None = None,
) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(title="Lore", version="0.1.0")
    if session_factory is None:
        from app.db.session import get_sessionmaker

        session_factory = get_sessionmaker()
    app.state.session_factory = session_factory
    app.state.dispatcher = dispatcher or build_dispatcher(settings, session_factory)
    app.include_router(system.router)
    app.include_router(imports.router, prefix="/api/v1")
    app.include_router(content.router, prefix="/api/v1")
    app.include_router(processing.router, prefix="/api/v1")
    app.include_router(browse.router, prefix="/api/v1")
    _mount_dashboard(app)
    return app


DASHBOARD_DIR = Path(__file__).parent / "web" / "static"
# Same-origin only: no external scripts/styles/fonts, no inline script, no framing.
DASHBOARD_CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
                 "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")


def _mount_dashboard(app: FastAPI) -> None:
    app.mount("/dashboard", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/dashboard/")

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/dashboard"):
            response.headers["Content-Security-Policy"] = DASHBOARD_CSP
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
            # Always revalidate (ETag makes it cheap) so a page and script from different
            # versions are never combined from the browser cache.
            response.headers["Cache-Control"] = "no-cache"
        return response


app = create_app()

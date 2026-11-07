from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1 import imports, system
from app.core.config import get_settings
from app.core.logging import configure_logging


def create_app(session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    app = FastAPI(title="InstaBrain", version="0.1.0")
    if session_factory is None:
        from app.db.session import get_sessionmaker

        session_factory = get_sessionmaker()
    app.state.session_factory = session_factory
    app.include_router(system.router)
    app.include_router(imports.router, prefix="/api/v1")
    return app


app = create_app()

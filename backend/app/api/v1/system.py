from fastapi import APIRouter, Request, Response, status  # type: ignore[import-not-found]
from sqlalchemy import text  # type: ignore[import-not-found]

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict[str, str]:
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": "error"}
    return {"status": "ready", "database": "ok"}

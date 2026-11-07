from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text

router = APIRouter(tags=["system"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(request: Request, response: Response) -> dict[str, str]:
    checks = {"database": "ok", "queue": "ok"}
    try:
        with request.app.state.session_factory() as session:
            session.execute(text("SELECT 1"))
    except Exception:
        checks["database"] = "error"
    if not request.app.state.dispatcher.is_healthy():
        checks["queue"] = "error"
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if ok else "unavailable", **checks}

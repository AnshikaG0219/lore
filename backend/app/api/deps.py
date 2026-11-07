from collections.abc import Iterator

from fastapi import Request
from sqlalchemy.orm import Session, sessionmaker


def get_session_factory(request: Request) -> sessionmaker[Session]:
    return request.app.state.session_factory


def get_session(request: Request) -> Iterator[Session]:
    session = get_session_factory(request)()
    try:
        yield session
    finally:
        session.close()

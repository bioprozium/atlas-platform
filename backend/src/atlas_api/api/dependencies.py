"""Shared database access for read-only API routes."""

from collections.abc import Iterator
from contextlib import contextmanager

from fastapi import HTTPException, Request
from sqlalchemy import Connection
from sqlalchemy.exc import SQLAlchemyError


@contextmanager
def read_connection(request: Request, unavailable: str) -> Iterator[Connection]:
    engine = request.app.state.database_engine
    if engine is None:
        raise HTTPException(503, unavailable)
    try:
        with engine.connect() as connection:
            # Keep response components on the same snapshot as the published parent.
            yield connection.execution_options(isolation_level="REPEATABLE READ")
    except SQLAlchemyError:
        raise HTTPException(503, unavailable) from None

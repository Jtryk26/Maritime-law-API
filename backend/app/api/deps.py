"""Fælles FastAPI-dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.orm import Session

from app.db.session import get_db

DbSession = Annotated[Session, Depends(get_db)]

MAX_PAGE_SIZE = 100


def pagination(
    page: Annotated[int, Query(ge=1, description="Sidenummer, 1-baseret.")] = 1,
    page_size: Annotated[
        int, Query(ge=1, le=MAX_PAGE_SIZE, description="Antal resultater pr. side.")
    ] = 20,
) -> tuple[int, int]:
    """Validerede sideinddelingsparametre."""
    return page, page_size


Pagination = Annotated[tuple[int, int], Depends(pagination)]

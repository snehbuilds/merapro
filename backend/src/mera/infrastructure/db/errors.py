"""Inspect database errors precisely, so one integrity failure is never mistaken for another."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError


def constraint_name(exc: IntegrityError) -> str | None:
    """The violated constraint's name, from the asyncpg exception under SQLAlchemy's wrapper."""
    for err in (exc.orig, getattr(exc.orig, "__cause__", None)):
        name = getattr(err, "constraint_name", None)
        if isinstance(name, str):
            return name
    return None

"""
CSRF protection for the server-side web UI.

Uses a synchroniser-token pattern:
  1. `csrf_token(request)` returns a per-session token, creating one if absent.
  2. `verify_csrf(request, form_token)` raises HTTPException 403 on mismatch.

SessionMiddleware (backed by `itsdangerous`) must be mounted on the FastAPI app
before these helpers are called.
"""
from __future__ import annotations

import os
import secrets
from typing import Annotated

from fastapi import Form, HTTPException, Request

_FIELD = "_csrf_token"
_TOKEN_BYTES = 32


def csrf_token(request: Request) -> str:
    """Return the CSRF token for this session, creating one if absent."""
    session = request.session
    token = session.get(_FIELD)
    if not token:
        token = secrets.token_hex(_TOKEN_BYTES)
        session[_FIELD] = token
    return token


def verify_csrf(request: Request, form_token: str) -> None:
    """
    Verify that *form_token* matches the session token.

    Raises HTTPException(403) on mismatch.  Pass the form field value
    directly from the route's `Form(...)` dependency.
    """
    expected = request.session.get(_FIELD)
    if not expected or not secrets.compare_digest(expected, form_token):
        raise HTTPException(status_code=403, detail="CSRF token mismatch — please reload the page and try again.")

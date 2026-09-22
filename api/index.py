"""Vercel WSGI entrypoint for NCC Convene."""

from app.web import app

__all__ = ["app"]

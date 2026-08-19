"""Uvicorn entrypoint; importing it requires complete live configuration."""

from app.live.config import LiveSettings
from app.live.server import create_live_app

app = create_live_app(LiveSettings.from_env())

__all__ = ["app"]

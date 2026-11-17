"""Shell IIC: configure its UI before loading the shared ASGI application."""

import os
from pathlib import Path

os.environ.setdefault("UI_DIST_DIR", str(Path(__file__).resolve().parent / "_ui"))
os.environ.setdefault("SESSION_COOKIE_NAME", "iic_session")

from app.sdk import asgi_app

app = asgi_app()

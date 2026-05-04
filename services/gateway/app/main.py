from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .agents.registry import init_registry_from_settings
from .config import get_settings
from .routes import api_v1, lark_events

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gateway")


def create_app() -> FastAPI:
    s = get_settings()
    init_registry_from_settings(s)
    app = FastAPI(
        title="im-office-agent Gateway",
        version="0.1.0",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(SessionMiddleware, secret_key=s.session_secret)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_v1.router)
    app.include_router(lark_events.router)
    return app


app = create_app()

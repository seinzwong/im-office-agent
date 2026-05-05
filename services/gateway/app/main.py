from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

from .agents.registry import init_registry_from_settings
from .config import get_settings
from .routes import api_v1, lark_events

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("gateway")


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI):
    s = get_settings()
    ws_thread = None
    if s.lark_ws_events_enabled:
        if s.lark_app_id.strip() and s.lark_app_secret.strip():
            from .lark_bot.ws_runner import start_lark_ws_client_background

            ws_thread = start_lark_ws_client_background(s)
            log.info(
                "飞书长连接：已提交后台线程（握手完成见 lark_bot.ws_runner 日志「WebSocket 已建立」）"
            )
        else:
            log.warning("lark_ws_events_enabled=true 但缺少 lark_app_id/lark_app_secret，未启动长连接")
    else:
        log.info("飞书长连接：未启用（gateway.yaml 中 lark_ws_events_enabled=false）")
    fastapi_app.state.lark_ws_thread = ws_thread
    yield
    from .lark_bot.im_summary_dispatch import shutdown_im_summary_executor

    shutdown_im_summary_executor()
    if ws_thread is not None and ws_thread.is_alive():
        ws_thread.join(timeout=1.0)


def create_app() -> FastAPI:
    s = get_settings()
    init_registry_from_settings(s)
    app = FastAPI(
        title="im-office-agent Gateway",
        version="0.1.0",
        lifespan=lifespan,
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

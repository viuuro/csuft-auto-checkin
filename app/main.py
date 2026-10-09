"""FastAPI 应用入口：挂载用户端与管理员端路由、静态资源与调度器。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .db import Database
from .routes_admin import router as admin_router
from .routes_user import router as user_router
from .scheduler import SignScheduler
from .security import SessionSigner
from .services import ServiceError, SignInService

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("autosinin")


class AppState:
    """放在 ``app.state`` 上的共享对象。"""

    def __init__(self) -> None:
        self.settings = get_settings()
        self.db = Database(self.settings.db_path)
        self.service = SignInService(self.db, self.settings)
        self.sessions = SessionSigner(self.settings.secret_key)
        self.scheduler = SignScheduler(self.db, self.settings, self.service)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    state = AppState()
    app.state.app_state = state
    state.db.log("startup", "服务启动")
    try:
        state.scheduler.start()
    except Exception:  # noqa: BLE001 - 调度器失败不应阻止 Web 服务
        log.exception("调度器启动失败，Web 服务继续运行")
    log.info("站点已就绪：%s", state.settings.site_name)
    try:
        yield
    finally:
        state.scheduler.shutdown()
        state.db.log("shutdown", "服务停止")


def create_app() -> FastAPI:
    app = FastAPI(
        title="中南林学工自动签到",
        description="基于 simp.csuft.edu.cn 接口的宿舍签到自动化服务",
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )

    app.include_router(user_router)
    app.include_router(admin_router)

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/", include_in_schema=False)
    async def _index() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "index.html"))

    @app.get("/admin", include_in_schema=False)
    async def _admin() -> FileResponse:
        return FileResponse(str(STATIC_DIR / "admin.html"))

    @app.get("/healthz", include_in_schema=False)
    async def _healthz() -> JSONResponse:
        return JSONResponse({"ok": True, "site": app.state.app_state.settings.site_name})

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": str(exc)},
        )

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception) -> JSONResponse:
        log.exception("未处理异常")
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"服务器内部错误：{exc}"},
        )

    return app


app = create_app()

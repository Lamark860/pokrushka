"""Точка входа веб-сервиса."""
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.web.deps import LoginRequired
from app.web.routers import articles, auth, dashboard, metrics, projects, redirect

settings = get_settings()

app = FastAPI(title="База Маркетинг — органический трафик", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, https_only=False)

app.include_router(auth.router)
app.include_router(projects.router)
app.include_router(articles.router)
app.include_router(dashboard.router)
app.include_router(metrics.router)
app.include_router(redirect.router)


@app.exception_handler(LoginRequired)
async def login_required_handler(request: Request, exc: LoginRequired) -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok"}

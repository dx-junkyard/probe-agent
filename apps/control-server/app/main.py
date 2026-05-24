from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI

from .auth import get_principal
from .db import init_db
from .routes import auth, components, shadow, traces

_auth = [Depends(get_principal)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="probe-agent Control Server", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    # Auth router carries its own per-route dependencies (login is public,
    # admin endpoints require an admin principal).
    app.include_router(auth.router)
    app.include_router(traces.router, dependencies=_auth)
    app.include_router(components.router, dependencies=_auth)
    app.include_router(shadow.router, dependencies=_auth)
    return app


app = create_app()

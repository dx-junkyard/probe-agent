from contextlib import asynccontextmanager

from fastapi import FastAPI

from .db import init_db
from .routes import components, shadow, traces


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="probe-agent Control Server", version="0.1.0", lifespan=lifespan)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    app.include_router(traces.router)
    app.include_router(components.router)
    app.include_router(shadow.router)
    return app


app = create_app()

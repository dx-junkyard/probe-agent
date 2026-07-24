from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import get_principal
from .db import init_db
from . import publish_recovery, repository_resync_jobs
from .routes import (
    assistant,
    auth,
    candidate_studio,
    cell_fabric,
    cell_orchestrators,
    cell_quality,
    cell_tasks,
    components,
    connectivity,
    diagnostics,
    evaluation,
    experiments,
    generation,
    github_connections,
    interview,
    interview_alignment,
    interview_change_sets,
    interview_handoff,
    interview_inquiry,
    interview_intent,
    interview_observation,
    interview_refresh,
    probe_patterns,
    project_intelligence,
    publish_jobs,
    question_router,
    replay,
    retention,
    shadow,
    systems,
    system_state,
    trace_analyzers,
    trace_lineage,
    traces,
    workspaces,
)

_auth = [Depends(get_principal)]


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    repository_resync_jobs.recover_interrupted_repository_resync_jobs()
    # Fail over publish jobs interrupted by a previous crash/restart before
    # the periodic worker's first tick (Issue #226); the periodic worker
    # (started below) covers everything that goes stale afterwards.
    publish_recovery.repair_interrupted_jobs(reason="startup_recovery")
    publish_recovery.start_worker()
    try:
        yield
    finally:
        publish_recovery.stop_worker()


def create_app() -> FastAPI:
    app = FastAPI(title="probe-agent Control Server", version="0.1.0", lifespan=lifespan)

    from .llm import LLMResourceLimitError

    @app.exception_handler(LLMResourceLimitError)
    async def llm_quota_exceeded_handler(
        _request: Request, exc: LLMResourceLimitError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=429,
            content={
                "detail": {
                    "code": exc.code,
                    "message": str(exc),
                }
            },
        )

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    # Auth router carries its own per-route dependencies (login is public,
    # admin endpoints require an admin principal).
    app.include_router(auth.router)
    app.include_router(systems.router)
    app.include_router(traces.router, dependencies=_auth)
    app.include_router(trace_lineage.router, dependencies=_auth)
    app.include_router(trace_analyzers.router, dependencies=_auth)
    app.include_router(retention.router, dependencies=_auth)
    app.include_router(components.router, dependencies=_auth)
    app.include_router(shadow.router, dependencies=_auth)
    app.include_router(evaluation.router, dependencies=_auth)
    app.include_router(experiments.router, dependencies=_auth)
    app.include_router(generation.router, dependencies=_auth)
    app.include_router(replay.router, dependencies=_auth)
    app.include_router(candidate_studio.router, dependencies=_auth)
    app.include_router(project_intelligence.router, dependencies=_auth)
    app.include_router(probe_patterns.router, dependencies=_auth)
    app.include_router(connectivity.router, dependencies=_auth)
    app.include_router(diagnostics.router, dependencies=_auth)
    app.include_router(system_state.router, dependencies=_auth)
    app.include_router(assistant.router, dependencies=_auth)
    app.include_router(workspaces.router, dependencies=_auth)
    app.include_router(interview.router, dependencies=_auth)
    app.include_router(interview_intent.router, dependencies=_auth)
    app.include_router(interview_inquiry.router, dependencies=_auth)
    app.include_router(interview_alignment.router, dependencies=_auth)
    app.include_router(interview_handoff.router, dependencies=_auth)
    app.include_router(interview_observation.router, dependencies=_auth)
    app.include_router(interview_refresh.router, dependencies=_auth)
    app.include_router(interview_change_sets.router, dependencies=_auth)
    app.include_router(question_router.router, dependencies=_auth)
    app.include_router(github_connections.router, dependencies=_auth)
    app.include_router(publish_jobs.router, dependencies=_auth)
    app.include_router(cell_fabric.router, dependencies=_auth)
    app.include_router(cell_tasks.router, dependencies=_auth)
    app.include_router(cell_orchestrators.router, dependencies=_auth)
    app.include_router(cell_quality.router, dependencies=_auth)
    return app


app = create_app()

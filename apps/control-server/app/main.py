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
    cell_improvement,
    cell_orchestrators,
    cell_quality,
    cell_root,
    cell_tasks,
    components,
    connectivity,
    diagnostics,
    evaluation,
    evolution_nodes,
    execution_modes,
    experiments,
    exploration,
    flow_experiments,
    flow_explanation,
    functional_lineage,
    generation,
    github_connections,
    interview,
    interview_alignment,
    interview_brief,
    interview_change_sets,
    interview_handoff,
    interview_inquiry,
    interview_intent,
    interview_metrics,
    interview_observation,
    interview_refresh,
    interview_workflow,
    joint_understanding,
    journey_blueprint,
    node_design,
    node_operations,
    overview,
    probe_patterns,
    product_features,
    product_gaps,
    product_lineage,
    product_objectives,
    project_intelligence,
    publish_jobs,
    purpose_chain,
    question_router,
    replay,
    replay_readiness,
    retention,
    shadow,
    snapshot_preflight,
    solution_design,
    stabilization,
    stakeholder_network,
    stakeholder_value_network,
    systems,
    system_state,
    trace_analyzers,
    trace_lineage,
    traces,
    ux_design,
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
    app.include_router(evolution_nodes.router, dependencies=_auth)
    app.include_router(node_design.router, dependencies=_auth)
    app.include_router(exploration.router, dependencies=_auth)
    app.include_router(stabilization.router, dependencies=_auth)
    app.include_router(node_operations.router, dependencies=_auth)
    app.include_router(execution_modes.router, dependencies=_auth)
    app.include_router(flow_explanation.router, dependencies=_auth)
    app.include_router(flow_experiments.router, dependencies=_auth)
    app.include_router(ux_design.router, dependencies=_auth)
    app.include_router(solution_design.router, dependencies=_auth)
    app.include_router(stakeholder_network.router, dependencies=_auth)
    app.include_router(stakeholder_value_network.router, dependencies=_auth)
    app.include_router(journey_blueprint.router, dependencies=_auth)
    app.include_router(functional_lineage.router, dependencies=_auth)
    app.include_router(generation.router, dependencies=_auth)
    app.include_router(replay.router, dependencies=_auth)
    app.include_router(candidate_studio.router, dependencies=_auth)
    app.include_router(project_intelligence.router, dependencies=_auth)
    app.include_router(probe_patterns.router, dependencies=_auth)
    app.include_router(connectivity.router, dependencies=_auth)
    app.include_router(snapshot_preflight.router, dependencies=_auth)
    app.include_router(replay_readiness.router, dependencies=_auth)
    app.include_router(diagnostics.router, dependencies=_auth)
    app.include_router(system_state.router, dependencies=_auth)
    app.include_router(overview.router, dependencies=_auth)
    app.include_router(assistant.router, dependencies=_auth)
    app.include_router(workspaces.router, dependencies=_auth)
    app.include_router(interview.router, dependencies=_auth)
    app.include_router(interview_intent.router, dependencies=_auth)
    app.include_router(interview_inquiry.router, dependencies=_auth)
    app.include_router(interview_alignment.router, dependencies=_auth)
    app.include_router(interview_metrics.router, dependencies=_auth)
    app.include_router(interview_handoff.router, dependencies=_auth)
    app.include_router(interview_observation.router, dependencies=_auth)
    app.include_router(interview_refresh.router, dependencies=_auth)
    app.include_router(interview_workflow.router, dependencies=_auth)
    app.include_router(interview_brief.router, dependencies=_auth)
    app.include_router(purpose_chain.router, dependencies=_auth)
    app.include_router(interview_change_sets.router, dependencies=_auth)
    app.include_router(question_router.router, dependencies=_auth)
    app.include_router(joint_understanding.router, dependencies=_auth)
    app.include_router(github_connections.router, dependencies=_auth)
    app.include_router(publish_jobs.router, dependencies=_auth)
    app.include_router(cell_fabric.router, dependencies=_auth)
    app.include_router(cell_tasks.router, dependencies=_auth)
    app.include_router(cell_orchestrators.router, dependencies=_auth)
    app.include_router(cell_quality.router, dependencies=_auth)
    app.include_router(cell_root.router, dependencies=_auth)
    app.include_router(cell_improvement.router, dependencies=_auth)
    app.include_router(product_objectives.router, dependencies=_auth)
    app.include_router(product_objectives.milestone_router, dependencies=_auth)
    app.include_router(product_gaps.router, dependencies=_auth)
    app.include_router(product_features.router, dependencies=_auth)
    app.include_router(product_lineage.router, dependencies=_auth)
    return app


app = create_app()

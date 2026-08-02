import os
import sys

import pytest

# Make `app` package importable when running pytest from repo root or from
# apps/control-server.
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


@pytest.fixture(autouse=True)
def _no_background_initial_build(monkeypatch):
    """Keep `POST /interview/sessions` from spawning its worker thread.

    Issue #349 made session creation actually dispatch the initial
    understanding build (the run record must describe a process that really
    ran). In production that worker performs a reasoning-model call; in tests
    it would start an unbounded network attempt on a daemon thread for every
    session any test creates, racing the test's own writes.

    The dispatch itself is covered explicitly in
    `test_interview_workflow.py`, which restores the real function
    (`app.routes.interview._dispatch_initial_understanding_build`) for the
    tests that assert on it. Everywhere else the run record stays `running`,
    which is what a build in flight looks like anyway.
    """
    import app.routes.interview as interview_route

    monkeypatch.setattr(
        interview_route,
        "_dispatch_initial_understanding_build",
        lambda session_row, system_id, run_id: None,
    )

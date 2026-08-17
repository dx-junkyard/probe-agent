"""Tests for Epic #394 Phase 3 (Issue #398): the Exploration Workbench.

The rules that make this phase correct:

1. What must be constant lives on the RUN, so two variants can never have
   been measured against different datasets or contract versions while still
   looking like a comparison.
2. Nothing is composited. There is no score/weight/total anywhere, and
   ranking requires an explicitly named dimension.
3. A gap is never rounded into a win or a loss: `incomparable` and
   `coverage_mismatch` are verdicts, and an unmeasured variant is `unranked`
   rather than last.
4. No executable content can enter through this contract -- a variant
   references the existing Replay/Experiment run that produced its numbers.
5. Completing a comparison adopts nothing.
6. System isolation and additive migration.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app import exploration_workbench as workbench
from app.evolution_node import add_implementation, add_version, create_node
from app.exploration_workbench import (
    HIGHER_IS_BETTER,
    MEASUREMENT_DIMENSIONS,
    ExplorationConflictError,
    ExplorationNotFoundError,
    ExplorationValidationError,
    add_variant,
    attach_execution,
    build_run_projection,
    compare_variants,
    complete_run,
    create_run,
    rank_by_dimension,
    record_measurement,
)


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "exploration-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    return r.cookies.get("probe_session")


def _headers(token, system_id=None):
    headers = {"Authorization": f"Bearer {token}"}
    if system_id is not None:
        headers["X-Probe-System-Id"] = str(system_id)
    return headers


def _create_system(client, token, name):
    r = client.post(
        "/systems",
        json={"name": name, "environment": "test", "description": f"{name} desc"},
        headers=_headers(token),
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _node_with_version(conn, system_id, node_key="explored"):
    node = create_node(conn, system_id=system_id, node_key=node_key)
    version = add_version(
        conn, system_id=system_id, node_id=node["id"], mission="m",
        input_contract={}, output_contract={}, side_effect_class="pure",
        trust_boundary="internal",
    )
    return node, version


def _measurement(dimension, value, state="measured", covered=None, total=None, name=""):
    return {
        "dimension": dimension,
        "metric_name": name,
        "value_state": state,
        "numeric_value": value,
        "covered_case_count": covered,
        "total_case_count": total,
    }


def _insert_snapshot(conn, system_id):
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, file_count,
                total_size, indexed_size, created_at, completed_at)
           VALUES (?, '', 'deadbeef', 'ready', 0, 0, 0, ?, NULL)""",
        (system_id, time.time()),
    )
    return cur.lastrowid


def _insert_experiment(conn, system_id, snapshot_id, status):
    cur = conn.execute(
        """INSERT INTO experiments
               (system_id, feature_id, objective, snapshot_id, baseline_commit,
                config_revision, execution_config, status, created_at)
           VALUES (?, 'feat-1', 'obj', ?, 'deadbeef', 'v1', '{}', ?, ?)""",
        (system_id, snapshot_id, status, time.time()),
    )
    return cur.lastrowid


def _insert_replay_set(conn, system_id):
    cur = conn.execute(
        """INSERT INTO replay_sets (system_id, component_id, created_at)
           VALUES (?, 'comp-1', ?)""",
        (system_id, time.time()),
    )
    return cur.lastrowid


def _insert_replay_run(
    conn, system_id, snapshot_id, status, *, replay_set_id=None, commit_sha="deadbeef"
):
    if replay_set_id is None:
        replay_set_id = _insert_replay_set(conn, system_id)
    cur = conn.execute(
        """INSERT INTO replay_runs
               (system_id, replay_set_id, component_id, snapshot_id, commit_sha,
                symbol_path, symbol_qualified_name, status, trace_set_hash,
                created_at)
           VALUES (?, ?, 'comp-1', ?, ?, 'a.py', 'mod.fn', ?, 'hash', ?)""",
        (system_id, replay_set_id, snapshot_id, commit_sha, status, time.time()),
    )
    return cur.lastrowid


def _executed(conn, system_id, variant_id):
    """Attach a real completed Replay run.

    This is the legitimate sequence a measured number requires: the execution
    that produced the numbers is named FIRST, then the numbers are recorded.
    """
    snapshot_id = _insert_snapshot(conn, system_id)
    replay_run_id = _insert_replay_run(conn, system_id, snapshot_id, "completed")
    return attach_execution(
        conn, system_id=system_id, variant_id=variant_id,
        execution_state="executed", execution_ref_kind="replay_run",
        execution_ref_id=replay_run_id,
    )


def _run_with_variant(conn, system_id, *, node_key="explored", is_baseline=False):
    node, version = _node_with_version(conn, system_id, node_key=node_key)
    run = create_run(
        conn, system_id=system_id, node_id=node["id"],
        node_version_id=version["id"],
    )
    variant = add_variant(
        conn, system_id=system_id, run_id=run["id"], variant_key="v",
        modality="rule", is_baseline=is_baseline,
    )
    return run, variant


def _completable_run(conn, system_id, *, node_key="explored"):
    """A run that satisfies every completion gate.

    A baseline and a candidate, each with a named execution and a recorded
    reading -- i.e. an actual comparison. Every test that needs a COMPLETED
    run builds it through this sequence rather than by writing numbers onto
    unexecuted variants, because that shortcut is precisely what the gates
    now refuse.
    """
    node, version = _node_with_version(conn, system_id, node_key=node_key)
    run = create_run(
        conn, system_id=system_id, node_id=node["id"],
        node_version_id=version["id"],
    )
    baseline = add_variant(
        conn, system_id=system_id, run_id=run["id"], variant_key="base",
        modality="reasoning_llm", is_baseline=True,
    )
    _executed(conn, system_id, baseline["id"])
    record_measurement(
        conn, system_id=system_id, variant_id=baseline["id"],
        dimension="latency", numeric_value=100.0,
    )
    candidate = add_variant(
        conn, system_id=system_id, run_id=run["id"], variant_key="rule",
        modality="rule",
    )
    _executed(conn, system_id, candidate["id"])
    record_measurement(
        conn, system_id=system_id, variant_id=candidate["id"],
        dimension="latency", numeric_value=10.0,
    )
    return node, run, baseline, candidate


# ---------------------------------------------------------------------------
# 1. What must be held constant lives on the run
# ---------------------------------------------------------------------------


class TestRunHoldsTheConstants:
    def test_an_implementation_of_another_contract_version_is_refused(
        self, admin_client
    ):
        """Comparing implementations of two contract versions compares two
        different promises -- the one thing a modality comparison must not
        do."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Versions")
        with get_conn() as conn:
            node, version_1 = _node_with_version(conn, system_id)
            version_2 = add_version(
                conn, system_id=system_id, node_id=node["id"], mission="m2",
                input_contract={}, output_contract={}, side_effect_class="pure",
                trust_boundary="internal",
            )
            other = add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version_2["id"], modality="rule",
            )
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version_1["id"],
            )
            with pytest.raises(ExplorationConflictError):
                add_variant(
                    conn, system_id=system_id, run_id=run["id"], variant_key="v1",
                    modality="rule", implementation_id=other["id"],
                )

    def test_a_declared_modality_must_match_the_implementations(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Modality")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            impl = add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule",
            )
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            with pytest.raises(ExplorationConflictError):
                add_variant(
                    conn, system_id=system_id, run_id=run["id"], variant_key="v1",
                    modality="reasoning_llm", implementation_id=impl["id"],
                )

    def test_only_one_baseline_per_run(self, admin_client):
        """"Which of these numbers is the reference" is not something a
        comparison may be ambiguous about."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Baseline")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="base",
                modality="reasoning_llm", is_baseline=True,
            )
            with pytest.raises(ExplorationConflictError):
                add_variant(
                    conn, system_id=system_id, run_id=run["id"], variant_key="base2",
                    modality="rule", is_baseline=True,
                )

    def test_an_unresolvable_evaluation_policy_is_refused_not_dropped(
        self, admin_client
    ):
        """A run that quietly lost its safety contract would still look like a
        complete comparison."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Policy")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            with pytest.raises(ExplorationNotFoundError):
                create_run(
                    conn, system_id=system_id, node_id=node["id"],
                    node_version_id=version["id"], evaluation_policy_ids=[999999],
                )


# ---------------------------------------------------------------------------
# 2 & 3. Nothing composited; no gap rounded into a difference
# ---------------------------------------------------------------------------


class TestComparisonNeverRounds:
    def test_an_unmeasured_dimension_is_incomparable_not_a_zero_delta(self):
        """"This variant has no token cost" and "its token cost is zero" must
        never produce the same verdict -- otherwise a comparison rewards
        whichever variant simply failed to be measured."""
        comparisons = compare_variants(
            [_measurement("cost", 12.0)],
            [_measurement("cost", None, state="not_applicable")],
        )
        assert len(comparisons) == 1
        assert comparisons[0].verdict == "incomparable"
        assert comparisons[0].delta is None
        assert comparisons[0].variant_state == "not_applicable"

    def test_unsupported_is_also_incomparable(self):
        comparisons = compare_variants(
            [_measurement("latency", 100.0)],
            [_measurement("latency", None, state="unsupported")],
        )
        assert comparisons[0].verdict == "incomparable"

    def test_a_dimension_present_on_only_one_side_is_reported_not_dropped(self):
        """A missing safety measurement on the candidate is exactly what a
        reader must see."""
        comparisons = compare_variants(
            [_measurement("safety", 1.0)],
            [],
        )
        assert len(comparisons) == 1
        assert comparisons[0].dimension == "safety"
        assert comparisons[0].verdict == "incomparable"
        assert comparisons[0].variant_state == "not_measured"

    def test_different_coverage_is_reported_never_compared(self):
        comparisons = compare_variants(
            [_measurement("output_quality", 0.9, covered=100, total=100)],
            [_measurement("output_quality", 0.95, covered=40, total=100)],
        )
        assert comparisons[0].verdict == "coverage_mismatch"
        assert comparisons[0].delta is None
        # The numbers are still shown so a human can judge.
        assert comparisons[0].baseline_value == 0.9
        assert comparisons[0].variant_value == 0.95

    @pytest.mark.parametrize(
        "dimension,baseline,variant,expected",
        [
            ("output_quality", 0.8, 0.9, "better"),
            ("output_quality", 0.9, 0.8, "worse"),
            ("latency", 100.0, 80.0, "better"),
            ("latency", 80.0, 100.0, "worse"),
            ("cost", 10.0, 4.0, "better"),
            ("error_rate", 0.1, 0.2, "worse"),
            ("safety", 1.0, 1.0, "equal"),
        ],
    )
    def test_direction_comes_from_the_explicit_table(
        self, dimension, baseline, variant, expected
    ):
        """Direction is a table, never inferred from a metric's spelling: a
        guess would silently invert the verdict for any name that did not
        match it."""
        comparisons = compare_variants(
            [_measurement(dimension, baseline)], [_measurement(dimension, variant)]
        )
        assert comparisons[0].verdict == expected

    def test_every_dimension_has_an_explicit_direction(self):
        assert set(HIGHER_IS_BETTER) == set(MEASUREMENT_DIMENSIONS)

    def test_ranking_requires_a_named_dimension(self, admin_client):
        with pytest.raises(ExplorationValidationError):
            rank_by_dimension([], "overall")

    def test_unmeasured_variants_are_unranked_not_last(self):
        """Sorting an unmeasured variant to the bottom reads as "worst"."""
        variants = [
            {
                "id": 1, "variant_key": "llm", "modality": "reasoning_llm",
                "measurements": [_measurement("latency", 120.0)],
            },
            {
                "id": 2, "variant_key": "rule", "modality": "rule",
                "measurements": [_measurement("latency", 12.0)],
            },
            {
                "id": 3, "variant_key": "manual", "modality": "manual",
                "measurements": [
                    _measurement("latency", None, state="not_applicable")
                ],
            },
        ]
        ranking = rank_by_dimension(variants, "latency")
        assert [e["variant_key"] for e in ranking["ranked"]] == ["rule", "llm"]
        assert [e["variant_key"] for e in ranking["unranked"]] == ["manual"]

    def test_an_unknown_dimension_has_no_direction_and_fails_closed(self):
        """`HIGHER_IS_BETTER.get(dimension, True)` would silently treat an
        unknown dimension as higher-is-better -- a guessed direction is the
        inversion risk the explicit table exists to prevent."""
        with pytest.raises(ExplorationValidationError):
            compare_variants(
                [_measurement("vibes", 1.0)], [_measurement("vibes", 2.0)]
            )

    def test_mixed_metric_names_require_the_caller_to_disambiguate(self):
        """A baseline measured on one metric must never rank against a
        candidate measured on another -- the same (dimension, metric_name)
        keying `compare_variants` uses."""
        variants = [
            {
                "id": 1, "variant_key": "base", "modality": "reasoning_llm",
                "is_baseline": True,
                "measurements": [_measurement("latency", 120.0, name="p50")],
            },
            {
                "id": 2, "variant_key": "rule", "modality": "rule",
                "measurements": [_measurement("latency", 12.0, name="p95")],
            },
        ]
        with pytest.raises(ExplorationValidationError):
            rank_by_dimension(variants, "latency")

        ranking = rank_by_dimension(variants, "latency", "p50")
        assert ranking["metric_name"] == "p50"
        assert [e["variant_key"] for e in ranking["ranked"]] == ["base"]
        assert [e["variant_key"] for e in ranking["unranked"]] == ["rule"]
        # Excluded with an explicit reason, never silently dropped.
        assert "p95" in ranking["unranked"][0]["reason"]

    def test_a_reading_at_a_different_coverage_is_unranked_not_compared(self):
        variants = [
            {
                "id": 1, "variant_key": "base", "modality": "reasoning_llm",
                "is_baseline": True,
                "measurements": [
                    _measurement("output_quality", 0.9, covered=50, total=50)
                ],
            },
            {
                "id": 2, "variant_key": "rule", "modality": "rule",
                "measurements": [
                    _measurement("output_quality", 0.95, covered=20, total=50)
                ],
            },
        ]
        ranking = rank_by_dimension(variants, "output_quality")
        assert [e["variant_key"] for e in ranking["ranked"]] == ["base"]
        assert [e["variant_key"] for e in ranking["unranked"]] == ["rule"]
        assert "coverage" in ranking["unranked"][0]["reason"]

    def test_disagreeing_coverage_with_no_baseline_ranks_nothing(self):
        """Without a baseline to anchor the basis there is no deterministic
        way to decide which coverage group is "the" ranking -- so none is."""
        variants = [
            {
                "id": 1, "variant_key": "a", "modality": "rule",
                "measurements": [
                    _measurement("output_quality", 0.9, covered=50, total=50)
                ],
            },
            {
                "id": 2, "variant_key": "b", "modality": "manual",
                "measurements": [
                    _measurement("output_quality", 0.95, covered=20, total=50)
                ],
            },
        ]
        ranking = rank_by_dimension(variants, "output_quality")
        assert ranking["ranked"] == []
        assert {e["variant_key"] for e in ranking["unranked"]} == {"a", "b"}
        for entry in ranking["unranked"]:
            assert entry["reason"]

    def test_no_composite_field_exists_on_the_projection(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-NoScore")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            base = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="base",
                modality="reasoning_llm", is_baseline=True,
            )
            _executed(conn, system_id, base["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=base["id"],
                dimension="latency", numeric_value=100.0,
            )
            projection = build_run_projection(
                conn, system_id=system_id, run_id=run["id"]
            )
        for forbidden in ("score", "weight", "total", "overall", "winner", "ranking"):
            assert forbidden not in projection
        for variant in projection["variants"]:
            for forbidden in ("score", "weight", "total", "overall"):
                assert forbidden not in variant


# ---------------------------------------------------------------------------
# 4. Measurement integrity and execution references
# ---------------------------------------------------------------------------


class TestMeasurementIntegrity:
    def test_measured_requires_a_value_and_the_others_forbid_one(self, admin_client):
        """A value paired with a non-measured state is indistinguishable from
        a real reading the moment anything reads the column, not the state."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-States")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule",
            )
            with pytest.raises(ExplorationValidationError):
                record_measurement(
                    conn, system_id=system_id, variant_id=variant["id"],
                    dimension="cost", value_state="measured",
                )
            with pytest.raises(ExplorationValidationError):
                record_measurement(
                    conn, system_id=system_id, variant_id=variant["id"],
                    dimension="cost", value_state="not_applicable", numeric_value=0.0,
                )

    def test_coverage_cannot_exceed_the_total(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Coverage")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule",
            )
            _executed(conn, system_id, variant["id"])
            with pytest.raises(ExplorationValidationError):
                record_measurement(
                    conn, system_id=system_id, variant_id=variant["id"],
                    dimension="output_quality", numeric_value=1.0,
                    covered_case_count=10, total_case_count=5,
                )

    def test_executed_requires_a_resolvable_reference(self, admin_client):
        """A variant claiming to be executed with nothing behind it would let
        unmeasured numbers enter a comparison."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Exec")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule",
            )
            with pytest.raises(ExplorationValidationError):
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed",
                )
            with pytest.raises(ExplorationNotFoundError):
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="experiment",
                    execution_ref_id=999999,
                )

    def test_non_executed_states_must_not_carry_a_reference(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-ExecRef")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule",
            )
            with pytest.raises(ExplorationValidationError):
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="not_executable", execution_ref_kind="experiment",
                    execution_ref_id=1,
                )

    def test_not_executable_is_a_recorded_outcome_not_a_loss(self, admin_client):
        """A rule variant that cannot express a case has not lost on it."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-NotExec")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule",
            )
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant["id"],
                execution_state="not_executable",
                note="the rule cannot express free-form addresses",
            )
        assert row["execution_state"] == "not_executable"
        assert row["execution_ref_id"] is None

    def test_the_variant_contract_accepts_no_executable_content(self):
        """Principle 8: execution stays inside the pinned-snapshot,
        network-off sandbox that Replay and Experiments enforce. A source or
        command parameter here would be a way around it."""
        import inspect

        from app.models import ExplorationVariantCreateIn

        signature = set(inspect.signature(add_variant).parameters)
        assert signature.isdisjoint(
            {"source", "patch", "patch_text", "command", "code", "script"}
        )
        assert set(ExplorationVariantCreateIn.model_fields).isdisjoint(
            {"source", "patch", "patch_text", "command", "code", "script"}
        )


class TestExecutionRefMustBeCompleted:
    """§6.1: the cited id must point at an existing COMPLETED run. A draft,
    running or failed run has no numbers to stand behind, so accepting it
    would let unmeasured numbers enter a comparison under an 'executed'
    label."""

    def test_a_draft_experiment_cannot_back_executed(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-DraftExp")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            experiment_id = _insert_experiment(conn, system_id, snapshot_id, "draft")
            _, variant = _run_with_variant(conn, system_id)
            with pytest.raises(ExplorationConflictError):
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="experiment",
                    execution_ref_id=experiment_id,
                )

    @pytest.mark.parametrize("status", ["running", "failed"])
    def test_a_non_completed_replay_run_is_refused(self, admin_client, status):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, f"EX-Replay-{status}")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(conn, system_id, snapshot_id, status)
            _, variant = _run_with_variant(conn, system_id)
            with pytest.raises(ExplorationConflictError):
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="replay_run",
                    execution_ref_id=replay_run_id,
                )

    def test_a_completed_reference_is_accepted(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-CompletedRef")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed"
            )
            experiment_id = _insert_experiment(
                conn, system_id, snapshot_id, "completed"
            )
            _, variant = _run_with_variant(conn, system_id)
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant["id"],
                execution_state="executed", execution_ref_kind="replay_run",
                execution_ref_id=replay_run_id,
            )
            assert row["execution_state"] == "executed"
            assert row["execution_ref_id"] == replay_run_id

            _, variant_2 = _run_with_variant(conn, system_id, node_key="explored-2")
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant_2["id"],
                execution_state="executed", execution_ref_kind="experiment",
                execution_ref_id=experiment_id,
            )
            assert row["execution_ref_id"] == experiment_id


class TestCompletedRunIsImmutable:
    """A completed run is exactly what #399's establishment gate reads as
    evidence, so its variants' measurements and execution records must not be
    silently rewritable afterwards."""

    def _completed_run(self, conn, system_id):
        _node, run, baseline, _candidate = _completable_run(conn, system_id)
        complete_run(conn, system_id=system_id, run_id=run["id"])
        return run, baseline

    def test_a_measurement_cannot_be_overwritten_after_completion(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Tamper")
        with get_conn() as conn:
            run, variant = self._completed_run(conn, system_id)
            with pytest.raises(ExplorationConflictError):
                record_measurement(
                    conn, system_id=system_id, variant_id=variant["id"],
                    dimension="latency", numeric_value=1.0,
                )
            stored = conn.execute(
                """SELECT numeric_value FROM exploration_measurement
                       WHERE variant_id = ? AND dimension = 'latency'""",
                (variant["id"],),
            ).fetchone()
        assert stored["numeric_value"] == 100.0

    def test_an_execution_cannot_be_attached_after_completion(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-TamperExec")
        with get_conn() as conn:
            run, variant = self._completed_run(conn, system_id)
            with pytest.raises(ExplorationConflictError):
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="not_executable",
                )


class TestMeasuredValueRequiresAnExecution:
    """A number is a claim about something that RAN. Accepting a
    caller-supplied figure on a variant nothing executed is the
    self-reported-number hole: it completes a comparison and becomes #399's
    evidence without anything ever having been measured."""

    def test_a_measured_value_is_refused_before_an_execution_is_attached(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-MeasureOrder")
        with get_conn() as conn:
            _run, variant = _run_with_variant(conn, system_id)
            with pytest.raises(ExplorationConflictError) as excinfo:
                record_measurement(
                    conn, system_id=system_id, variant_id=variant["id"],
                    dimension="latency", numeric_value=12.0,
                )
        assert excinfo.value.reason == "measured_without_execution"

    def test_the_other_states_stay_recordable_without_an_execution(
        self, admin_client
    ):
        """`not_applicable` / `not_measured` / `unsupported` are statements
        ABOUT the absence of a reading -- recording them is how a gap stays
        visible instead of being rounded away."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-GapStates")
        with get_conn() as conn:
            _run, variant = _run_with_variant(conn, system_id)
            for state in ("not_applicable", "not_measured", "unsupported"):
                row = record_measurement(
                    conn, system_id=system_id, variant_id=variant["id"],
                    dimension="cost", metric_name=state, value_state=state,
                )
                assert row["value_state"] == state

    def test_attach_then_measure_is_the_workable_sequence(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-MeasureAfter")
        with get_conn() as conn:
            _run, variant = _run_with_variant(conn, system_id)
            _executed(conn, system_id, variant["id"])
            row = record_measurement(
                conn, system_id=system_id, variant_id=variant["id"],
                dimension="latency", numeric_value=12.0,
            )
        assert row["numeric_value"] == 12.0


class TestExecutionRefMustMatchPinnedInputs:
    """Existing and completed is not enough. A run produced under a different
    dataset, snapshot or commit measured something else, and letting it in
    puts incomparable numbers inside one comparison.

    A fact recorded on only ONE side passes: you cannot verify what was never
    recorded, and refusing it would reject every legitimate reference whose
    table has no column for that fact."""

    def _pinned_run(
        self,
        conn,
        system_id,
        *,
        node_key="pinned",
        snapshot_id=None,
        commit_sha="",
        dataset_ref="",
        implementation_id=None,
        modality="rule",
    ):
        node, version = _node_with_version(conn, system_id, node_key=node_key)
        run = create_run(
            conn, system_id=system_id, node_id=node["id"],
            node_version_id=version["id"], snapshot_id=snapshot_id,
            commit_sha=commit_sha, dataset_kind="replay_set",
            dataset_ref=dataset_ref,
        )
        variant = add_variant(
            conn, system_id=system_id, run_id=run["id"], variant_key="v",
            modality=modality, implementation_id=implementation_id,
        )
        return node, version, run, variant

    def test_a_snapshot_mismatch_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinSnapshot")
        with get_conn() as conn:
            pinned_snapshot = _insert_snapshot(conn, system_id)
            other_snapshot = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, other_snapshot, "completed"
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, snapshot_id=pinned_snapshot
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="replay_run",
                    execution_ref_id=replay_run_id,
                )
        assert excinfo.value.reason == "pinned_input_mismatch"
        assert "snapshot_id" in str(excinfo.value)

    def test_a_commit_mismatch_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinCommit")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed", commit_sha="0123456789ab",
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, commit_sha="ffffffffffff"
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="replay_run",
                    execution_ref_id=replay_run_id,
                )
        assert excinfo.value.reason == "pinned_input_mismatch"
        assert "commit_sha" in str(excinfo.value)

    def test_an_abbreviated_sha_of_the_same_commit_agrees(self, admin_client):
        """A short sha and the full one are the same fact; refusing them
        would make this gate unusable rather than safe."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinShortSha")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed",
                commit_sha="0123456789abcdef0123456789abcdef01234567",
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, commit_sha="0123456"
            )
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant["id"],
                execution_state="executed", execution_ref_kind="replay_run",
                execution_ref_id=replay_run_id,
            )
        assert row["execution_state"] == "executed"

    def test_a_different_replay_set_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinDataset")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            pinned_set = _insert_replay_set(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed"
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, dataset_ref=f"replay_set:{pinned_set}"
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="replay_run",
                    execution_ref_id=replay_run_id,
                )
        assert excinfo.value.reason == "pinned_input_mismatch"
        assert "dataset_replay_set" in str(excinfo.value)

    def test_the_matching_replay_set_is_accepted(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinDatasetOk")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_set_id = _insert_replay_set(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed",
                replay_set_id=replay_set_id,
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, snapshot_id=snapshot_id, commit_sha="deadbeef",
                dataset_ref=f"replay_set:{replay_set_id}",
            )
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant["id"],
                execution_state="executed", execution_ref_kind="replay_run",
                execution_ref_id=replay_run_id,
            )
        assert row["execution_ref_id"] == replay_run_id

    def test_a_fact_recorded_on_only_one_side_passes(self, admin_client):
        """The run pins no snapshot, no commit and no parseable dataset ref;
        the Replay run records all three. Nothing contradicts anything."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinOneSided")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed"
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, dataset_ref="a curated golden corpus"
            )
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant["id"],
                execution_state="executed", execution_ref_kind="replay_run",
                execution_ref_id=replay_run_id,
            )
        assert row["execution_state"] == "executed"

    def test_an_experiment_snapshot_mismatch_is_refused_and_a_match_accepted(
        self, admin_client
    ):
        """`experiments` records the commit under `baseline_commit` and has no
        dataset column at all -- the dataset fact is one-sided there."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinExperiment")
        with get_conn() as conn:
            pinned_snapshot = _insert_snapshot(conn, system_id)
            other_snapshot = _insert_snapshot(conn, system_id)
            mismatched = _insert_experiment(
                conn, system_id, other_snapshot, "completed"
            )
            matching = _insert_experiment(
                conn, system_id, pinned_snapshot, "completed"
            )
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, snapshot_id=pinned_snapshot,
                commit_sha="deadbeef", dataset_ref="replay_set:1",
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="experiment",
                    execution_ref_id=mismatched,
                )
            assert excinfo.value.reason == "pinned_input_mismatch"
            row = attach_execution(
                conn, system_id=system_id, variant_id=variant["id"],
                execution_state="executed", execution_ref_kind="experiment",
                execution_ref_id=matching,
            )
        assert row["execution_ref_id"] == matching

    def test_a_replay_variant_inherits_its_parent_runs_pinned_facts(
        self, admin_client
    ):
        """`replay_variants` has no snapshot/commit column of its own; the run
        it hangs off is what pinned them."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinReplayVariant")
        with get_conn() as conn:
            pinned_snapshot = _insert_snapshot(conn, system_id)
            other_snapshot = _insert_snapshot(conn, system_id)
            parent_run = _insert_replay_run(
                conn, system_id, other_snapshot, "completed"
            )
            replay_variant_id = conn.execute(
                """INSERT INTO replay_variants
                       (system_id, replay_run_id, variant_key, patch_hash, status,
                        created_at)
                   VALUES (?, ?, 'cand', 'hash', 'completed', ?)""",
                (system_id, parent_run, time.time()),
            ).lastrowid
            _n, _v, _run, variant = self._pinned_run(
                conn, system_id, snapshot_id=pinned_snapshot
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed",
                    execution_ref_kind="replay_variant",
                    execution_ref_id=replay_variant_id,
                )
        assert excinfo.value.reason == "pinned_input_mismatch"
        assert "snapshot_id" in str(excinfo.value)

    def test_the_variants_own_implementation_is_checked_too(self, admin_client):
        """The implementation records the snapshot its code was written
        against; an execution at another snapshot measured something other
        than the implementation this variant claims to be."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinImplementation")
        with get_conn() as conn:
            impl_snapshot = _insert_snapshot(conn, system_id)
            other_snapshot = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, other_snapshot, "completed"
            )
            node, version = _node_with_version(conn, system_id, node_key="impl-pin")
            implementation = add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule",
                snapshot_id=impl_snapshot,
            )
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule", implementation_id=implementation["id"],
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="replay_run",
                    execution_ref_id=replay_run_id,
                )
        assert excinfo.value.reason == "pinned_input_mismatch"
        assert "implementation_snapshot_id" in str(excinfo.value)

    def test_the_implementations_commit_is_checked_too(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-PinImplCommit")
        with get_conn() as conn:
            snapshot_id = _insert_snapshot(conn, system_id)
            replay_run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed", commit_sha="0123456789ab",
            )
            node, version = _node_with_version(conn, system_id, node_key="impl-sha")
            implementation = add_implementation(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"], modality="rule",
                commit_sha="ffffffffffff",
            )
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="v",
                modality="rule", implementation_id=implementation["id"],
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                attach_execution(
                    conn, system_id=system_id, variant_id=variant["id"],
                    execution_state="executed", execution_ref_kind="replay_run",
                    execution_ref_id=replay_run_id,
                )
        assert excinfo.value.reason == "pinned_input_mismatch"
        assert "implementation_commit_sha" in str(excinfo.value)

    def test_the_compared_facts_are_a_finite_documented_set(self):
        assert set(workbench.PINNED_INPUT_FACTS) == {
            "snapshot_id",
            "commit_sha",
            "dataset_replay_set",
            "implementation_snapshot_id",
            "implementation_commit_sha",
        }
        # Every ref kind this contract accepts must have a documented column
        # mapping; a kind without one would silently skip the whole gate.
        assert set(workbench._EXECUTION_REF_QUERIES) == set(
            workbench.EXECUTION_REF_KINDS
        )


# ---------------------------------------------------------------------------
# 5. Completing adopts nothing
# ---------------------------------------------------------------------------


class TestCompletion:
    def test_completing_changes_no_node_maturity_and_pins_nothing(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Complete")
        with get_conn() as conn:
            node, run, _baseline, _candidate = _completable_run(conn, system_id)
            complete_run(conn, system_id=system_id, run_id=run["id"])
            after = conn.execute(
                "SELECT maturity, stable_implementation_id FROM evolution_node "
                "WHERE id = ?",
                (node["id"],),
            ).fetchone()
        assert after["maturity"] == "exploring"
        assert after["stable_implementation_id"] is None

    def test_a_run_without_a_baseline_cannot_be_completed(self, admin_client):
        """A difference measured against nothing is not a difference."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-NoBase")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                complete_run(conn, system_id=system_id, run_id=run["id"])
        assert excinfo.value.reason == "missing_baseline"

    def test_a_run_without_a_baseline_reports_comparable_false(self, admin_client):
        """An in-progress run must not read as "compared, and nothing
        differed"."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Comparable")
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
            run = create_run(
                conn, system_id=system_id, node_id=node["id"],
                node_version_id=version["id"],
            )
            projection = build_run_projection(
                conn, system_id=system_id, run_id=run["id"]
            )
        assert projection["comparable"] is False
        assert projection["comparisons"] == {}

    def test_variants_cannot_be_added_to_a_completed_run(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Closed")
        with get_conn() as conn:
            _node, run, _baseline, _candidate = _completable_run(conn, system_id)
            complete_run(conn, system_id=system_id, run_id=run["id"])
            with pytest.raises(ExplorationConflictError):
                add_variant(
                    conn, system_id=system_id, run_id=run["id"], variant_key="late",
                    modality="reasoning_llm",
                )


class TestCompletionRequiresAnActualComparison:
    """A completed run is what #399's establishment gate reads as evidence, so
    closing one must mean something was compared. Each refusal below closes a
    run that would otherwise LOOK complete while containing no comparison."""

    def _open_run(self, conn, system_id, node_key="incomplete"):
        node, version = _node_with_version(conn, system_id, node_key=node_key)
        run = create_run(
            conn, system_id=system_id, node_id=node["id"],
            node_version_id=version["id"],
        )
        baseline = add_variant(
            conn, system_id=system_id, run_id=run["id"], variant_key="base",
            modality="reasoning_llm", is_baseline=True,
        )
        return run, baseline

    def test_a_baseline_alone_is_not_a_comparison(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-BaselineOnly")
        with get_conn() as conn:
            run, baseline = self._open_run(conn, system_id)
            _executed(conn, system_id, baseline["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=baseline["id"],
                dimension="latency", numeric_value=100.0,
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                complete_run(conn, system_id=system_id, run_id=run["id"])
        assert excinfo.value.reason == "no_candidate_variant"

    def test_an_unresolved_execution_state_blocks_completion(self, admin_client):
        """`not_executed` is the column default, so it is also what a variant
        whose execution was never recorded reads as -- nobody has said what
        happened to it."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Unresolved")
        with get_conn() as conn:
            run, baseline = self._open_run(conn, system_id)
            _executed(conn, system_id, baseline["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=baseline["id"],
                dimension="latency", numeric_value=100.0,
            )
            add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="rule",
                modality="rule",
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                complete_run(conn, system_id=system_id, run_id=run["id"])
        assert excinfo.value.reason == "execution_unresolved"

    def test_an_executed_variant_with_no_measurement_blocks_completion(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Unmeasured")
        with get_conn() as conn:
            run, baseline = self._open_run(conn, system_id)
            _executed(conn, system_id, baseline["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=baseline["id"],
                dimension="latency", numeric_value=100.0,
            )
            candidate = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="rule",
                modality="rule",
            )
            _executed(conn, system_id, candidate["id"])
            with pytest.raises(ExplorationConflictError) as excinfo:
                complete_run(conn, system_id=system_id, run_id=run["id"])
        assert excinfo.value.reason == "executed_without_measurement"

    def test_a_measured_value_left_on_an_unexecuted_variant_blocks_completion(
        self, admin_client
    ):
        """`record_measurement`'s gate applies when the number is written;
        re-attaching a different execution state afterwards would otherwise
        leave a self-reported figure behind."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Detached")
        with get_conn() as conn:
            _node, run, _baseline, candidate = _completable_run(conn, system_id)
            attach_execution(
                conn, system_id=system_id, variant_id=candidate["id"],
                execution_state="not_executable",
            )
            with pytest.raises(ExplorationConflictError) as excinfo:
                complete_run(conn, system_id=system_id, run_id=run["id"])
        assert excinfo.value.reason == "measured_without_execution"

    def test_a_fully_resolved_run_including_not_executable_completes(
        self, admin_client
    ):
        """`not_executable` is a recorded OUTCOME, never an unresolved state:
        a rule variant that cannot express these cases has not lost on them,
        and #398 requires that result be preserved rather than blocked."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Resolved")
        with get_conn() as conn:
            run, baseline = self._open_run(conn, system_id)
            _executed(conn, system_id, baseline["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=baseline["id"],
                dimension="latency", numeric_value=100.0,
            )
            measured = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="rule",
                modality="rule",
            )
            _executed(conn, system_id, measured["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=measured["id"],
                dimension="latency", numeric_value=8.0,
            )
            blocked = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="manual",
                modality="manual",
            )
            attach_execution(
                conn, system_id=system_id, variant_id=blocked["id"],
                execution_state="not_executable",
                note="a human cannot be replayed over 50 cases",
            )
            record_measurement(
                conn, system_id=system_id, variant_id=blocked["id"],
                dimension="latency", value_state="not_applicable",
            )
            row = complete_run(
                conn, system_id=system_id, run_id=run["id"],
                conclusion_note="the rule holds on the covered cases",
            )
        assert row["status"] == "completed"
        assert row["conclusion_note"] == "the rule holds on the covered cases"

    def test_an_unsupported_variant_also_does_not_block(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-Unsupported")
        with get_conn() as conn:
            _node, run, _baseline, _candidate = _completable_run(conn, system_id)
            extra = add_variant(
                conn, system_id=system_id, run_id=run["id"], variant_key="small",
                modality="small_model",
            )
            attach_execution(
                conn, system_id=system_id, variant_id=extra["id"],
                execution_state="unsupported",
            )
            row = complete_run(conn, system_id=system_id, run_id=run["id"])
        assert row["status"] == "completed"

    def test_the_refusal_reasons_are_a_finite_documented_set(self):
        assert set(workbench.COMPLETION_REFUSAL_REASONS) == {
            "missing_baseline",
            "no_candidate_variant",
            "execution_unresolved",
            "executed_without_measurement",
            "measured_without_execution",
        }
        # `not_executed` is the only unresolved state; the two recorded
        # outcomes must never be treated as "nothing was said".
        assert workbench.UNRESOLVED_EXECUTION_STATES == ("not_executed",)
        assert set(workbench.UNRESOLVED_EXECUTION_STATES) <= set(
            workbench.EXECUTION_STATES
        )
        assert {"not_executable", "unsupported"}.isdisjoint(
            workbench.UNRESOLVED_EXECUTION_STATES
        )

    def test_completion_over_http_is_a_409_not_a_silent_success(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-CompleteHttp")
        with get_conn() as conn:
            run, baseline = self._open_run(conn, system_id)
            _executed(conn, system_id, baseline["id"])
            record_measurement(
                conn, system_id=system_id, variant_id=baseline["id"],
                dimension="latency", numeric_value=100.0,
            )
        r = admin_client.post(
            f"/exploration/runs/{run['id']}/complete",
            json={"conclusion_note": ""},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 409, r.text
        assert "candidate" in r.json()["detail"]


# ---------------------------------------------------------------------------
# 6. Isolation, migration and the HTTP boundary
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_runs_and_variants_are_system_scoped(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "EX-IsoA")
        sys_b = _create_system(admin_client, token, "EX-IsoB")
        with get_conn() as conn:
            node, version = _node_with_version(conn, sys_a)
            run = create_run(
                conn, system_id=sys_a, node_id=node["id"],
                node_version_id=version["id"],
            )
            variant = add_variant(
                conn, system_id=sys_a, run_id=run["id"], variant_key="v",
                modality="rule",
            )
            with pytest.raises(ExplorationNotFoundError):
                build_run_projection(conn, system_id=sys_b, run_id=run["id"])
            with pytest.raises(ExplorationNotFoundError):
                record_measurement(
                    conn, system_id=sys_b, variant_id=variant["id"],
                    dimension="latency", numeric_value=1.0,
                )
            # A Node in another System cannot anchor a run here either.
            with pytest.raises(ExplorationNotFoundError):
                create_run(
                    conn, system_id=sys_b, node_id=node["id"],
                    node_version_id=version["id"],
                )


class TestMigration:
    def test_new_tables_appear_via_init_db(self, admin_client):
        from app.db import get_conn

        with get_conn() as conn:
            names = {
                row["name"]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        assert {
            "exploration_run", "exploration_variant", "exploration_measurement"
        } <= names


class TestApi:
    def _setup(self, admin_client, name="EX-Api"):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, name)
        with get_conn() as conn:
            node, version = _node_with_version(conn, system_id)
        return token, system_id, node, version

    def _completed_replay_run(self, system_id, *, replay_set_id=None, snapshot_id=None):
        """A real completed Replay run to hang an `executed` variant on."""
        from app.db import get_conn

        with get_conn() as conn:
            if snapshot_id is None:
                snapshot_id = _insert_snapshot(conn, system_id)
            if replay_set_id is None:
                replay_set_id = _insert_replay_set(conn, system_id)
            run_id = _insert_replay_run(
                conn, system_id, snapshot_id, "completed",
                replay_set_id=replay_set_id,
            )
        return run_id, replay_set_id, snapshot_id

    def _attach_executed(self, client, token, system_id, variant_id, replay_run_id):
        r = client.post(
            f"/exploration/variants/{variant_id}/execution",
            json={
                "execution_state": "executed",
                "execution_ref_kind": "replay_run",
                "execution_ref_id": replay_run_id,
            },
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        return r.json()

    def test_end_to_end_modality_comparison_over_http(self, admin_client):
        """The Epic's central capability: an LLM implementation and a rule
        implementation of the SAME contract, measured on the same dataset.

        The dataset the run pins is the Replay Set both executions actually
        ran against -- not a decorative string. Measurements are recorded
        only after each variant's execution is attached.
        """
        token, system_id, node, version = self._setup(admin_client)
        llm_run_id, replay_set_id, snapshot_id = self._completed_replay_run(system_id)
        rule_run_id, _, _ = self._completed_replay_run(
            system_id, replay_set_id=replay_set_id, snapshot_id=snapshot_id
        )

        r = admin_client.post(
            "/exploration/runs",
            json={
                "node_id": node["id"],
                "node_version_id": version["id"],
                "objective": "can a rule replace the model here",
                "dataset_kind": "replay_set",
                "dataset_ref": f"replay_set:{replay_set_id}",
                "snapshot_id": snapshot_id,
                "commit_sha": "deadbeef",
            },
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        run_id = r.json()["id"]
        assert r.json()["comparable"] is False

        r = admin_client.post(
            f"/exploration/runs/{run_id}/variants",
            json={
                "variant_key": "llm", "modality": "reasoning_llm", "is_baseline": True,
            },
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        baseline_id = r.json()["id"]

        r = admin_client.post(
            f"/exploration/runs/{run_id}/variants",
            json={"variant_key": "rule", "modality": "rule"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        rule_id = r.json()["id"]

        self._attach_executed(admin_client, token, system_id, baseline_id, llm_run_id)
        self._attach_executed(admin_client, token, system_id, rule_id, rule_run_id)

        for variant_id, latency, cost_state, cost in (
            (baseline_id, 900.0, "measured", 0.004),
            (rule_id, 8.0, "not_applicable", None),
        ):
            r = admin_client.post(
                f"/exploration/variants/{variant_id}/measurements",
                json={
                    "dimension": "latency", "numeric_value": latency, "unit": "ms",
                    "covered_case_count": 50, "total_case_count": 50,
                },
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text
            r = admin_client.post(
                f"/exploration/variants/{variant_id}/measurements",
                json={
                    "dimension": "cost", "value_state": cost_state,
                    "numeric_value": cost, "unit": "usd",
                },
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        r = admin_client.get(
            f"/exploration/runs/{run_id}", headers=_headers(token, system_id)
        )
        body = r.json()
        assert body["comparable"] is True
        comparisons = {
            c["dimension"]: c for c in body["comparisons"][str(rule_id)]
        }
        # Latency: a real, comparable improvement.
        assert comparisons["latency"]["verdict"] == "better"
        # Cost: the rule has none, which is NOT "zero cost, therefore better".
        assert comparisons["cost"]["verdict"] == "incomparable"
        assert comparisons["cost"]["delta"] is None

    def test_ranking_requires_a_dimension_and_rejects_an_unknown_one(
        self, admin_client
    ):
        token, system_id, node, version = self._setup(admin_client)
        r = admin_client.post(
            "/exploration/runs",
            json={"node_id": node["id"], "node_version_id": version["id"]},
            headers=_headers(token, system_id),
        )
        run_id = r.json()["id"]

        r = admin_client.get(
            f"/exploration/runs/{run_id}/ranking", headers=_headers(token, system_id)
        )
        assert r.status_code == 422, r.text  # dimension is required

        r = admin_client.get(
            f"/exploration/runs/{run_id}/ranking?dimension=overall",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "unknown_dimension"

    def test_ranking_response_exposes_metric_name_and_unranked_reason(
        self, admin_client
    ):
        """The exclusion audit trail must survive the HTTP boundary: a reader
        of the API response has to see WHICH metric the ranking was computed
        over and WHY an unranked variant was excluded, or the unranked group
        is a bare verdict they cannot check."""
        token, system_id, node, version = self._setup(admin_client)
        r = admin_client.post(
            "/exploration/runs",
            json={"node_id": node["id"], "node_version_id": version["id"]},
            headers=_headers(token, system_id),
        )
        run_id = r.json()["id"]
        variant_ids = {}
        for key, modality, is_baseline in (
            ("base", "reasoning_llm", True),
            ("rule", "rule", False),
        ):
            r = admin_client.post(
                f"/exploration/runs/{run_id}/variants",
                json={
                    "variant_key": key, "modality": modality,
                    "is_baseline": is_baseline,
                },
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text
            variant_ids[key] = r.json()["id"]
            replay_run_id, _, _ = self._completed_replay_run(system_id)
            self._attach_executed(
                admin_client, token, system_id, variant_ids[key], replay_run_id
            )
        for key, metric in (("base", "p50"), ("rule", "p95")):
            r = admin_client.post(
                f"/exploration/variants/{variant_ids[key]}/measurements",
                json={
                    "dimension": "latency", "metric_name": metric,
                    "numeric_value": 10.0, "unit": "ms",
                    "covered_case_count": 5, "total_case_count": 5,
                },
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        r = admin_client.get(
            f"/exploration/runs/{run_id}/ranking"
            "?dimension=latency&metric_name=p50",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["metric_name"] == "p50"
        assert [e["variant_key"] for e in body["ranked"]] == ["base"]
        assert [e["variant_key"] for e in body["unranked"]] == ["rule"]
        assert "p95" in body["unranked"][0]["reason"]

    def test_a_run_in_another_system_is_404(self, admin_client):
        token, system_id, node, version = self._setup(admin_client)
        other = _create_system(admin_client, token, "EX-ApiOther")
        r = admin_client.post(
            "/exploration/runs",
            json={"node_id": node["id"], "node_version_id": version["id"]},
            headers=_headers(token, system_id),
        )
        run_id = r.json()["id"]
        r = admin_client.get(
            f"/exploration/runs/{run_id}", headers=_headers(token, other)
        )
        assert r.status_code == 404, r.text

    def test_sdk_api_token_is_rejected(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "EX-ApiAuth")
        r = admin_client.post(
            "/tokens",
            json={"name": "sdk", "kind": "api", "system_id": system_id},
            headers=_headers(token),
        )
        if r.status_code not in (200, 201):
            pytest.skip(f"token issuing endpoint unavailable: {r.status_code}")
        api_token = r.json().get("token")
        r = admin_client.get(
            "/exploration/runs/1", headers={"Authorization": f"Bearer {api_token}"}
        )
        assert r.status_code == 403, r.text

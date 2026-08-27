"""Tests for Epic #394 Phase 2 (Issue #397): the Design Studio.

The rules that make this phase correct, one class each:

1. An AI-PROPOSED link never reads as a developer-CONFIRMED relation, and a
   Purpose element's own state is carried through independently of it.
2. A failed reasoning run persists the failure and ZERO candidates -- no
   heuristic decomposition is ever stored (Principle 6), and a partially
   valid response is rejected whole rather than filtered down.
3. Adopting a candidate is the ONLY way Nodes get created, it is
   `decision_method: manual`, it cannot be done twice, and a colliding
   node_key aborts the whole adoption rather than leaving half a cut behind.
4. The three evaluation contracts stay separate: grouped by level, criteria
   and floors in separate lists, and no score/weight/total field anywhere
   (ADR-7).
5. An unresolvable handoff reference makes the bundle `incomplete` and is
   named -- never silently dropped.
6. System isolation, and additive migration.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from app import node_design
from app.capability_graph import confirm_capability_graph
from app.evolution_node import add_link, create_node
from app.llm import LLMConfig, LLMError, MockLLMClient
from app.node_design import (
    CANDIDATE_DECISIONS,
    EVALUATION_LEVELS,
    NodeDesignConflictError,
    NodeDesignNotFoundError,
    NodeDesignValidationError,
    assemble_handoff,
    create_evaluation_policy,
    decide_candidate,
    derive_node_lineage,
    get_handoff,
    list_evaluation_policies,
    persist_decomposition,
    propose_decomposition,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "node-design-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)

    from app.llm import get_llm_client

    get_llm_client.cache_clear()
    from app.main import app

    with TestClient(app) as client:
        yield client


def _login(client, username="root", password="s3cret"):
    r = client.post("/auth/login", json={"username": username, "password": password})
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


class _StubClient:
    """A non-mock LLM client returning a canned body.

    `propose_decomposition` refuses a `MockLLMClient` outright (that is the
    Principle 6 gate), so exercising the parsing path at all requires a
    client that is not one.
    """

    def __init__(self, body):
        self._body = body

    def generate_text(self, messages, temperature=0.0, max_tokens=None, timeout=None):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


REASONING_CONFIG = LLMConfig(
    provider="anthropic",
    model="claude-opus-4-20250514",
    api_key="test-key",
    base_url="",
    timeout=30.0,
)


def _node_payload(key="normalize", **overrides):
    payload = {
        "node_key": key,
        "display_name": key,
        "mission": "normalize an address",
        "scope": "string in, string out",
        "out_of_scope": "does not geocode",
        "input_contract": {"address": "str"},
        "output_contract": {"normalized": "str"},
        "side_effect_class": "pure",
        "trust_boundary": "internal",
        "dependencies": [],
        "observability": "callable directly",
        "open_questions": [],
    }
    payload.update(overrides)
    return payload


def _snapshot_and_session(conn, system_id):
    """A ready snapshot + interview session, the two FK anchors the
    Capability Graph's confirmation rows require."""
    now = time.time()
    cur = conn.execute(
        """INSERT INTO repository_snapshots
               (system_id, repo_path, commit_sha, status, created_at, completed_at)
           VALUES (?, '/tmp/repo', 'abc123', 'ready', ?, ?)""",
        (system_id, now, now),
    )
    snapshot_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO interview_session
               (system_id, snapshot_id, title, focus, status, stage,
                created_at, updated_at)
           VALUES (?, ?, '', '', 'open', 'purpose_confirmation', ?, ?)""",
        (system_id, snapshot_id, now, now),
    )
    return snapshot_id, cur.lastrowid


def _confirm_capability_graph(conn, system_id, names=("Checkout",)):
    """Confirm a Capability composition through #312's own writer, so the
    entity ids under test are the real stable identities."""
    snapshot_id, session_id = _snapshot_and_session(conn, system_id)
    understanding = {
        "core_capabilities": [
            {"name": name, "summary": f"{name} summary", "children": []}
            for name in names
        ],
        "capability_elements": [],
        "supporting_elements": [],
        "api_boundaries": [],
    }
    now = time.time()
    cur = conn.execute(
        """INSERT INTO understanding_revision
               (session_id, system_id, snapshot_id, intelligence_run_id,
                current_understanding, gap_analysis, created_at)
           VALUES (?, ?, ?, NULL, ?, NULL, ?)""",
        (session_id, system_id, snapshot_id, json.dumps(understanding), now),
    )
    revision_id = cur.lastrowid
    graph = confirm_capability_graph(
        conn,
        system_id=system_id,
        session_id=session_id,
        revision_id=revision_id,
        revision_created_at=now,
        current_understanding=understanding,
        actor="alice",
        now=now + 0.1,
    )
    return graph, snapshot_id, session_id


def _insert_feature_draft(conn, system_id, snapshot_id, feature_id, name):
    now = time.time()
    cur = conn.execute(
        """INSERT INTO intelligence_runs
               (system_id, snapshot_id, run_type, provider, model, prompt_version,
                schema_version, decision_method, status, started_at)
           VALUES (?, ?, 'repository_drafts', 'anthropic', 'm', 'p', 's',
                   'reasoning_llm', 'completed', ?)""",
        (system_id, snapshot_id, now),
    )
    run_id = cur.lastrowid
    conn.execute(
        """INSERT INTO feature_drafts
               (system_id, intelligence_run_id, snapshot_id, feature_id, name,
                created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (system_id, run_id, snapshot_id, feature_id, name, now),
    )


def _insert_trace_span(conn, system_id, flow_id, trace_id="t-1"):
    conn.execute(
        """INSERT INTO trace_spans
               (system_id, trace_id, component_id, flow_id, timestamp)
           VALUES (?, ?, 'svc', ?, ?)""",
        (system_id, trace_id, flow_id, time.time()),
    )


def _persist_scoped_proposal(conn, system_id, *, capability_ref="", flow_ref=""):
    """A real proposal + candidates, scoped to a Capability and/or a Flow."""
    result = propose_decomposition(
        _StubClient(_good_response()), REASONING_CONFIG, scope_summary="s"
    )
    return persist_decomposition(
        conn,
        system_id=system_id,
        result=result,
        scope_summary="s",
        capability_ref=capability_ref,
        flow_ref=flow_ref,
    )


def _insert_raw_candidate(conn, system_id, nodes, candidate_key="cut-raw"):
    """Store a candidate row directly, bypassing `propose_decomposition`'s
    parse-time validation -- the stored-but-invalid shape the adoption
    pre-flight must refuse with zero writes."""
    now = time.time()
    cur = conn.execute(
        """INSERT INTO node_decomposition_proposal
               (system_id, scope_summary, status, created_at)
           VALUES (?, 's', 'proposed', ?)""",
        (system_id, now),
    )
    proposal_id = cur.lastrowid
    cur = conn.execute(
        """INSERT INTO node_decomposition_candidate
               (proposal_id, system_id, candidate_key, summary, nodes_json,
                created_at)
           VALUES (?, ?, ?, 's', ?, ?)""",
        (proposal_id, system_id, candidate_key, json.dumps(nodes), now),
    )
    return cur.lastrowid


def _good_response(nodes=None, candidate_key="cut-a"):
    return json.dumps(
        {
            "candidates": [
                {
                    "candidate_key": candidate_key,
                    "summary": "two nodes split at the external boundary",
                    "rationale": "the parser and the lookup fail differently",
                    "open_questions": ["is the lookup cacheable?"],
                    "nodes": nodes or [_node_payload()],
                },
                {
                    "candidate_key": "cut-b",
                    "summary": "one node",
                    "rationale": "keeps the contract simple",
                    "open_questions": [],
                    "nodes": [_node_payload("normalize-and-lookup")],
                },
            ]
        }
    )


# ---------------------------------------------------------------------------
# 1. Proposed vs confirmed lineage
# ---------------------------------------------------------------------------


class TestLineageProvenance:
    def test_ai_proposed_link_never_reads_as_developer_confirmed(self, admin_client):
        """The link's own decision_method IS the proposal/confirmation axis.
        Confirming a relation is a human act; an LLM suggesting one is not."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Provenance")

        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="lineage-node")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="capability", target_ref="cap-1",
                decision_method="reasoning_llm", created_by="agent",
            )
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="purpose_element", target_ref="core_capability:abc",
                decision_method="manual", created_by="alice",
            )
            lineage = derive_node_lineage(
                conn, system_id=system_id, node_id=node["id"]
            )

        by_ref = {r["target_ref"]: r for r in lineage["relations"]}
        assert by_ref["cap-1"]["relation_status"] == "proposed"
        assert by_ref["core_capability:abc"]["relation_status"] == "confirmed"
        assert lineage["confirmed_relation_count"] == 1
        assert lineage["proposed_relation_count"] == 1
        # No completeness score: #397 forbids collapsing design state into a
        # number, and "lineage 50% complete" would be exactly that.
        for forbidden in ("score", "completeness", "confidence", "percent"):
            assert forbidden not in lineage

    def test_element_state_is_independent_of_relation_status(self, admin_client):
        """A confirmed relation to an element the developer never confirmed
        is a real state, and both halves must stay visible."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ElementState")

        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="independent")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="purpose_element", target_ref="desired_change",
                decision_method="manual", created_by="alice",
            )
            lineage = derive_node_lineage(
                conn, system_id=system_id, node_id=node["id"],
                purpose_elements={"desired_change": {"state": "unknown", "name": "V"}},
            )

        relation = lineage["relations"][0]
        assert relation["relation_status"] == "confirmed"
        assert relation["element_state"] == "unknown"
        assert lineage["purpose_frame_supplied"] is True

    def test_absent_purpose_frame_is_not_the_same_as_no_match(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-NoFrame")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="no-frame")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="capability", target_ref="cap-x", created_by="alice",
            )
            lineage = derive_node_lineage(conn, system_id=system_id, node_id=node["id"])

        assert lineage["purpose_frame_supplied"] is False
        assert lineage["relations"][0]["element_state"] == "unresolved"

    def test_implementation_links_are_not_design_lineage(self, admin_client):
        """A component/probe_point link is an implementation fact, not a
        design relation, and must not inflate the lineage."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ImplLinks")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="impl-links")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="component", target_ref="svc-a", created_by="alice",
            )
            lineage = derive_node_lineage(conn, system_id=system_id, node_id=node["id"])
        assert lineage["relations"] == []


class TestLineageTargetResolution:
    """Every link kind resolves against ITS OWN canonical source.

    Before this, a `capability` / `flow` / `feature` link was looked up in
    the caller's Purpose Frame map, so it could only resolve when its
    `target_ref` happened to collide with a Purpose element id -- a target
    that plainly exists still read as `unresolved` with no name.
    """

    def test_a_capability_link_resolves_to_the_confirmed_entity(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-CapResolve")
        with get_conn() as conn:
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
            node = create_node(conn, system_id=system_id, node_key="cap-resolve")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="capability", target_ref=str(entity_id), created_by="alice",
            )
            lineage = derive_node_lineage(
                conn, system_id=system_id, node_id=node["id"]
            )

        relation = lineage["relations"][0]
        assert relation["target_resolution"] == "resolved"
        assert relation["target_source"] == "capability_graph"
        assert relation["target_name"] == "Checkout"
        # The entity's OWN state: it is part of the current confirmed
        # composition. Independent of the link having been confirmed.
        assert relation["element_state"] == "confirmed"
        assert relation["relation_status"] == "confirmed"
        # The Purpose Frame was never consulted for this kind.
        assert lineage["purpose_frame_supplied"] is False

    def test_a_capability_dropped_by_a_later_confirmation_reads_superseded(
        self, admin_client
    ):
        """Existing-but-no-longer-composed is a real third answer: neither
        `confirmed` nor `unresolved`."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-CapSuperseded")
        with get_conn() as conn:
            graph, snapshot_id, session_id = _confirm_capability_graph(
                conn, system_id, names=("Checkout", "Refund")
            )
            refund_id = next(
                n["entity_id"] for n in graph["nodes"] if n["name"] == "Refund"
            )
            # A second confirmation of the same session drops "Refund".
            now = time.time()
            understanding = {
                "core_capabilities": [
                    {"name": "Checkout", "summary": "Checkout summary", "children": []}
                ],
                "capability_elements": [],
                "supporting_elements": [],
                "api_boundaries": [],
            }
            cur = conn.execute(
                """INSERT INTO understanding_revision
                       (session_id, system_id, snapshot_id, intelligence_run_id,
                        current_understanding, gap_analysis, created_at)
                   VALUES (?, ?, ?, NULL, ?, NULL, ?)""",
                (session_id, system_id, snapshot_id, json.dumps(understanding), now),
            )
            confirm_capability_graph(
                conn, system_id=system_id, session_id=session_id,
                revision_id=cur.lastrowid, revision_created_at=now,
                current_understanding=understanding, actor="alice", now=now + 0.1,
            )

            node = create_node(conn, system_id=system_id, node_key="cap-superseded")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="capability", target_ref=str(refund_id), created_by="alice",
            )
            lineage = derive_node_lineage(conn, system_id=system_id, node_id=node["id"])

        relation = lineage["relations"][0]
        assert relation["target_resolution"] == "resolved"
        assert relation["element_state"] == "superseded"
        assert relation["target_name"] == "Refund"

    def test_a_capability_ref_is_never_matched_by_display_name(self, admin_client):
        """#312: names never determine identity. A ref that is not the
        stable entity id is `unresolved` -- never fuzzy-matched to a
        similarly named Capability (Principle 6)."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-CapByName")
        with get_conn() as conn:
            _confirm_capability_graph(conn, system_id)
            node = create_node(conn, system_id=system_id, node_key="cap-by-name")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="capability", target_ref="Checkout", created_by="alice",
            )
            lineage = derive_node_lineage(conn, system_id=system_id, node_id=node["id"])

        relation = lineage["relations"][0]
        assert relation["target_resolution"] == "unresolved"
        assert relation["element_state"] == "unresolved"
        assert relation["target_name"] is None

    def test_a_flow_link_resolves_to_an_observed_flow(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-FlowResolve")
        with get_conn() as conn:
            _insert_trace_span(conn, system_id, "checkout-flow")
            node = create_node(conn, system_id=system_id, node_key="flow-resolve")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="flow", target_ref="checkout-flow", created_by="alice",
            )
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="flow", target_ref="never-seen", created_by="alice",
            )
            lineage = derive_node_lineage(conn, system_id=system_id, node_id=node["id"])

        by_ref = {r["target_ref"]: r for r in lineage["relations"]}
        assert by_ref["checkout-flow"]["target_resolution"] == "resolved"
        assert by_ref["checkout-flow"]["target_source"] == "flow_lineage"
        assert by_ref["checkout-flow"]["element_state"] == "observed"
        assert by_ref["never-seen"]["target_resolution"] == "unresolved"
        assert by_ref["never-seen"]["element_state"] == "unresolved"

    def test_a_feature_link_resolves_to_the_feature_map(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-FeatureResolve")
        with get_conn() as conn:
            snapshot_id, _ = _snapshot_and_session(conn, system_id)
            _insert_feature_draft(
                conn, system_id, snapshot_id, "feat-checkout", "Checkout feature"
            )
            node = create_node(conn, system_id=system_id, node_key="feature-resolve")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="feature", target_ref="feat-checkout", created_by="alice",
            )
            lineage = derive_node_lineage(conn, system_id=system_id, node_id=node["id"])

        relation = lineage["relations"][0]
        assert relation["target_resolution"] == "resolved"
        assert relation["target_source"] == "feature_map"
        assert relation["target_name"] == "Checkout feature"
        # The Feature Map records no finite confirmation state for a
        # Feature; saying `unresolved` for something that demonstrably
        # exists would be a different, false claim.
        assert relation["element_state"] == "not_applicable"

    def test_a_target_of_another_system_never_resolves(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "ND-ResolveIsoA")
        sys_b = _create_system(admin_client, token, "ND-ResolveIsoB")
        with get_conn() as conn:
            graph, snapshot_id, _ = _confirm_capability_graph(conn, sys_a)
            entity_id = graph["nodes"][0]["entity_id"]
            _insert_trace_span(conn, sys_a, "a-flow")
            _insert_feature_draft(conn, sys_a, snapshot_id, "feat-a", "A feature")

            node_b = create_node(conn, system_id=sys_b, node_key="cross-system")
            for link_kind, ref in (
                ("capability", str(entity_id)),
                ("flow", "a-flow"),
                ("feature", "feat-a"),
            ):
                add_link(
                    conn, system_id=sys_b, node_id=node_b["id"],
                    link_kind=link_kind, target_ref=ref, created_by="mallory",
                )
            lineage = derive_node_lineage(conn, system_id=sys_b, node_id=node_b["id"])

        assert {r["target_resolution"] for r in lineage["relations"]} == {"unresolved"}
        assert all(r["target_name"] is None for r in lineage["relations"])

    def test_an_unsupplied_purpose_frame_is_unavailable_not_unresolved(
        self, admin_client
    ):
        """`unavailable` (the source was never consulted) and `unresolved`
        (it was, and the target is not there) are different answers."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-PurposeUnavailable")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="purpose-unavail")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="purpose_element", target_ref="desired_change",
                created_by="alice",
            )
            without_frame = derive_node_lineage(
                conn, system_id=system_id, node_id=node["id"]
            )
            with_frame = derive_node_lineage(
                conn, system_id=system_id, node_id=node["id"],
                purpose_elements={"beneficiary_problem": {"state": "confirmed"}},
            )
            resolved = derive_node_lineage(
                conn, system_id=system_id, node_id=node["id"],
                purpose_elements={
                    "desired_change": {"state": "hypothesis", "name": "V"}
                },
            )

        assert without_frame["relations"][0]["target_resolution"] == "unavailable"
        assert with_frame["relations"][0]["target_resolution"] == "unresolved"
        assert resolved["relations"][0]["target_resolution"] == "resolved"
        assert resolved["relations"][0]["target_source"] == "purpose_frame"
        assert resolved["relations"][0]["element_state"] == "hypothesis"

    def test_lineage_endpoint_reports_resolved_targets(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiResolve")
        with get_conn() as conn:
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
            _insert_trace_span(conn, system_id, "api-flow")
            node = create_node(conn, system_id=system_id, node_key="api-resolve")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="capability", target_ref=str(entity_id), created_by="alice",
            )
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="flow", target_ref="api-flow", created_by="alice",
            )

        r = admin_client.get(
            f"/node-design/nodes/{node['id']}/lineage",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        by_kind = {rel["link_kind"]: rel for rel in r.json()["relations"]}
        assert by_kind["capability"]["target_name"] == "Checkout"
        assert by_kind["capability"]["element_state"] == "confirmed"
        assert by_kind["capability"]["target_resolution"] == "resolved"
        assert by_kind["flow"]["element_state"] == "observed"
        assert by_kind["flow"]["target_resolution"] == "resolved"


# ---------------------------------------------------------------------------
# 2. Fail-closed reasoning
# ---------------------------------------------------------------------------


class TestDecompositionFailClosed:
    def test_mock_client_is_refused(self):
        result = propose_decomposition(
            MockLLMClient(),
            LLMConfig(
                provider="mock", model="mock", api_key="", base_url="", timeout=30.0
            ),
            scope_summary="anything",
        )
        assert result.error is not None
        assert result.candidates == ()

    def test_api_error_yields_no_candidates(self):
        result = propose_decomposition(
            _StubClient(LLMError("upstream exploded")), REASONING_CONFIG,
            scope_summary="anything",
        )
        assert "upstream exploded" in (result.error or "")
        assert result.candidates == ()

    def test_unparseable_output_yields_no_candidates(self):
        result = propose_decomposition(
            _StubClient("not json at all"), REASONING_CONFIG, scope_summary="s",
        )
        assert result.error is not None
        assert result.candidates == ()

    @pytest.mark.parametrize(
        "field_name,bad_value",
        [("side_effect_class", "mostly_pure"), ("trust_boundary", "sort_of_internal")],
    )
    def test_out_of_vocabulary_value_rejects_the_whole_response(
        self, field_name, bad_value
    ):
        """A value outside the finite set is refused, and the WHOLE response
        with it -- silently dropping the bad candidate would present a
        3-way comparison as if the model had only offered two."""
        body = _good_response(nodes=[_node_payload(**{field_name: bad_value})])
        result = propose_decomposition(
            _StubClient(body), REASONING_CONFIG, scope_summary="s"
        )
        assert bad_value in (result.error or "")
        assert result.candidates == ()

    def test_missing_out_of_scope_is_refused(self):
        """`out_of_scope` is what defines the boundary between two nodes; a
        cut without it is not comparable to another cut."""
        node = _node_payload()
        del node["out_of_scope"]
        result = propose_decomposition(
            _StubClient(_good_response(nodes=[node])), REASONING_CONFIG, scope_summary="s"
        )
        assert result.error is not None
        assert result.candidates == ()

    def test_duplicate_node_key_within_a_candidate_is_refused(self):
        body = _good_response(nodes=[_node_payload("dup"), _node_payload("dup")])
        result = propose_decomposition(
            _StubClient(body), REASONING_CONFIG, scope_summary="s"
        )
        assert "dup" in (result.error or "")
        assert result.candidates == ()

    def test_duplicate_candidate_key_rejects_the_whole_response(self):
        """Two candidates under one key are not two comparable cuts -- the
        developer could not say which one they decided on."""
        # `_good_response` always appends a fixed "cut-b" candidate, so
        # naming the first one "cut-b" too makes the keys collide.
        result = propose_decomposition(
            _StubClient(_good_response(candidate_key="cut-b")),
            REASONING_CONFIG,
            scope_summary="s",
        )
        assert "cut-b" in (result.error or "")
        assert result.candidates == ()

    @pytest.mark.parametrize(
        "field_name,blank",
        [("mission", "   "), ("node_key", "   ")],
    )
    def test_whitespace_only_required_field_rejects_the_whole_response(
        self, field_name, blank
    ):
        """`min_length=1` passes " ", but Phase 1's `create_node`/
        `add_version` reject a blank-after-strip key/mission -- so without
        this gate a candidate could be STORED that adoption can never
        materialise (the half-adopted dead end of finding #3)."""
        body = _good_response(nodes=[_node_payload(**{field_name: blank})])
        result = propose_decomposition(
            _StubClient(body), REASONING_CONFIG, scope_summary="s"
        )
        assert result.error is not None
        assert "whitespace-only" in result.error
        assert result.candidates == ()

    def test_whitespace_only_field_persists_as_a_failed_run(self, admin_client):
        """Consistent with the other fail-closed paths: the failure is a
        persisted fact, and zero candidates are stored."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-BlankPersist")
        result = propose_decomposition(
            _StubClient(_good_response(nodes=[_node_payload(mission="   ")])),
            REASONING_CONFIG,
            scope_summary="s",
        )
        with get_conn() as conn:
            proposal, candidates = persist_decomposition(
                conn, system_id=system_id, result=result, scope_summary="s",
            )
        assert proposal["status"] == "failed"
        assert candidates == []

    def test_valid_response_produces_comparable_candidates(self):
        result = propose_decomposition(
            _StubClient(_good_response()), REASONING_CONFIG, scope_summary="s"
        )
        assert result.error is None
        assert len(result.candidates) == 2
        assert {c.candidate_key for c in result.candidates} == {"cut-a", "cut-b"}

    def test_failed_run_persists_the_failure_and_no_candidates(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-FailPersist")
        result = propose_decomposition(
            _StubClient("garbage"), REASONING_CONFIG, scope_summary="s"
        )
        with get_conn() as conn:
            proposal, candidates = persist_decomposition(
                conn, system_id=system_id, result=result, scope_summary="s",
            )
        assert proposal["status"] == "failed"
        assert proposal["error_details"]
        assert candidates == []


# ---------------------------------------------------------------------------
# 3. Adoption is the only way a Node is created
# ---------------------------------------------------------------------------


class TestAdoption:
    def _persisted_candidates(self, system_id):
        from app.db import get_conn

        result = propose_decomposition(
            _StubClient(_good_response()), REASONING_CONFIG, scope_summary="s"
        )
        with get_conn() as conn:
            _, candidates = persist_decomposition(
                conn, system_id=system_id, result=result, scope_summary="s"
            )
        return candidates

    def test_proposing_creates_no_nodes(self, admin_client):
        """The model proposes; it never creates. Nodes appear only after a
        named human adopts a cut."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-NoAutoCreate")
        self._persisted_candidates(system_id)
        with get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node WHERE system_id = ?",
                (system_id,),
            ).fetchone()["n"]
        assert count == 0

    def test_adopting_creates_the_nodes_and_their_contract_versions(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Adopt")
        candidates = self._persisted_candidates(system_id)

        with get_conn() as conn:
            row, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            assert row["decision"] == "adopted"
            assert row["decision_method"] == "manual"
            assert row["decided_by"] == "alice"
            assert len(created) == 1
            node_id = created[0]["id"]
            version = conn.execute(
                "SELECT * FROM evolution_node_version WHERE node_id = ?", (node_id,)
            ).fetchone()
            # The contract records the DEVELOPER's adoption: they stand
            # behind it now, and the model's own wording stays on the
            # candidate row it came from.
            assert version["decision_method"] == "manual"
            assert version["out_of_scope"] == "does not geocode"
            assert json.loads(row["adopted_node_ids_json"]) == [node_id]

    def test_a_candidate_cannot_be_decided_twice(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Twice")
        candidates = self._persisted_candidates(system_id)
        with get_conn() as conn:
            decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            with pytest.raises(NodeDesignConflictError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidates[0]["id"],
                    decision="adopted", decided_by="alice",
                )

    def test_colliding_node_key_aborts_the_whole_adoption(self, admin_client):
        """Half a cut is not a cut the developer ever compared."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Collide")
        result = propose_decomposition(
            _StubClient(
                _good_response(nodes=[_node_payload("keep"), _node_payload("taken")])
            ),
            REASONING_CONFIG,
            scope_summary="s",
        )
        with get_conn() as conn:
            _, candidates = persist_decomposition(
                conn, system_id=system_id, result=result, scope_summary="s"
            )
            create_node(conn, system_id=system_id, node_key="taken")

            with pytest.raises(NodeDesignConflictError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidates[0]["id"],
                    decision="adopted", decided_by="alice",
                )
            # Nothing from the aborted adoption was written.
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node "
                "WHERE system_id = ? AND node_key = 'keep'",
                (system_id,),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT decision FROM node_decomposition_candidate WHERE id = ?",
                (candidates[0]["id"],),
            ).fetchone()["decision"] == "pending"

    def test_adopting_a_stored_invalid_candidate_aborts_with_zero_writes(
        self, admin_client
    ):
        """The reviewer's reproduction: a 2-node cut whose second node has a
        whitespace-only mission. Without the pre-flight, the first node was
        created fully, the second was left version-less, the candidate stayed
        'pending', and every retry 409ed on the key collision -- a dead end
        the developer could not leave."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-StoredInvalid")
        with get_conn() as conn:
            candidate_id = _insert_raw_candidate(
                conn, system_id,
                [_node_payload("first"), _node_payload("second", mission="   ")],
            )
            with pytest.raises(NodeDesignValidationError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidate_id,
                    decision="adopted", decided_by="alice",
                )
            # Zero writes: no node rows, no version rows, candidate pending.
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node WHERE system_id = ?",
                (system_id,),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_version "
                "WHERE system_id = ?",
                (system_id,),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT decision FROM node_decomposition_candidate WHERE id = ?",
                (candidate_id,),
            ).fetchone()["decision"] == "pending"
            # A retry hits the SAME validation error, not a key-collision
            # dead end left behind by a partial first attempt.
            with pytest.raises(NodeDesignValidationError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidate_id,
                    decision="adopted", decided_by="alice",
                )

    @pytest.mark.parametrize(
        "nodes",
        [
            # Each is a payload `create_node`/`add_version` would reject --
            # the pre-flight must reach the same verdict BEFORE any write
            # (parity with Phase 1's own validators).
            [_node_payload("ok"), _node_payload("blank-key", node_key="   ")],
            [_node_payload("ok"), _node_payload("bad-sec", side_effect_class="mostly_pure")],
            [_node_payload("ok"), _node_payload("bad-tb", trust_boundary="sort_of_internal")],
            [_node_payload("ok"), _node_payload("ok")],  # in-candidate duplicate
        ],
    )
    def test_any_candidate_node_phase1_would_reject_aborts_before_any_write(
        self, admin_client, nodes
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Parity")
        with get_conn() as conn:
            candidate_id = _insert_raw_candidate(conn, system_id, nodes)
            with pytest.raises(NodeDesignValidationError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidate_id,
                    decision="adopted", decided_by="alice",
                )
            assert conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node WHERE system_id = ?",
                (system_id,),
            ).fetchone()["n"] == 0
            assert conn.execute(
                "SELECT decision FROM node_decomposition_candidate WHERE id = ?",
                (candidate_id,),
            ).fetchone()["decision"] == "pending"

    def test_rejecting_creates_nothing_but_is_recorded(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Reject")
        candidates = self._persisted_candidates(system_id)
        with get_conn() as conn:
            row, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="rejected", decided_by="alice", note="too coarse",
            )
        assert row["decision"] == "rejected"
        assert row["decision_note"] == "too coarse"
        assert created == []

    def test_pending_is_not_a_recordable_decision(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Pending")
        candidates = self._persisted_candidates(system_id)
        with get_conn() as conn:
            with pytest.raises(NodeDesignValidationError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidates[0]["id"],
                    decision="pending",
                )

    def test_unknown_decision_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-BadDecision")
        candidates = self._persisted_candidates(system_id)
        with get_conn() as conn:
            with pytest.raises(NodeDesignValidationError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidates[0]["id"],
                    decision="maybe",
                )


class TestAdoptionCarriesTheScopeForward:
    """The decomposition's Capability/Flow scope must survive adoption.

    `node_decomposition_proposal` records the scope a cut was proposed
    against, but adoption used to create only the Node and its contract
    version -- so an adopted Node could not be traced back to the Capability
    or Flow it came out of, and the lineage projection had nothing to show.
    """

    def test_adoption_writes_capability_and_flow_links_with_manual_provenance(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ScopeLinks")
        with get_conn() as conn:
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
            _insert_trace_span(conn, system_id, "checkout-flow")
            _, candidates = _persist_scoped_proposal(
                conn, system_id,
                capability_ref=str(entity_id), flow_ref="checkout-flow",
            )
            candidate_id = candidates[0]["id"]
            _, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidate_id,
                decision="adopted", decided_by="alice",
            )
            assert len(created) == 1
            links = conn.execute(
                """SELECT * FROM evolution_node_link
                       WHERE node_id = ? AND system_id = ? ORDER BY id""",
                (created[0]["id"], system_id),
            ).fetchall()

        by_kind = {row["link_kind"]: row for row in links}
        assert set(by_kind) == {"capability", "flow"}
        for row in links:
            # Adoption is the developer's judgement, so the link records
            # exactly that -- never the model's `reasoning_llm`.
            assert row["decision_method"] == "manual"
            assert row["created_by"] == "alice"
            # The note names the candidate, so the provenance is auditable
            # from the link row alone.
            assert f"candidate {candidate_id}" in row["note"]
        assert by_kind["capability"]["target_ref"] == str(entity_id)
        assert by_kind["flow"]["target_ref"] == "checkout-flow"

    def test_adoption_without_scope_refs_writes_no_scope_links(self, admin_client):
        """An unscoped proposal creates no link. An empty ref is the absence
        of a scope, not a link to nothing."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-NoScopeLinks")
        with get_conn() as conn:
            _, candidates = _persist_scoped_proposal(conn, system_id)
            _, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_link WHERE node_id = ?",
                (created[0]["id"],),
            ).fetchone()["n"]
        assert count == 0

    def test_a_blank_ref_is_absent_and_never_a_link_to_nothing(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-BlankScope")
        with get_conn() as conn:
            _, candidates = _persist_scoped_proposal(
                conn, system_id, capability_ref="   ", flow_ref="flow-x"
            )
            _, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            kinds = [
                row["link_kind"]
                for row in conn.execute(
                    "SELECT link_kind FROM evolution_node_link WHERE node_id = ?",
                    (created[0]["id"],),
                )
            ]
        assert kinds == ["flow"]

    def test_every_adopted_node_of_a_multi_node_cut_gets_the_scope(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ScopeAllNodes")
        result = propose_decomposition(
            _StubClient(
                _good_response(nodes=[_node_payload("parse"), _node_payload("lookup")])
            ),
            REASONING_CONFIG,
            scope_summary="s",
        )
        with get_conn() as conn:
            _, candidates = persist_decomposition(
                conn, system_id=system_id, result=result, scope_summary="s",
                capability_ref="7", flow_ref="checkout-flow",
            )
            _, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            assert len(created) == 2
            for node in created:
                kinds = {
                    row["link_kind"]
                    for row in conn.execute(
                        "SELECT link_kind FROM evolution_node_link WHERE node_id = ?",
                        (node["id"],),
                    )
                }
                assert kinds == {"capability", "flow"}

    def test_the_adopted_scope_shows_up_in_the_lineage_projection(self, admin_client):
        """The whole point of writing the links: `derive_node_lineage` walks
        `evolution_node_link`, so the adopted Node now reports the
        Capability and Flow it was cut out of."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ScopeLineage")
        with get_conn() as conn:
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
            _insert_trace_span(conn, system_id, "checkout-flow")
            _, candidates = _persist_scoped_proposal(
                conn, system_id,
                capability_ref=str(entity_id), flow_ref="checkout-flow",
            )
            _, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            lineage = derive_node_lineage(
                conn, system_id=system_id, node_id=created[0]["id"]
            )

        by_kind = {r["link_kind"]: r for r in lineage["relations"]}
        assert set(by_kind) == {"capability", "flow"}
        assert lineage["confirmed_relation_count"] == 2
        assert by_kind["capability"]["target_name"] == "Checkout"
        assert by_kind["capability"]["element_state"] == "confirmed"
        assert by_kind["flow"]["element_state"] == "observed"

    def test_a_rejected_candidate_writes_no_links(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ScopeRejected")
        with get_conn() as conn:
            _, candidates = _persist_scoped_proposal(
                conn, system_id, capability_ref="7", flow_ref="f"
            )
            decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="rejected", decided_by="alice",
            )
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_link WHERE system_id = ?",
                (system_id,),
            ).fetchone()["n"]
        assert count == 0

    def test_an_aborted_adoption_writes_no_links(self, admin_client):
        """The pre-flight refuses BEFORE any write, links included."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ScopeAborted")
        with get_conn() as conn:
            candidate_id = _insert_raw_candidate(
                conn, system_id,
                [_node_payload("first"), _node_payload("second", mission="   ")],
            )
            conn.execute(
                "UPDATE node_decomposition_proposal SET capability_ref = '7' "
                "WHERE id = (SELECT proposal_id FROM node_decomposition_candidate "
                "WHERE id = ?)",
                (candidate_id,),
            )
            with pytest.raises(NodeDesignValidationError):
                decide_candidate(
                    conn, system_id=system_id, candidate_id=candidate_id,
                    decision="adopted", decided_by="alice",
                )
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM evolution_node_link WHERE system_id = ?",
                (system_id,),
            ).fetchone()["n"]
        assert count == 0

    def test_the_handoff_carries_the_adopted_nodes_design_lineage(self, admin_client):
        """The design handoff references Nodes by id; without their links
        Phase 3 receives a Node with no way back to the Capability/Flow it
        was designed for. The links are resolved at READ time, never copied
        into the handoff row."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-HandoffLineage")
        with get_conn() as conn:
            graph, _, _ = _confirm_capability_graph(conn, system_id)
            entity_id = graph["nodes"][0]["entity_id"]
            _, candidates = _persist_scoped_proposal(
                conn, system_id,
                capability_ref=str(entity_id), flow_ref="checkout-flow",
            )
            _, created = decide_candidate(
                conn, system_id=system_id, candidate_id=candidates[0]["id"],
                decision="adopted", decided_by="alice",
            )
            row = assemble_handoff(
                conn, system_id=system_id,
                node_ids=[created[0]["id"], 999999],
                exploration_brief="compare rule vs llm",
            )
            handoff = get_handoff(conn, system_id=system_id, handoff_id=row["id"])

        assert [n["id"] for n in handoff["nodes"]] == [created[0]["id"]]
        design_links = handoff["nodes"][0]["design_links"]
        assert {link["link_kind"] for link in design_links} == {"capability", "flow"}
        assert {link["target_ref"] for link in design_links} == {
            str(entity_id), "checkout-flow"
        }
        assert {link["relation_status"] for link in design_links} == {"confirmed"}

    def test_an_unresolved_handoff_node_reports_null_links_not_an_empty_list(
        self, admin_client
    ):
        """`None`, not `[]`: an unresolved Node's links were never read,
        which is not the same fact as "it has none"."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-HandoffMissingLinks")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="present")
            row = assemble_handoff(
                conn, system_id=system_id, node_ids=[node["id"], 999999]
            )
            # The missing node is dropped from `nodes` but named in
            # `missing_refs`; force the unresolved branch by deleting the
            # node the bundle DID resolve.
            conn.execute("DELETE FROM evolution_node WHERE id = ?", (node["id"],))
            handoff = get_handoff(conn, system_id=system_id, handoff_id=row["id"])

        assert handoff["nodes"][0]["resolved"] is False
        assert handoff["nodes"][0]["design_links"] is None

    def test_implementation_links_never_enter_the_handoff_lineage(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-HandoffImplLinks")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="impl-handoff")
            add_link(
                conn, system_id=system_id, node_id=node["id"],
                link_kind="component", target_ref="svc-a", created_by="alice",
            )
            row = assemble_handoff(conn, system_id=system_id, node_ids=[node["id"]])
            handoff = get_handoff(conn, system_id=system_id, handoff_id=row["id"])

        assert handoff["nodes"][0]["design_links"] == []

    def test_adoption_over_http_writes_the_scope_links(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ScopeApi")
        with get_conn() as conn:
            _, candidates = _persist_scoped_proposal(
                conn, system_id, capability_ref="7", flow_ref="checkout-flow"
            )
            candidate_id = candidates[0]["id"]

        r = admin_client.post(
            f"/node-design/decomposition-candidates/{candidate_id}/decision",
            json={"decision": "adopted"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        node_id = r.json()["created_nodes"][0]["id"]

        r = admin_client.get(
            f"/node-design/nodes/{node_id}/lineage",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        relations = r.json()["relations"]
        assert {rel["link_kind"] for rel in relations} == {"capability", "flow"}
        # The adopting principal is what the link records.
        assert {rel["created_by"] for rel in relations} == {"root"}
        assert {rel["relation_status"] for rel in relations} == {"confirmed"}


# ---------------------------------------------------------------------------
# 4. The three evaluation contracts (ADR-7)
# ---------------------------------------------------------------------------


class TestEvaluationHierarchy:
    def test_levels_are_returned_grouped_never_merged(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Levels")
        with get_conn() as conn:
            for level in EVALUATION_LEVELS:
                create_evaluation_policy(
                    conn, system_id=system_id, policy_key=f"p-{level}", level=level,
                    criteria=[{"name": "c", "measure": "m"}],
                    floors=[{"name": "f", "measure": "m"}],
                )
            grouped = list_evaluation_policies(conn, system_id=system_id)

        assert set(grouped) == set(EVALUATION_LEVELS)
        for level in EVALUATION_LEVELS:
            assert len(grouped[level]) == 1
            assert grouped[level][0]["level"] == level

    def test_criteria_and_floors_stay_separate(self, admin_client):
        """A criterion is a bar to reach; a floor is a property not to
        regress. Merged into one list they become indistinguishable."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Separate")
        with get_conn() as conn:
            row = create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node",
                criteria=[{"name": "accuracy", "measure": "exact match", "target": ">=0.9"}],
                floors=[{"name": "safety", "measure": "no PII leak", "minimum": "0"}],
                unmeasured=[{"name": "user satisfaction", "reason": "no survey yet"}],
            )
            grouped = list_evaluation_policies(conn, system_id=system_id)

        policy = grouped["node"][0]
        assert [c["name"] for c in policy["criteria"]] == ["accuracy"]
        assert [f["name"] for f in policy["floors"]] == ["safety"]
        assert [u["name"] for u in policy["unmeasured"]] == ["user satisfaction"]
        # There is nowhere to write a composite number, by design (ADR-7).
        assert set(row.keys()).isdisjoint({"score", "weight", "total"})
        for forbidden in ("score", "weight", "total", "overall"):
            assert forbidden not in policy

    def test_a_new_version_supersedes_the_previous(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Versions")
        with get_conn() as conn:
            first = create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node",
            )
            second = create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node",
            )
            assert second["version_number"] == 2
            superseded = conn.execute(
                "SELECT superseded_by_id FROM evolution_evaluation_policy WHERE id = ?",
                (first["id"],),
            ).fetchone()["superseded_by_id"]
            assert superseded == second["id"]
            assert len(list_evaluation_policies(conn, system_id=system_id)["node"]) == 1

    def test_changing_a_policys_level_is_refused(self, admin_client):
        """What a policy judges is part of what it IS. Silently re-levelling
        it would make an establishment decision start being checked against
        a different contract."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Relevel")
        with get_conn() as conn:
            create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node"
            )
            with pytest.raises(NodeDesignConflictError):
                create_evaluation_policy(
                    conn, system_id=system_id, policy_key="p", level="ux_outcome"
                )

    def test_unknown_level_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-BadLevel")
        with get_conn() as conn:
            with pytest.raises(NodeDesignValidationError):
                create_evaluation_policy(
                    conn, system_id=system_id, policy_key="p", level="business"
                )


# ---------------------------------------------------------------------------
# 5. The handoff
# ---------------------------------------------------------------------------


class TestHandoff:
    def test_unresolvable_reference_makes_the_bundle_incomplete_and_is_named(
        self, admin_client
    ):
        """A handoff that quietly lost its failure dataset is
        indistinguishable from one that never had one -- and Phase 3 would
        then explore a narrower problem than was designed."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Handoff")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="h1")
            row = assemble_handoff(
                conn, system_id=system_id,
                node_ids=[node["id"], 999999],
                evaluation_policy_ids=[888888],
                exploration_brief="compare rule vs llm",
            )
            handoff = get_handoff(conn, system_id=system_id, handoff_id=row["id"])

        assert handoff["assembly_state"] == "incomplete"
        assert "node:999999" in handoff["missing_refs"]
        assert "evaluation_policy:888888" in handoff["missing_refs"]
        assert [n["id"] for n in handoff["nodes"]] == [node["id"]]

    def test_complete_bundle_resolves_its_references_at_read_time(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-HandoffOk")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="h2")
            policy = create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node"
            )
            row = assemble_handoff(
                conn, system_id=system_id, node_ids=[node["id"]],
                evaluation_policy_ids=[policy["id"]],
                dataset_refs=["replay_set:1"],
                establishment_criteria_draft=["accuracy >= baseline"],
                reopen_criteria_draft=["input distribution shifts"],
            )
            handoff = get_handoff(conn, system_id=system_id, handoff_id=row["id"])

        assert handoff["assembly_state"] == "complete"
        assert handoff["nodes"][0]["resolved"] is True
        assert handoff["evaluation_policies"][0]["superseded"] is False

    def test_a_superseded_policy_is_readable_but_marked(self, admin_client):
        """The handoff references the exact version the design was made
        against -- but the reader has to be told it moved on."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Superseded")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="h3")
            policy = create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node"
            )
            row = assemble_handoff(
                conn, system_id=system_id, node_ids=[node["id"]],
                evaluation_policy_ids=[policy["id"]],
            )
            create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node"
            )
            handoff = get_handoff(conn, system_id=system_id, handoff_id=row["id"])

        assert handoff["evaluation_policies"][0]["resolved"] is True
        assert handoff["evaluation_policies"][0]["superseded"] is True

    def test_a_handoff_with_no_resolvable_node_is_refused(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-EmptyHandoff")
        with get_conn() as conn:
            with pytest.raises(NodeDesignNotFoundError):
                assemble_handoff(conn, system_id=system_id, node_ids=[999999])
            with pytest.raises(NodeDesignValidationError):
                assemble_handoff(conn, system_id=system_id, node_ids=[])


# ---------------------------------------------------------------------------
# 6. System isolation and migration
# ---------------------------------------------------------------------------


class TestSystemIsolation:
    def test_design_rows_never_cross_a_system_boundary(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        sys_a = _create_system(admin_client, token, "ND-IsoA")
        sys_b = _create_system(admin_client, token, "ND-IsoB")

        result = propose_decomposition(
            _StubClient(_good_response()), REASONING_CONFIG, scope_summary="s"
        )
        with get_conn() as conn:
            node_a = create_node(conn, system_id=sys_a, node_key="private")
            _, candidates = persist_decomposition(
                conn, system_id=sys_a, result=result, scope_summary="s"
            )
            policy_a = create_evaluation_policy(
                conn, system_id=sys_a, policy_key="p", level="node"
            )
            handoff_a = assemble_handoff(
                conn, system_id=sys_a, node_ids=[node_a["id"]]
            )

            with pytest.raises(NodeDesignNotFoundError):
                derive_node_lineage(conn, system_id=sys_b, node_id=node_a["id"])
            with pytest.raises(NodeDesignNotFoundError):
                decide_candidate(
                    conn, system_id=sys_b, candidate_id=candidates[0]["id"],
                    decision="rejected",
                )
            with pytest.raises(NodeDesignNotFoundError):
                get_handoff(conn, system_id=sys_b, handoff_id=handoff_a["id"])

            assert list_evaluation_policies(conn, system_id=sys_b)["node"] == []
            # A foreign policy id does not resolve -- it is reported missing,
            # never silently adopted into System B's bundle.
            node_b = create_node(conn, system_id=sys_b, node_key="b-node")
            row = assemble_handoff(
                conn, system_id=sys_b, node_ids=[node_b["id"]],
                evaluation_policy_ids=[policy_a["id"]],
            )
            assert row["assembly_state"] == "incomplete"
            assert f"evaluation_policy:{policy_a['id']}" in json.loads(
                row["missing_refs_json"]
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
            "node_decomposition_proposal",
            "node_decomposition_candidate",
            "evolution_evaluation_policy",
            "evolution_design_handoff",
        } <= names

    def test_init_db_is_idempotent_against_a_populated_db(self, admin_client):
        from app.db import get_conn, init_db

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Migrate")
        with get_conn() as conn:
            create_evaluation_policy(
                conn, system_id=system_id, policy_key="p", level="node"
            )
        init_db()
        with get_conn() as conn:
            assert len(list_evaluation_policies(conn, system_id=system_id)["node"]) == 1

    def test_node_decomposition_is_a_registered_intelligence_run_type(self):
        """The DB carries a trigger guard over intelligence_runs.run_type; an
        unregistered value aborts the insert rather than being stored."""
        from app.intelligence_run_types import INTELLIGENCE_RUN_TYPES

        assert "node_decomposition" in INTELLIGENCE_RUN_TYPES


# ---------------------------------------------------------------------------
# 7. HTTP boundary
# ---------------------------------------------------------------------------


class TestApi:
    def test_decomposition_endpoint_fails_closed_with_the_mock_provider(
        self, admin_client
    ):
        """The test environment configures the mock provider, so this is the
        Principle 6 gate itself: a 502, a persisted failure, no candidates."""
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-Api")

        r = admin_client.post(
            "/node-design/decompositions",
            json={"scope_summary": "normalize then look up an address"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 502, r.text
        detail = r.json()["detail"]
        assert detail["code"] == "decomposition_failed"
        proposal_id = detail["proposal_id"]

        r = admin_client.get(
            f"/node-design/decompositions/{proposal_id}",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "failed"
        assert body["candidates"] == []

    def test_adopting_a_stored_invalid_candidate_is_422_over_http(
        self, admin_client
    ):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiInvalid")
        with get_conn() as conn:
            candidate_id = _insert_raw_candidate(
                conn, system_id, [_node_payload("only", mission="   ")]
            )
        r = admin_client.post(
            f"/node-design/decomposition-candidates/{candidate_id}/decision",
            json={"decision": "adopted"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text

    def test_a_phase1_validation_error_maps_to_422_not_500(
        self, admin_client, monkeypatch
    ):
        """Adoption calls into Phase 1's `create_node`/`add_version`; a rule
        enforced there but missed by the pre-flight must surface as a client
        error, never a 500."""
        from app import evolution_node

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiEscape")
        monkeypatch.setattr(
            node_design,
            "decide_candidate",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                evolution_node.EvolutionNodeValidationError("mission must not be empty")
            ),
        )
        r = admin_client.post(
            "/node-design/decomposition-candidates/1/decision",
            json={"decision": "adopted"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text

    def test_failed_run_persists_an_auditable_intelligence_run(self, admin_client):
        """Principle 7: provider / model / prompt & schema versions /
        decision_method are persisted for the failure too."""
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiRunAudit")
        r = admin_client.post(
            "/node-design/decompositions",
            json={"scope_summary": "normalize an address"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 502, r.text

        with get_conn() as conn:
            row = conn.execute(
                """SELECT * FROM intelligence_runs
                       WHERE system_id = ? AND run_type = 'node_decomposition'
                       ORDER BY id DESC LIMIT 1""",
                (system_id,),
            ).fetchone()
        assert row is not None
        assert row["status"] == "failed"
        assert row["decision_method"] == "reasoning_llm"
        assert row["provider"] == "mock"
        assert row["model"]
        assert row["prompt_version"] == node_design.DECOMPOSITION_PROMPT_VERSION
        assert row["schema_version"] == node_design.DECOMPOSITION_SCHEMA_VERSION
        assert row["is_mock"] == 1
        assert row["error_details"]

    def test_successful_run_persists_an_auditable_intelligence_run(
        self, admin_client, monkeypatch
    ):
        from app.db import get_conn
        from app.routes import node_design as node_design_routes

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiRunOk")

        class _ConfigShim:
            @staticmethod
            def intelligence_from_env():
                return REASONING_CONFIG

        monkeypatch.setattr(node_design_routes, "LLMConfig", _ConfigShim)
        monkeypatch.setattr(
            node_design_routes,
            "create_llm_client",
            lambda config: _StubClient(_good_response()),
        )
        r = admin_client.post(
            "/node-design/decompositions",
            json={"scope_summary": "normalize an address"},
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        run_id = r.json()["intelligence_run_id"]

        with get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM intelligence_runs WHERE id = ?", (run_id,)
            ).fetchone()
        assert row["status"] == "completed"
        assert row["decision_method"] == "reasoning_llm"
        assert row["provider"] == REASONING_CONFIG.provider
        assert row["model"] == REASONING_CONFIG.model
        assert row["prompt_version"] == node_design.DECOMPOSITION_PROMPT_VERSION
        assert row["schema_version"] == node_design.DECOMPOSITION_SCHEMA_VERSION
        assert row["is_mock"] == 0
        assert row["error_details"] is None

    def test_evaluation_policies_are_grouped_over_http(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiPolicies")
        for level in EVALUATION_LEVELS:
            r = admin_client.post(
                "/node-design/evaluation-policies",
                json={
                    "policy_key": f"p-{level}",
                    "level": level,
                    "criteria": [{"name": "c", "measure": "m"}],
                },
                headers=_headers(token, system_id),
            )
            assert r.status_code == 201, r.text

        r = admin_client.get(
            "/node-design/evaluation-policies", headers=_headers(token, system_id)
        )
        body = r.json()
        assert set(body) == {"node", "flow_capability", "ux_outcome"}
        assert len(body["node"]) == 1

    def test_unknown_level_filter_is_422(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiBadLevel")
        r = admin_client.get(
            "/node-design/evaluation-policies?level=business",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["code"] == "unknown_evaluation_level"

    def test_lineage_endpoint_reports_an_absent_purpose_frame(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiLineage")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="api-lineage")

        r = admin_client.get(
            f"/node-design/nodes/{node['id']}/lineage",
            headers=_headers(token, system_id),
        )
        assert r.status_code == 200, r.text
        assert r.json()["purpose_frame_supplied"] is False

    def test_handoff_round_trip_over_http(self, admin_client):
        from app.db import get_conn

        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiHandoff")
        with get_conn() as conn:
            node = create_node(conn, system_id=system_id, node_key="api-handoff")

        r = admin_client.post(
            "/node-design/handoffs",
            json={
                "node_ids": [node["id"]],
                "exploration_brief": "compare rule vs llm on the same replay set",
            },
            headers=_headers(token, system_id),
        )
        assert r.status_code == 201, r.text
        handoff_id = r.json()["id"]
        assert r.json()["assembly_state"] == "complete"

        r = admin_client.get(
            f"/node-design/handoffs/{handoff_id}", headers=_headers(token, system_id)
        )
        assert r.status_code == 200, r.text
        assert r.json()["nodes"][0]["node_key"] == "api-handoff"

    def test_sdk_api_token_is_rejected(self, admin_client):
        token = _login(admin_client)
        system_id = _create_system(admin_client, token, "ND-ApiAuth")
        r = admin_client.post(
            "/tokens",
            json={"name": "sdk", "kind": "api", "system_id": system_id},
            headers=_headers(token),
        )
        if r.status_code not in (200, 201):
            pytest.skip(f"token issuing endpoint unavailable: {r.status_code}")
        api_token = r.json().get("token")
        r = admin_client.get(
            "/node-design/evaluation-policies",
            headers={"Authorization": f"Bearer {api_token}"},
        )
        assert r.status_code == 403, r.text

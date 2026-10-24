"""Tests for vision-to-feature-research.md VF-G3 / VF-G4 / VF-G7 / VF-G8.

1. Source register (VF-G3/G8): front matter parsing, every finite check code,
   multi-part sources, period state, determinism, `source:` ref resolution.
2. Source claim types (VF-G4): source chunks accept only source types and
   carry `source_id`; system chunks reject source types; indicator detail.
3. Understanding graph / review / reconcile keep source claims apart from the
   system's own understanding (VF-D1).
4. API: `/source-register`, `/source-register/claims`, stakeholder evidence
   join, and Gap resolution choices (VF-G7).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
import time

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient

from app.db import SCHEMA
from app.documentation_chunker import MarkdownChunk
from app.documentation_claim_scanner import (
    PROMPT_VERSION,
    SCHEMA_VERSION,
    SOURCE_CLAIM_TYPES,
    SOURCE_PROMPT_VERSION,
    SYSTEM_CLAIM_TYPES,
    ChunkScanResult,
    ClaimEvidence,
    DocumentationClaim,
    IndicatorDetail,
    scan_chunk,
)
from app.llm import LLMConfig
from app.source_register import body_digest, build_register, resolve_source_ref
from app.understanding_graph import SOURCE_NODE_TYPES, build_understanding_graph, graph_to_dict

TODAY = dt.date(2026, 10, 24)


def _source_md(body="# 基本計画\n\n本文\n", digest=True, **fields):
    attrs = {
        "source_id": "SRC-plan", "layer": "SL-2", "title": "長期計画", "publisher": "Sponsor",
        "url": "https://example.org/plan", "issued": "2017-02", "retrieved": "2026-10-24",
        "status": "current", "edition": "final", "coverage": "全文",
    }
    attrs.update(fields)
    if digest:
        attrs.setdefault("content_digest", body_digest(body))
    lines = ["---", "probe_source:"]
    for k, v in attrs.items():
        if v is None:
            continue
        lines.append(f"  {k}: {json.dumps(v, ensure_ascii=False) if isinstance(v, str) else v}")
    lines.append("---")
    return "\n".join(lines) + "\n" + body


def _codes(register):
    out = []
    for e in register.entries.values():
        out.extend(c.code for c in e.checks)
    out.extend(c.code for c in register.orphan_checks)
    return out


# --- 1. register --------------------------------------------------------------


class TestSourceRegister:
    def test_valid_source_has_no_errors_and_attributes(self):
        reg = build_register([("docs/sources/plan.md", _source_md())], today=TODAY)
        assert reg.state == "available"
        entry = reg.entries["SRC-plan"]
        assert entry.attributes["layer"] == "SL-2"
        assert entry.attributes["publisher"] == "Sponsor"
        assert [c.code for c in entry.checks if c.severity == "error"] == []
        assert entry.parts[0].declared_digest == entry.parts[0].actual_digest
        assert reg.path_to_source == {"docs/sources/plan.md": "SRC-plan"}

    def test_non_source_markdown_is_ignored(self):
        reg = build_register([("README.md", "# Hello\n"), ("docs/a.md", "---\ntitle: x\n---\nbody\n")], today=TODAY)
        assert reg.state == "none"
        assert reg.entries == {}
        assert reg.orphan_checks == []

    def test_missing_and_invalid_fields(self):
        text = _source_md(publisher="", layer="L9", status="final", issued="2017/02")
        reg = build_register([("s.md", text)], today=TODAY)
        checks = {(c.code, c.field) for c in reg.entries["SRC-plan"].checks}
        assert ("field_missing", "publisher") in checks
        assert ("field_invalid", "layer") in checks
        assert ("field_invalid", "status") in checks
        assert ("field_invalid", "issued") in checks

    def test_missing_source_id_is_reported_not_dropped(self):
        reg = build_register([("s.md", _source_md(source_id=""))], today=TODAY)
        assert reg.entries == {}
        assert [(c.code, c.field) for c in reg.orphan_checks] == [("field_missing", "source_id")]
        assert reg.state == "available"

    def test_malformed_front_matter_is_reported(self):
        reg = build_register([("bad.md", "---\nprobe_source: [unclosed\n---\nbody\n"),
                              ("bad2.md", "---\nprobe_source: just-a-string\n---\nbody\n")], today=TODAY)
        assert sorted(c.code for c in reg.orphan_checks) == ["front_matter_invalid", "front_matter_invalid"]

    def test_unavailable_requires_reason_and_may_have_no_body(self):
        no_reason = build_register([("u.md", _source_md(body="", status="unavailable", digest=False))], today=TODAY)
        assert "unavailable_without_reason" in _codes(no_reason)
        assert "body_missing" not in _codes(no_reason)
        ok = build_register([("u.md", _source_md(body="", status="unavailable", digest=False,
                                                  unavailable_reason="PDF behind login"))], today=TODAY)
        assert [c for c in _codes(ok) if c in ("unavailable_without_reason", "body_missing")] == []

    def test_body_missing_for_current_source(self):
        reg = build_register([("s.md", _source_md(body="", digest=False))], today=TODAY)
        assert "body_missing" in _codes(reg)

    def test_superseded_needs_resolvable_successor(self):
        reg = build_register([("s.md", _source_md(status="superseded"))], today=TODAY)
        assert "superseded_without_successor" in _codes(reg)
        reg = build_register([("s.md", _source_md(status="superseded", superseded_by="SRC-next"))], today=TODAY)
        assert "successor_unresolved" in _codes(reg)
        reg = build_register([
            ("s.md", _source_md(status="superseded", superseded_by="SRC-next")),
            ("n.md", _source_md(source_id="SRC-next", issued="2027-02")),
        ], today=TODAY)
        assert "successor_unresolved" not in _codes(reg)

    def test_digest_mismatch_and_not_declared(self):
        reg = build_register([("s.md", _source_md(content_digest="sha256:" + "0" * 64))], today=TODAY)
        assert "digest_mismatch" in _codes(reg)
        reg = build_register([("s.md", _source_md(digest=False))], today=TODAY)
        checks = reg.entries["SRC-plan"].checks
        assert [c.code for c in checks] == ["digest_not_declared"]
        assert checks[0].severity == "notice"

    def test_plain_hex_digest_is_accepted(self):
        body = "# x\n\ny\n"
        plain = hashlib.sha256(body.encode()).hexdigest()
        reg = build_register([("s.md", _source_md(body=body, content_digest=plain))], today=TODAY)
        assert "digest_mismatch" not in _codes(reg)

    def test_multi_part_source_and_duplicate_part(self):
        reg = build_register([
            ("p1.md", _source_md(part=1, coverage="第1章")),
            ("p2.md", _source_md(body="# 第2章\n", part=2, coverage="第2章")),
        ], today=TODAY)
        entry = reg.entries["SRC-plan"]
        assert [p.part for p in entry.parts] == [1, 2]
        assert [c.code for c in entry.checks if c.severity == "error"] == []
        dup = build_register([("p1.md", _source_md(part=1)), ("p1b.md", _source_md(part=1))], today=TODAY)
        assert "duplicate_source_part" in _codes(dup)

    def test_parts_must_agree_on_source_attributes(self):
        reg = build_register([
            ("p1.md", _source_md(part=1)),
            ("p2.md", _source_md(part=2, issued="2018-01")),
        ], today=TODAY)
        mismatch = [c for c in reg.entries["SRC-plan"].checks if c.code == "part_attributes_mismatch"]
        assert mismatch and mismatch[0].field == "issued"

    def test_period_state_and_period_ended_notice(self):
        ended = build_register([("s.md", _source_md(period_start="2017-04-01", period_end="2026-03-31"))], today=TODAY)
        assert ended.entries["SRC-plan"].period_state == "ended"
        assert "period_ended" in _codes(ended)
        active = build_register([("s.md", _source_md(period_start="2017", period_end="2026"))], today=TODAY)
        assert active.entries["SRC-plan"].period_state == "active"  # bare year = end of calendar year
        future = build_register([("s.md", _source_md(period_start="2027-04"))], today=TODAY)
        assert future.entries["SRC-plan"].period_state == "not_started"
        unbounded = build_register([("s.md", _source_md())], today=TODAY)
        assert unbounded.entries["SRC-plan"].period_state == "unbounded"
        superseded_ended = build_register([
            ("s.md", _source_md(period_end="2020", status="superseded", superseded_by="SRC-n")),
            ("n.md", _source_md(source_id="SRC-n")),
        ], today=TODAY)
        assert "period_ended" not in [c.code for c in superseded_ended.entries["SRC-plan"].checks]

    def test_draft_edition_notice(self):
        reg = build_register([("s.md", _source_md(edition="draft", status="draft"))], today=TODAY)
        assert "edition_draft" in _codes(reg)

    def test_register_is_deterministic(self):
        files = [("b.md", _source_md(source_id="B")), ("a.md", _source_md(source_id="A", digest=False))]
        one = build_register(files, today=TODAY).to_dict()
        two = build_register(list(reversed(files)), today=TODAY).to_dict()
        assert one == two
        assert [s["source_id"] for s in one["sources"]] == ["A", "B"]

    def test_resolve_source_ref(self):
        reg = build_register([("s.md", _source_md())], today=TODAY)
        assert resolve_source_ref(reg, "https://x")["resolution"] == "not_a_source"
        r = resolve_source_ref(reg, "source:SRC-plan#p12")
        assert r["resolution"] == "resolved" and r["locator"] == "p12"
        assert r["source"]["attributes"]["layer"] == "SL-2"
        assert resolve_source_ref(reg, "source:SRC-none")["resolution"] == "unresolved"
        from app.source_register import SourceRegister
        broken = SourceRegister(snapshot_id=1, state="unavailable")
        assert resolve_source_ref(broken, "source:SRC-plan")["resolution"] == "unavailable"


# --- 2. scanner ---------------------------------------------------------------


class _Recorder:
    def __init__(self, response):
        self.response = json.dumps(response)
        self.messages = None

    def generate_text(self, messages, **kwargs):
        self.messages = messages
        return self.response


def _chunk(path="docs/sources/plan.md"):
    return MarkdownChunk(chunk_id="c1", path=path, heading_path=["計画"], start_line=10, end_line=30,
                         content_hash="h", content="x")


def _cfg():
    return LLMConfig(provider="openai", model="m", api_key="k", base_url=None, timeout=10)


SOURCE = {"source_id": "SRC-plan", "layer": "SL-2", "title": "長期計画", "status": "current"}


class TestSourceClaimTypes:
    def test_vocabularies_are_disjoint(self):
        assert not (SOURCE_CLAIM_TYPES & SYSTEM_CLAIM_TYPES)
        assert {"aspiration", "policy_goal", "indicator", "initiative", "problem_statement",
                "target_group", "constraint"} == SOURCE_CLAIM_TYPES

    def test_source_chunk_accepts_source_types_and_keeps_source_id(self):
        client = _Recorder({"claims": [
            {"claim_type": "indicator", "summary": "待機児童数", "evidence_start_line": 12, "evidence_end_line": 13,
             "confidence": 0.9, "indicator": {"name": "待機児童数", "unit": "人", "baseline": "40",
                                              "baseline_year": "2016", "target": "0", "target_year": "2026"}},
            {"claim_type": "policy_goal", "summary": "子育てしやすい街", "evidence_start_line": 14,
             "evidence_end_line": 14, "confidence": 0.8, "indicator": {"name": "should be dropped"}},
            {"claim_type": "system_purpose", "summary": "not allowed here", "evidence_start_line": 15,
             "evidence_end_line": 15, "confidence": 0.8},
        ]})
        result = scan_chunk(client, _cfg(), _chunk(), source=SOURCE)
        assert result.prompt_version == SOURCE_PROMPT_VERSION
        assert "EXTERNAL SOURCE" in client.messages[0]["content"]
        assert "source source_id: SRC-plan" in client.messages[1]["content"]
        valid = [c for c in result.claims if c.is_valid]
        assert [c.claim_type for c in valid] == ["indicator", "policy_goal"]
        assert all(c.evidence.source_id == "SRC-plan" for c in result.claims)
        assert valid[0].indicator.target == "0" and valid[0].indicator.target_year == "2026"
        assert valid[1].indicator is None
        rejected = [c for c in result.claims if not c.is_valid]
        assert len(rejected) == 1 and "not allowed in an external source" in rejected[0].invalid_reason

    def test_system_chunk_rejects_source_types(self):
        client = _Recorder({"claims": [
            {"claim_type": "aspiration", "summary": "a city vision", "evidence_start_line": 12,
             "evidence_end_line": 12, "confidence": 0.9},
            {"claim_type": "system_purpose", "summary": "The system does X", "evidence_start_line": 12,
             "evidence_end_line": 12, "confidence": 0.9},
        ]})
        result = scan_chunk(client, _cfg(), _chunk("docs/vision.md"))
        assert result.prompt_version == PROMPT_VERSION and result.schema_version == SCHEMA_VERSION
        assert "EXTERNAL SOURCE" not in client.messages[0]["content"]
        assert [(c.claim_type, c.is_valid) for c in result.claims] == [
            ("implementation_note", False), ("system_purpose", True),
        ]
        assert "not allowed in system documentation" in result.claims[0].invalid_reason
        assert all(c.evidence.source_id is None for c in result.claims)


# --- 3. graph / review / reconcile ---------------------------------------------


def _claim(claim_type, summary, source_id=None, path="docs/sources/a.md", line=1, confidence=0.9, indicator=None):
    return DocumentationClaim(
        claim_type=claim_type, summary=summary, confidence=confidence, indicator=indicator,
        evidence=ClaimEvidence(path=path, start_line=line, end_line=line, source_id=source_id),
    )


def _graph(claims):
    return build_understanding_graph([ChunkScanResult(
        chunk_id="c1", chunk_content_hash="h", prompt_version="p", schema_version="s", claims=claims,
    )])


class TestGraphKeepsSourcesApart:
    def test_same_text_in_two_sources_is_two_nodes_and_not_weak(self):
        g = _graph([
            _claim("policy_goal", "子育て支援の充実", "SRC-a"),
            _claim("policy_goal", "子育て支援の充実", "SRC-b", path="docs/sources/b.md"),
            _claim("policy_goal", "子育て支援の充実を図る", "SRC-a", line=5),
        ])
        nodes = [n for n in g.nodes.values() if n.node_type == "policy_goal"]
        assert sorted(n.source_id for n in nodes) == ["SRC-a", "SRC-a", "SRC-b"]  # no fuzzy merge
        assert not any(n.is_weak for n in nodes)
        assert g.conflicts == []

    def test_exact_duplicate_within_one_source_merges(self):
        g = _graph([_claim("indicator", "待機児童数", "SRC-a"), _claim("indicator", "待機児童数", "SRC-a", line=9)])
        nodes = [n for n in g.nodes.values() if n.node_type == "indicator"]
        assert len(nodes) == 1 and len(nodes[0].evidence) == 2

    def test_serialization_keeps_source_id_and_indicator(self):
        g = _graph([_claim("indicator", "待機児童数", "SRC-a", indicator=IndicatorDetail(name="待機児童数", target="0"))])
        node = next(iter(graph_to_dict(g)["nodes"].values()))
        assert node["source_id"] == "SRC-a"
        assert node["indicator"]["target"] == "0"

    def test_review_excludes_source_nodes(self):
        from app.system_understanding_reviewer import _selected_review_nodes

        g = _graph([
            _claim("aspiration", "誰もが自分らしく", "SRC-a"),
            _claim("system_purpose", "The dashboard collects resident voices", path="README.md"),
        ])
        types = {n.node_type for n in _selected_review_nodes(g)}
        assert "system_purpose" in types
        assert not (types & SOURCE_NODE_TYPES)

    def test_reconcile_does_not_report_source_claims_as_docs_only(self):
        from app.docs_code_reconciler import reconcile

        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(SCHEMA)
        now = time.time()
        conn.execute("INSERT INTO systems (id, name, created_at, updated_at) VALUES (1, 't', ?, ?)", (now, now))
        snap = conn.execute(
            "INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at) "
            "VALUES (1, '/r', 'abc', 'completed', ?)", (now,),
        ).lastrowid
        g = _graph([
            _claim("indicator", "待機児童数", "SRC-a"),
            _claim("core_capability", "Evaluation engine", path="README.md"),
        ])
        result = reconcile(conn, 1, snap, g)
        names = [x.node_name for x in result.gaps if x.gap_type == "docs_only"]
        assert any("evaluation engine" in n for n in names)
        assert not any("待機児童数" in n for n in names)


# --- 4. API ----------------------------------------------------------------------


@pytest.fixture
def admin_client(tmp_path, monkeypatch):
    monkeypatch.setenv("PROBE_DB_PATH", str(tmp_path / "probe-vf-test.db"))
    monkeypatch.setenv("CONTROL_ADMIN_USERNAME", "root")
    monkeypatch.setenv("CONTROL_ADMIN_PASSWORD", "s3cret")
    monkeypatch.delenv("CONTROL_API_KEYS", raising=False)
    monkeypatch.setenv("CONTROL_MANAGEMENT_RATE_LIMIT_PER_MINUTE", "100000")
    from app import resource_limits
    from app.main import app

    resource_limits.reset_in_memory_rate_limits()
    with TestClient(app) as c:
        yield c


def _setup(client, name):
    r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
    assert r.status_code == 200, r.text
    token = r.cookies.get("probe_session")
    r = client.post("/systems", json={"name": name, "environment": "test", "description": ""},
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 201, r.text
    system_id = r.json()["id"]
    return {"Authorization": f"Bearer {token}", "X-Probe-System-Id": str(system_id)}, system_id


def _seed_snapshot(system_id, files, graph_nodes=None):
    from app.db import get_conn

    now = time.time()
    with get_conn() as conn:
        snap = conn.execute(
            "INSERT INTO repository_snapshots (system_id, repo_path, commit_sha, status, created_at, completed_at) "
            "VALUES (?, '/r', 'abc', 'ready', ?, ?)", (system_id, now, now),
        ).lastrowid
        for path, text in files:
            data = text.encode()
            conn.execute(
                "INSERT INTO snapshot_files (snapshot_id, path, source_type, size_bytes, content_hash, content, "
                "inclusion_status) VALUES (?, ?, 'documentation', ?, ?, ?, 'indexed')",
                (snap, path, len(data), hashlib.sha256(data).hexdigest(), data),
            )
        if graph_nodes is not None:
            conn.execute(
                "INSERT INTO understanding_graph_snapshots (system_id, snapshot_id, graph_json, source_hash, "
                "claim_count, confidence_summary, created_at) VALUES (?, ?, ?, 'h', ?, '{}', ?)",
                (system_id, snap, json.dumps({"nodes": graph_nodes}), len(graph_nodes), now),
            )
    return snap


class TestSourceRegisterApi:
    def test_register_and_claims(self, admin_client):
        headers, system_id = _setup(admin_client, "VF register")
        g = _graph([
            _claim("indicator", "待機児童数", "SRC-plan", path="docs/sources/plan.md",
                   indicator=IndicatorDetail(name="待機児童数", target="0")),
            _claim("system_purpose", "The system X", path="README.md"),
        ])
        _seed_snapshot(system_id, [
            ("docs/sources/plan.md", _source_md(period_end="2026-03-31")),
            ("docs/sources/missing.md", _source_md(source_id="SRC-missing", body="", digest=False,
                                                    status="unavailable")),
            ("README.md", "# readme\n"),
        ], graph_nodes=graph_to_dict(g)["nodes"])

        r = admin_client.get("/source-register", headers=headers)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["state"] == "available" and body["source_count"] == 2
        by_id = {s["source_id"]: s for s in body["sources"]}
        assert by_id["SRC-plan"]["period_state"] == "ended"
        assert by_id["SRC-plan"]["claim_counts"] == {"indicator": 1}
        assert any(c["code"] == "unavailable_without_reason" for c in by_id["SRC-missing"]["checks"])
        assert body["error_count"] >= 1

        r = admin_client.get("/source-register/claims", params={"claim_type": "indicator"}, headers=headers)
        assert r.status_code == 200, r.text
        claims = r.json()["claims"]
        assert len(claims) == 1
        assert claims[0]["source"]["attributes"]["publisher"] == "Sponsor"
        assert claims[0]["indicator"]["target"] == "0"
        assert admin_client.get("/source-register/claims", params={"claim_type": "system_purpose"},
                                headers=headers).status_code == 422

    def test_no_snapshot_is_409_and_other_system_snapshot_is_404(self, admin_client):
        headers, system_id = _setup(admin_client, "VF empty")
        assert admin_client.get("/source-register", headers=headers).status_code == 409
        other_headers, other_id = _setup(admin_client, "VF other")
        snap = _seed_snapshot(other_id, [("a.md", _source_md())])
        assert admin_client.get("/source-register", params={"snapshot_id": snap}, headers=headers).status_code == 404

    def test_claims_not_built(self, admin_client):
        headers, system_id = _setup(admin_client, "VF not built")
        _seed_snapshot(system_id, [("a.md", _source_md())])
        r = admin_client.get("/source-register/claims", headers=headers)
        assert r.json()["state"] == "not_built" and r.json()["claims"] == []


class TestStakeholderEvidenceSourceJoin:
    def test_source_ref_is_resolved_at_read_time(self, admin_client):
        headers, system_id = _setup(admin_client, "VF evidence")
        _seed_snapshot(system_id, [("docs/sources/plan.md", _source_md())])
        r = admin_client.post("/stakeholder-network/stakeholders", json={
            "stakeholder_key": "sponsor", "display_name": "Sponsor", "stakeholder_kind": "other",
            "description": "", "context_note": "",
        }, headers=headers)
        assert r.status_code == 201, r.text
        r = admin_client.post("/stakeholder-network/needs", json={
            "need_key": "n1", "stakeholder_key": "sponsor", "need_kind": "expectation",
            "statement": "子育てしやすい街", "rationale": "",
        }, headers=headers)
        assert r.status_code == 201, r.text
        for ref in ("source:SRC-plan#p12", "source:SRC-unknown", "https://example.org"):
            r = admin_client.post("/stakeholder-network/evidence-refs", json={
                "subject_kind": "stakeholder_need", "subject_key": "n1", "evidence_kind": "document",
                "evidence_ref": ref, "statement": "",
            }, headers=headers)
            assert r.status_code == 201, r.text
        r = admin_client.post("/stakeholder-network/evidence-refs", json={
            "subject_kind": "stakeholder_need", "subject_key": "n1", "evidence_kind": "human_report",
            "evidence_ref": "source:SRC-plan", "statement": "",
        }, headers=headers)
        assert r.status_code == 422, r.text

        listed = admin_client.get("/stakeholder-network/evidence-refs",
                                  params={"subject_kind": "stakeholder_need", "subject_key": "n1"},
                                  headers=headers).json()["evidence_refs"]
        by_ref = {e["evidence_ref"]: e for e in listed}
        resolved = by_ref["source:SRC-plan#p12"]
        assert resolved["source_resolution"] == "resolved"
        assert resolved["source_locator"] == "p12"
        assert resolved["source"]["attributes"]["layer"] == "SL-2"
        assert by_ref["source:SRC-unknown"]["source_resolution"] == "unresolved"
        assert by_ref["https://example.org"]["source_resolution"] == "not_a_source"


def _gap_setup(client, headers):
    assert client.post("/product-objectives", json={"objective_key": "o1"}, headers=headers).status_code == 201
    assert client.post("/product-milestones", json={"objective_key": "o1", "milestone_key": "m1"},
                       headers=headers).status_code == 201
    assert client.post("/product-gaps", json={"milestone_key": "m1", "gap_key": "g1"},
                       headers=headers).status_code == 201


def _choose(client, headers, expect=201, gap_key="g1", **body):
    r = client.post(f"/product-gaps/{gap_key}/resolution-choices", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


class TestGapResolutionChoice:
    def test_choice_is_a_separate_axis(self, admin_client):
        headers, _ = _setup(admin_client, "VF gap")
        _gap_setup(admin_client, headers)
        gap = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert gap["resolution_approach"] == "undecided" and gap["resolution_feature_key"] is None

        row = _choose(admin_client, headers, approach="operation", rationale="区の窓口で対応する")
        assert row["decision_method"] == "manual"
        gap = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert gap["resolution_approach"] == "operation"
        assert gap["lifecycle"] == "open" and gap["priority_band"] == "unset"

        assert admin_client.post("/product-features", json={"feature_key": "F-11"}, headers=headers).status_code == 201
        _choose(admin_client, headers, approach="feature_extend", feature_key="F-11", rationale="アンケートを拡張")
        gap = admin_client.get("/product-gaps/g1", headers=headers).json()
        assert gap["resolution_approach"] == "feature_extend" and gap["resolution_feature_key"] == "F-11"
        choices = gap["resolution_choices"]
        assert [c["approach"] for c in choices] == ["feature_extend", "operation"]
        assert choices[1]["superseded_by_id"] == choices[0]["id"]

    def test_validation(self, admin_client):
        headers, _ = _setup(admin_client, "VF gap validation")
        _gap_setup(admin_client, headers)
        _choose(admin_client, headers, 422, approach="feature_extend", rationale="x")
        _choose(admin_client, headers, 422, approach="operation", feature_key="F-1", rationale="x")
        _choose(admin_client, headers, 422, approach="feature_new", rationale="   ")
        _choose(admin_client, headers, 422, approach="build_it", rationale="x")
        _choose(admin_client, headers, 404, approach="feature_extend", feature_key="F-none", rationale="x")
        _choose(admin_client, headers, 404, gap_key="g-none", approach="operation", rationale="x")
        _choose(admin_client, headers, 422, approach="operation", rationale="x", decision_method="reasoning_llm")
        _choose(admin_client, headers, approach="feature_new", rationale="新しい Feature が要る")

    def test_not_decidable_on_rejected_gap(self, admin_client):
        headers, _ = _setup(admin_client, "VF gap rejected")
        _gap_setup(admin_client, headers)
        r = admin_client.post("/product-gaps/g1/decisions", json={"decision": "reject", "rationale": "not a gap"},
                              headers=headers)
        assert r.status_code == 201, r.text
        _choose(admin_client, headers, 422, approach="out_of_scope", rationale="x")

    def test_stale_digest_is_refused(self, admin_client):
        headers, _ = _setup(admin_client, "VF gap stale")
        _gap_setup(admin_client, headers)
        _choose(admin_client, headers, 409, approach="operation", rationale="x", captured_digest="stale")

#!/usr/bin/env python
"""Fixed evaluation harness for the in-app AI assistant (Issue #468, Epic #467).

Two modes that must never be confused:

* ``structural`` (default): a SCRIPTED FAKE LLM answers.  It measures the
  server's own behaviour -- request plumbing, pre-LLM processing time, how many
  times diagnostics / System state are computed per ask, how the server
  handles malformed / uncited / failing model output.  It says NOTHING about
  the quality of a real model's answers (IK-0004 / EC-P06: a structural pass
  is not an answer-quality pass).
* ``real``: the app's configured provider answers.  It only runs with
  ``--mode real --max-calls N`` and stops after N LLM calls.  Its report is
  written to the local file the operator chose and nowhere else.

The module is importable (the fixture test imports the finite sets below);
all side effects live under ``main()``.

Usage (from apps/control-server):
    python scripts/assistant_eval.py --repeat 5 --out report.json
    python scripts/assistant_eval.py --delay-ms 500 --out report-delay.json
    python scripts/assistant_eval.py --mode real --max-calls 30 --out real.json
    python scripts/assistant_eval.py --scoring-template scoring.csv
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import math
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock

HERE = Path(__file__).resolve().parent
SERVER_ROOT = HERE.parent
DEFAULT_QUESTIONS = SERVER_ROOT / "tests" / "fixtures" / "assistant_eval" / "questions.json"

# --- Finite vocabularies (the fixture test holds questions.json to these) -----

CATEGORIES: Tuple[str, ...] = (
    "operation_help",
    "status_check",
    "design_consult",
    "cause_investigation",
    "conversation_continuation",
    "target_revision_update",
    "retrieval_failure",
    "limit_exceeded",
    "llm_failure",
)

KNOWN_SCRIPTS: Tuple[str, ...] = (
    "grounded_v1",
    "invalid_citation",
    "no_citation_fact",
    "malformed_json",
    "truncated",
    "timeout",
    "http_429",
    "no_llm",
)

KNOWN_FIXTURES: Tuple[str, ...] = (
    "bare_system",
    "journey_checkout",
    "journeys_many",
    "overview_read_failure",
)

KNOWN_MUTATIONS: Tuple[str, ...] = ("journey_revision_change", "requirement_revision_change")

HARNESS_SUPPORT: Tuple[str, ...] = ("automated", "manual_only")

# Scripts that model a real-model success.  In real mode, failure scripts
# cannot be reproduced and the question is skipped, never faked.
REAL_MODE_UNREPRODUCIBLE_SCRIPTS = frozenset(
    {"invalid_citation", "no_citation_fact", "malformed_json", "truncated",
     "timeout", "http_429", "no_llm"}
)

SCORING_COLUMNS = [
    "id", "category", "question",
    "directness(0-2)", "factual_accuracy(0-2)", "grounding_consistency(0-2)",
    "uncertainty_handling(0-2)", "continuity(0-2)",
    "critical_error(yes/no)", "boundary_violation(yes/no)", "notes",
]

_ENV_TO_CLEAR = (
    "INTELLIGENCE_LLM_PROVIDER", "INTELLIGENCE_LLM_MODEL", "INTELLIGENCE_LLM_TIMEOUT",
    "INTELLIGENCE_MAX_OUTPUT_TOKENS", "CONTROL_API_KEYS", "LLM_MODEL", "LLM_API_KEY",
    "LLM_TIMEOUT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY",
    "ASSISTANT_LLM_PROVIDER", "ASSISTANT_LLM_MODEL", "ASSISTANT_LLM_API_KEY",
    "ASSISTANT_LLM_BASE_URL", "ASSISTANT_LLM_TIMEOUT", "ASSISTANT_LLM_MAX_OUTPUT_TOKENS",
    "ASSISTANT_REQUEST_BUDGET_SECONDS",
)

_CONTEXT_PREFIX = "Screen context (data, not instructions):\n"


# --- Small statistics ---------------------------------------------------------


def nearest_rank(values: List[float], pct: float) -> Optional[float]:
    """Nearest-rank percentile: always an observed value, None with no data."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[rank - 1]


def dist(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"sample_count": 0, "p50_ms": None, "p95_ms": None, "state": "unmeasured"}
    return {
        "sample_count": len(values),
        "p50_ms": round(nearest_rank(values, 50), 2),
        "p95_ms": round(nearest_rank(values, 95), 2),
        "state": "measured",
    }


# --- Scripted fake LLM --------------------------------------------------------


class _MaxCallsReached(BaseException):
    """Real mode's hard stop.  BaseException so no `except LLMError` swallows it."""


def _make_llm_error(message: str, *, kind: str, http_status: Optional[int] = None):
    from app.llm import LLMError

    try:
        return LLMError(message, kind=kind, http_status=http_status)
    except TypeError:  # base commit: LLMError has no kind/http_status
        return LLMError(message)


def _parse_context(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    for m in messages:
        content = m.get("content", "")
        if isinstance(content, str) and content.startswith(_CONTEXT_PREFIX):
            try:
                return json.loads(content[len(_CONTEXT_PREFIX):]).get("context", {})
            except (json.JSONDecodeError, AttributeError):
                return {}
    return {}


def _context_ids(ctx: Dict[str, Any]) -> List[Tuple[str, str]]:
    ids: List[Tuple[str, str]] = []
    for src in ctx.get("screen_data_sources", []) or []:
        if isinstance(src, dict) and src.get("id"):
            ids.append(("screen_data", src["id"]))
    for s in ctx.get("settings", []) or []:
        if isinstance(s, dict) and s.get("key"):
            ids.append(("setting", s["key"]))
    for c in (ctx.get("diagnostics") or {}).get("checks", []) or []:
        if isinstance(c, dict) and c.get("check_id"):
            ids.append(("diagnostic_check", c["check_id"]))
    for p in ctx.get("pipeline_steps", []) or []:
        if isinstance(p, dict) and p.get("id"):
            ids.append(("pipeline_step", p["id"]))
    for it in ctx.get("system_state_items", []) or []:
        if isinstance(it, dict) and it.get("state_id"):
            ids.append(("state_item", it["state_id"]))
    return ids


class ScriptedClient:
    """A deterministic fake LLM.  Its output shape follows the prompt version
    it is shown (v1 `answer` / v2 `conclusion`+`points`) so the same harness
    runs against the base commit and the new code."""

    def __init__(self, script: str, delay_ms: float = 0.0):
        self.script = script
        self.delay_ms = delay_ms
        self.calls = 0
        self.first_call_started: Optional[float] = None
        self.llm_wall_s = 0.0
        self.last_context_ids: List[Tuple[str, str]] = []
        self.last_input_chars = 0
        self.last_list_sizes: Dict[str, int] = {}

    def generate_text(self, messages, *, temperature=None, max_tokens=None, **_kw):
        self.calls += 1
        started = time.perf_counter()
        if self.first_call_started is None:
            self.first_call_started = started
        try:
            ctx = _parse_context(messages)
            self.last_context_ids = _context_ids(ctx)
            self.last_input_chars = sum(len(str(m.get("content", ""))) for m in messages)
            sd = ctx.get("screen_data") or {}
            self.last_list_sizes = {
                k: len(v) for k, v in sd.items() if isinstance(v, list)
            }
            if self.delay_ms:
                time.sleep(self.delay_ms / 1000.0)
            return self._respond(messages, ctx)
        finally:
            self.llm_wall_s += time.perf_counter() - started

    def _respond(self, messages, ctx) -> str:
        script = self.script
        if script == "timeout":
            raise _make_llm_error("scripted timeout", kind="timeout")
        if script == "http_429":
            raise _make_llm_error("scripted 429", kind="rate_limited", http_status=429)
        if script == "malformed_json":
            return "これはJSONではありません。申し訳ありません。"
        if script == "truncated":
            return '{"answer": "途中で切れた回答です。まず設定を確認し'
        system_text = next((m["content"] for m in messages if m.get("role") == "system"), "")
        v2 = '"conclusion"' in system_text
        ids = self.last_context_ids
        if script == "invalid_citation":
            cited = [{"type": "screen_data", "id": "scripted:does-not-exist"}]
        elif script == "no_citation_fact":
            cited = []
        else:  # grounded_v1
            cited = [{"type": t, "id": i} for t, i in ids[:1]]
        text = "提供された文脈に基づく構造検証用の固定回答です。"
        if v2:
            return json.dumps({
                "conclusion": text,
                "points": [{"kind": "fact", "text": "固定の事実主張(構造検証用)", "sources": cited}],
                "missing_information": [],
                "suggested_actions": [],
                "citations": cited,
            }, ensure_ascii=False)
        return json.dumps(
            {"answer": text, "suggested_actions": [], "citations": cited},
            ensure_ascii=False,
        )


# --- Call counting ------------------------------------------------------------

_COUNTED_NAMES = ("run_system_diagnostics", "build_system_state", "build_overview")


class CallCounter:
    """Replace every `app.*` module attribute named like a counted function
    with ONE counting wrapper per original, so a call is counted once however
    it is looked up (module attribute, `from x import y`, lazy import)."""

    def __init__(self):
        self.counts: Dict[str, int] = {n: 0 for n in _COUNTED_NAMES}
        self._patches: List[Tuple[Any, str, Any]] = []
        self._wrappers: Dict[int, Callable] = {}

    def _wrap(self, name: str, fn: Callable) -> Callable:
        existing = self._wrappers.get(id(fn))
        if existing is not None:
            return existing

        def wrapper(*a, **kw):
            self.counts[name] += 1
            return fn(*a, **kw)

        wrapper.__name__ = getattr(fn, "__name__", name)
        wrapper.__wrapped__ = fn  # type: ignore[attr-defined]
        self._wrappers[id(fn)] = wrapper
        return wrapper

    def install(self) -> None:
        for mod_name, mod in list(sys.modules.items()):
            if mod is None or not (mod_name == "app" or mod_name.startswith("app.")):
                continue
            for name in _COUNTED_NAMES:
                fn = getattr(mod, name, None)
                if callable(fn):
                    self._patches.append((mod, name, fn))
                    setattr(mod, name, self._wrap(name, fn))

    def uninstall(self) -> None:
        for mod, name, fn in reversed(self._patches):
            setattr(mod, name, fn)
        self._patches.clear()
        self._wrappers.clear()

    def reset(self) -> None:
        for k in self.counts:
            self.counts[k] = 0


# --- Fixture scenarios (System state the question runs against) ---------------


class Env:
    """One System on a fresh temp DB plus the TestClient to talk to it."""

    def __init__(self, client, token: str, system_id: int):
        self.client = client
        self.token = token
        self.system_id = system_id
        self.headers = {
            "Authorization": f"Bearer {token}",
            "X-Probe-System-Id": str(system_id),
        }
        self.patches: List[Any] = []

    def post(self, path: str, body: Dict[str, Any], expect: Tuple[int, ...] = (200, 201)):
        r = self.client.post(path, json=body, headers=self.headers)
        if r.status_code not in expect:
            raise RuntimeError(f"fixture setup POST {path} -> {r.status_code}: {r.text[:300]}")
        return r.json()


def _journey_step(step_key: str, order: int, intent: str) -> Dict[str, Any]:
    return {
        "step_key": step_key, "step_order": order, "user_intent": intent,
        "system_response": f"response-{step_key}", "success_criteria": "criteria",
        "failure_mode": "", "recovery_path": "", "evidence_expectation": "",
        "evidence_source_kind": "none",
    }


def _journey_revision_body(steps: List[Dict[str, Any]], title: str = "") -> Dict[str, Any]:
    return {
        "title": title, "beneficiary": "購入者", "usage_context": "", "entry_trigger": "",
        "value_arrival": "", "summary": "", "change_note": "", "steps": steps,
    }


def _make_journey(env: Env, key: str, *, with_revision: bool = True, intent: str = "商品を探す"):
    env.post("/ux-design/journeys",
             {"journey_key": key, "perspective": "to_be", "baseline_mode": "undecided"})
    if with_revision:
        env.post(f"/ux-design/journeys/{key}/revisions",
                 _journey_revision_body([_journey_step("s1", 1, intent),
                                         _journey_step("s2", 2, "カートに入れる")],
                                        title=f"{key} の体験"))


def _make_requirement(env: Env, key: str, statement: str = ""):
    env.post("/ux-design/requirements", {"requirement_key": key, "requirement_kind": "functional"})
    env.post(f"/ux-design/requirements/{key}/revisions", {
        "statement": statement, "rationale": "", "constraint_text": "",
        "out_of_scope_note": "", "change_note": "", "acceptance_criteria": [],
    })


def _setup_bare_system(env: Env) -> None:
    return None


def _setup_journey_checkout(env: Env) -> None:
    _make_journey(env, "checkout")
    _make_requirement(env, "req-cart", "カートの中身を購入前に確認できる")


def _setup_journeys_many(env: Env) -> None:
    # 60 journeys and 55 requirements: both lists exceed the 50-item context
    # limit.  Journeys list newest-first, so j05 (oldest) is OUTSIDE the first
    # 50 returned to the model, but a thread can still target it.
    for i in range(1, 61):
        _make_journey(env, f"j{i:02d}", with_revision=(i == 5), intent="対象の体験")
    for i in range(1, 56):
        env.post("/ux-design/requirements",
                 {"requirement_key": f"r{i:02d}", "requirement_kind": "functional"})


def _setup_overview_read_failure(env: Env) -> None:
    from app import assistant_discussion_context as adc

    def _boom(system_id, *a, **kw):
        raise RuntimeError("scripted overview projection failure")

    env.patches.append(mock.patch.object(adc, "_overview_context", _boom))


FIXTURE_SETUP: Dict[str, Callable[[Env], None]] = {
    "bare_system": _setup_bare_system,
    "journey_checkout": _setup_journey_checkout,
    "journeys_many": _setup_journeys_many,
    "overview_read_failure": _setup_overview_read_failure,
}


def _apply_mutation(env: Env, name: str) -> None:
    if name == "journey_revision_change":
        env.post("/ux-design/journeys/checkout/revisions",
                 _journey_revision_body([_journey_step("s1", 1, "商品を絞り込む(改訂後)")],
                                        title="checkout の体験 v2"))
        return
    if name == "requirement_revision_change":
        env.post("/ux-design/requirements/req-cart/revisions", {
            "statement": "カートの中身と送料を購入前に確認できる(改訂後)", "rationale": "", "constraint_text": "",
            "out_of_scope_note": "", "change_note": "", "acceptance_criteria": [],
        })
        return
    raise ValueError(f"unknown mutation: {name}")


# --- One run ------------------------------------------------------------------

_ASKS_DONE = 0  # process-wide: the very first ask is the cold one


@contextlib.contextmanager
def _environment(db_path: str, repo_root: str, *, scripted: bool):
    saved = {k: os.environ.get(k) for k in _ENV_TO_CLEAR}
    keys = ("PROBE_DB_PATH", "CONTROL_ADMIN_USERNAME", "CONTROL_ADMIN_PASSWORD",
            "LLM_PROVIDER", "PROBE_REPOSITORY_ROOTS", "INTELLIGENCE_LLM_MODEL", "LLM_API_KEY")
    saved.update({k: os.environ.get(k) for k in keys})
    os.environ["PROBE_DB_PATH"] = db_path
    os.environ["CONTROL_ADMIN_USERNAME"] = "root"
    os.environ["CONTROL_ADMIN_PASSWORD"] = "s3cret"
    os.environ["PROBE_REPOSITORY_ROOTS"] = repo_root
    if scripted:
        for k in _ENV_TO_CLEAR:
            os.environ.pop(k, None)
        # `no_llm` questions flip these back to the mock provider per run.
        os.environ["LLM_PROVIDER"] = "openai"
        os.environ["INTELLIGENCE_LLM_MODEL"] = "gpt-5"
        os.environ["LLM_API_KEY"] = "unused"
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class RealBudget:
    def __init__(self, max_calls: int):
        self.max_calls = max_calls
        self.calls = 0


def _wrap_real_client(client, budget: RealBudget, tally: Dict[str, Any]):
    original = client.generate_text

    def generate_text(*a, **kw):
        if budget.calls >= budget.max_calls:
            raise _MaxCallsReached()
        budget.calls += 1
        tally["calls"] += 1
        started = time.perf_counter()
        if tally["first_started"] is None:
            tally["first_started"] = started
        try:
            return original(*a, **kw)
        finally:
            tally["llm_wall_s"] += time.perf_counter() - started

    client.generate_text = generate_text  # type: ignore[method-assign]
    return client


def run_question(
    q: Dict[str, Any],
    *,
    mode: str,
    delay_ms: float,
    counter: CallCounter,
    budget: Optional[RealBudget] = None,
    repeat_index: int = 0,
) -> Dict[str, Any]:
    """Run one question once, on a fresh DB.  Returns the per-run record."""
    global _ASKS_DONE
    from fastapi.testclient import TestClient

    scripted = mode == "structural"
    script = q["llm_script"]
    record: Dict[str, Any] = {
        "id": q["id"], "category": q["category"], "screen_id": q["screen_id"],
        "fixture": q["fixture"], "llm_script": script, "repeat_index": repeat_index,
    }
    if mode == "real" and script in REAL_MODE_UNREPRODUCIBLE_SCRIPTS:
        record["skipped"] = "scripted_failure_not_reproducible_in_real_mode"
        return record

    with tempfile.TemporaryDirectory(prefix="assistant-eval-") as tmp, \
            _environment(str(Path(tmp) / "eval.db"), tmp, scripted=scripted), \
            contextlib.ExitStack() as stack:
        if scripted and script == "no_llm":
            os.environ["LLM_PROVIDER"] = "mock"
            os.environ.pop("LLM_API_KEY", None)
        from app.llm import get_llm_client
        get_llm_client.cache_clear()
        from app.main import app

        scripted_client = ScriptedClient(script, delay_ms) if scripted and script != "no_llm" else None
        real_tally = {"calls": 0, "first_started": None, "llm_wall_s": 0.0}

        def _fake_create(config, *a, **kw):
            if scripted_client is not None:
                return scripted_client
            from app.llm import create_llm_client as real_create
            return _wrap_real_client(real_create(config, *a, **kw), budget, real_tally)  # type: ignore[arg-type]

        if scripted_client is not None or mode == "real":
            import app.llm as llm_mod
            import app.routes.assistant as assistant_route
            stack.enter_context(mock.patch.object(assistant_route, "create_llm_client", _fake_create, create=True))
            stack.enter_context(mock.patch.object(llm_mod, "create_llm_client", _fake_create))

        with TestClient(app) as client:
            r = client.post("/auth/login", json={"username": "root", "password": "s3cret"})
            token = r.cookies.get("probe_session")
            sysr = client.post("/systems", json={"name": "eval-sys", "environment": "test",
                                                 "description": "assistant eval"},
                               headers={"Authorization": f"Bearer {token}"})
            env = Env(client, token, sysr.json()["id"])
            FIXTURE_SETUP[q["fixture"]](env)
            for p in env.patches:
                stack.enter_context(p)

            body: Dict[str, Any] = {
                "screen_id": q["screen_id"], "question": q["question"],
                "route_params": dict(q.get("route_params") or {}),
            }
            thread = q.get("thread")
            setup_turns = q.get("conversation_setup") or []
            if thread:
                created = env.post("/assistant/discussion-threads", {
                    "scope": thread["scope"], "screen_id": q["screen_id"],
                    "target_kind": thread["target_kind"], "target_ref": thread["target_ref"],
                })
                thread_id = created["thread"]["id"]
                body["thread_id"] = thread_id
                for turn in setup_turns:
                    if turn.get("role") != "user":
                        continue  # assistant turns are generated by the ask itself
                    env.post("/assistant/ask", {
                        "screen_id": q["screen_id"], "question": turn["content"],
                        "thread_id": thread_id,
                        "route_params": dict(q.get("route_params") or {}),
                    })
                    _ASKS_DONE += 1
                if q.get("after_setup"):
                    _apply_mutation(env, q["after_setup"])
                if q.get("ui_draft"):
                    body["ui_draft"] = {
                        "target_kind": thread["target_kind"], "target_ref": thread["target_ref"],
                        "form_id": q["ui_draft"]["form_id"],
                        "fields": q["ui_draft"]["fields"],
                    }
            elif setup_turns:
                body["conversation"] = [
                    {"role": t["role"], "content": t["content"]} for t in setup_turns
                ]

            # Reset per-ask instrumentation right before the measured ask.
            counter.reset()
            if scripted_client is not None:
                scripted_client.calls = 0
                scripted_client.llm_wall_s = 0.0
                scripted_client.first_call_started = None
            real_tally.update({"calls": 0, "first_started": None, "llm_wall_s": 0.0})
            cold = _ASKS_DONE == 0

            t0 = time.perf_counter()
            resp = client.post("/assistant/ask", json=body, headers=env.headers)
            wall_ms = (time.perf_counter() - t0) * 1000.0
            _ASKS_DONE += 1

            data: Dict[str, Any] = {}
            try:
                data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            except ValueError:
                data = {}
            if not isinstance(data, dict):
                data = {}

            tally = real_tally if scripted_client is None else {
                "calls": scripted_client.calls,
                "first_started": scripted_client.first_call_started,
                "llm_wall_s": scripted_client.llm_wall_s,
            }
            pre_llm_ms = ((tally["first_started"] - t0) * 1000.0
                          if tally["first_started"] is not None else None)
            llm_ms = tally["llm_wall_s"] * 1000.0
            failure = data.get("failure")
            expected = q.get("expected_sources") or []
            available = set(scripted_client.last_context_ids) if scripted_client else None
            missing_expected = (
                [f"{e['type']}:{e['id']}" for e in expected if (e["type"], e["id"]) not in available]
                if available is not None else None
            )
            record.update({
                "http_status": resp.status_code,
                "cold": cold,
                "wall_ms": round(wall_ms, 2),
                "pre_llm_ms": None if pre_llm_ms is None else round(pre_llm_ms, 2),
                "llm_ms": round(llm_ms, 2),
                "non_llm_ms": round(wall_ms - llm_ms, 2),
                "llm_calls": tally["calls"],
                "used_fallback": data.get("used_fallback"),
                "decision_method": data.get("decision_method"),
                "citations": len(data.get("citations") or []),
                "answer_length": len(data.get("answer") or ""),
                # Fields added by Epic #467; None on the base commit.
                "request_id": data.get("request_id"),
                "answer_status": data.get("answer_status"),
                "grounding_state": data.get("grounding_state"),
                "failure_class": failure.get("failure_class") if isinstance(failure, dict) else None,
                "screen_context_state": data.get("screen_context_state"),
                "target_state": data.get("target_state"),
                "diagnostics_calls": counter.counts["run_system_diagnostics"],
                "system_state_calls": counter.counts["build_system_state"],
                "overview_calls": counter.counts["build_overview"],
                "llm_input_chars": scripted_client.last_input_chars if scripted_client else None,
                "screen_data_list_sizes": scripted_client.last_list_sizes if scripted_client else None,
                "expected_sources_missing_from_context": missing_expected,
            })
    return record


# --- Summaries ----------------------------------------------------------------


def _ok(rec: Dict[str, Any]) -> bool:
    return "skipped" not in rec and "http_status" in rec


def summarize(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    ran = [r for r in records if _ok(r)]
    skipped = [r for r in records if not _ok(r)]
    warm = [r for r in ran if not r.get("cold")]
    cold = [r for r in ran if r.get("cold")]

    per_category: Dict[str, Dict[str, Any]] = {}
    for cat in CATEGORIES:
        rs = [r for r in ran if r["category"] == cat]
        per_category[cat] = {
            "questions": len({r["id"] for r in rs}),
            "runs": len(rs),
            "used_fallback": sum(1 for r in rs if r.get("used_fallback")),
            "http_errors": sum(1 for r in rs if r["http_status"] >= 400),
            "wall": dist([r["wall_ms"] for r in rs if not r.get("cold")]),
        }

    def mean(vals: List[int]) -> Optional[float]:
        return round(sum(vals) / len(vals), 3) if vals else None

    by_screen: Dict[str, Dict[str, Any]] = {}
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in ran:
        grouped[r["screen_id"]].append(r)
    for screen, rs in sorted(grouped.items()):
        by_screen[screen] = {
            "asks": len(rs),
            "diagnostics_calls_per_ask": mean([r["diagnostics_calls"] for r in rs]),
            "system_state_calls_per_ask": mean([r["system_state_calls"] for r in rs]),
            "overview_calls_per_ask": mean([r["overview_calls"] for r in rs]),
            "llm_calls_per_ask": mean([r["llm_calls"] for r in rs]),
        }

    # Issues #471/#472: answer_status / grounding_state distributions per
    # llm_script.  `None` keys mean the field was absent (base commit).
    by_script: Dict[str, Dict[str, Any]] = {}
    for script in sorted({r["llm_script"] for r in ran}):
        rs = [r for r in ran if r["llm_script"] == script]
        status_counts: Dict[str, int] = defaultdict(int)
        grounding_counts: Dict[str, int] = defaultdict(int)
        failure_counts: Dict[str, int] = defaultdict(int)
        for r in rs:
            status_counts[str(r.get("answer_status"))] += 1
            grounding_counts[str(r.get("grounding_state"))] += 1
            if r.get("failure_class"):
                failure_counts[r["failure_class"]] += 1
        by_script[script] = {
            "runs": len(rs),
            "answer_status": dict(status_counts),
            "grounding_state": dict(grounding_counts),
            "failure_class": dict(failure_counts),
        }

    n = len(ran)
    http_fail = sum(1 for r in ran if r["http_status"] >= 400)
    status_present = [r for r in ran if r.get("answer_status") is not None]
    status_failed = sum(1 for r in status_present if r["answer_status"] == "failed")
    fallback = sum(1 for r in ran if r.get("used_fallback"))
    return {
        "runs": n,
        "skipped": [{"id": r["id"], "reason": r.get("skipped")} for r in skipped],
        "per_category": per_category,
        "latency_warm": {
            "wall": dist([r["wall_ms"] for r in warm]),
            "pre_llm": dist([r["pre_llm_ms"] for r in warm if r.get("pre_llm_ms") is not None]),
            "non_llm": dist([r["non_llm_ms"] for r in warm]),
        },
        "latency_cold_first_ask": [
            {"id": r["id"], "wall_ms": r["wall_ms"], "pre_llm_ms": r.get("pre_llm_ms")} for r in cold
        ],
        "failures": {
            "denominator": n,
            "http_error_count": http_fail,
            "http_error_rate": round(http_fail / n, 4) if n else None,
            "used_fallback_count": fallback,
            "used_fallback_rate": round(fallback / n, 4) if n else None,
            "answer_status_failed_count": status_failed if status_present else None,
            "answer_status_state": "measured" if status_present else "unmeasured (field absent)",
        },
        "by_llm_script": by_script,
        "calls_per_ask": {
            "overall": {
                "diagnostics": mean([r["diagnostics_calls"] for r in ran]),
                "system_state": mean([r["system_state_calls"] for r in ran]),
                "overview": mean([r["overview_calls"] for r in ran]),
                "llm": mean([r["llm_calls"] for r in ran]),
            },
            "by_screen": by_screen,
        },
    }


def _git_head() -> Optional[str]:
    try:
        return subprocess.run(
            ["git", "-C", str(SERVER_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or None
    except Exception:
        return None


def load_questions(path: Path) -> List[Dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data["questions"] if isinstance(data, dict) else data


def write_scoring_template(questions: List[Dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(SCORING_COLUMNS)
        for q in questions:
            w.writerow([q["id"], q["category"], q["question"]] + [""] * (len(SCORING_COLUMNS) - 3))


def run_all(
    questions: List[Dict[str, Any]], *, mode: str, repeat: int, delay_ms: float,
    budget: Optional[RealBudget] = None,
) -> Tuple[List[Dict[str, Any]], bool]:
    """Returns (records, stopped_by_max_calls)."""
    sys.path.insert(0, str(SERVER_ROOT))
    import app.main  # noqa: F401  (import once so the counter can see every module)

    counter = CallCounter()
    counter.install()
    records: List[Dict[str, Any]] = []
    stopped = False
    try:
        for q in questions:
            if q.get("harness_support") != "automated":
                records.append({"id": q["id"], "category": q["category"], "screen_id": q["screen_id"],
                                "skipped": f"manual_only: {q.get('harness_reason', '')}"})
                continue
            for k in range(repeat):
                try:
                    records.append(run_question(q, mode=mode, delay_ms=delay_ms, counter=counter,
                                                budget=budget, repeat_index=k))
                except _MaxCallsReached:
                    stopped = True
                    break
            if stopped:
                break
    finally:
        counter.uninstall()
    return records, stopped


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=("structural", "real"), default="structural")
    ap.add_argument("--max-calls", type=int, default=None,
                    help="real mode only: hard cap on LLM calls (required)")
    ap.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS)
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--delay-ms", type=float, default=0.0,
                    help="structural mode only: injected LLM latency")
    ap.add_argument("--scoring-template", type=Path, default=None)
    args = ap.parse_args(argv)

    questions = load_questions(args.questions)
    if args.scoring_template:
        write_scoring_template(questions, args.scoring_template)
        print(f"scoring template written: {args.scoring_template}")
        if args.out is None:
            return 0

    if args.mode == "real" and (args.max_calls is None or args.max_calls < 1):
        print("refused: --mode real requires --max-calls N (N >= 1). "
              "Real runs need an approved plan: target data, max calls, cost cap.",
              file=sys.stderr)
        return 2
    if args.mode == "structural" and args.max_calls is not None:
        print("note: --max-calls is ignored in structural mode", file=sys.stderr)
    if args.mode == "real" and args.delay_ms:
        print("refused: --delay-ms is a structural-mode injection", file=sys.stderr)
        return 2

    budget = RealBudget(args.max_calls) if args.mode == "real" else None
    records, stopped = run_all(questions, mode=args.mode, repeat=args.repeat,
                               delay_ms=args.delay_ms, budget=budget)
    report = {
        "mode": args.mode,
        "llm": "scripted_fake" if args.mode == "structural" else "configured_provider",
        "notice": (
            "STRUCTURAL RESULTS ARE NOT QUALITY MEASUREMENTS: a scripted fake LLM answered."
            if args.mode == "structural"
            else "Real-LLM run: answers must still be scored by a human (see the scoring CSV)."
        ),
        "commit": _git_head(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "environment": {
            "python": platform.python_version(), "platform": platform.platform(),
            "db": "sqlite temp file per run", "concurrency": 1,
            "repeat": args.repeat, "delay_ms": args.delay_ms if args.mode == "structural" else None,
            "questions_file": str(args.questions),
        },
        "real_mode": None if args.mode == "structural" else {
            "max_calls": args.max_calls, "calls_made": budget.calls if budget else None,
            "stopped_by_max_calls": stopped,
        },
        "summary": summarize(records),
        "results": records,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"report written: {args.out}")
    s = report["summary"]
    print(f"mode={report['mode']} llm={report['llm']} runs={s['runs']} "
          f"skipped={len(s['skipped'])}")
    print("wall warm:", s["latency_warm"]["wall"])
    print("calls/ask overall:", s["calls_per_ask"]["overall"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

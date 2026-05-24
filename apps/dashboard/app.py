import ast
import datetime as dt
import difflib
import json
import os
from typing import Any, Dict, List, Optional

import requests
import streamlit as st


def _maybe_pretty(text: Optional[str]) -> str:
    """Best-effort pretty-print so the diff is readable.

    The SDK serializes outputs with ``repr()``; for strings/dicts/lists
    we can re-parse via ``ast.literal_eval`` and pretty JSON it. JSON
    strings are also handled. Anything else is returned verbatim.
    """
    if text is None:
        return ""
    raw = text.strip()
    if not raw:
        return text

    try:
        return json.dumps(json.loads(raw), indent=2, ensure_ascii=False, sort_keys=True)
    except (ValueError, TypeError):
        pass
    try:
        return json.dumps(
            ast.literal_eval(raw), indent=2, ensure_ascii=False, sort_keys=True, default=str
        )
    except (ValueError, SyntaxError, TypeError):
        pass
    return text


def _unified_diff(current: Optional[str], candidate: Optional[str]) -> str:
    a = _maybe_pretty(current).splitlines()
    b = _maybe_pretty(candidate).splitlines()
    return "\n".join(
        difflib.unified_diff(a, b, fromfile="current", tofile="candidate", lineterm="")
    )

SERVER_URL = os.getenv("PROBE_SERVER_URL", "http://localhost:8000").rstrip("/")
MODES = ["off", "trace", "shadow"]
EVALUATIONS = ["unknown", "better", "worse", "same"]
CRITERION_TYPES = [
    "natural_language",
    "exact_match",
    "json_equal",
    "required_keys",
    "contains",
    "regex",
]
STATUS_ICON = {"ok": "✅", "ng": "❌", "needs_review": "🔍"}


def api_get(path: str) -> Optional[Any]:
    try:
        r = requests.get(f"{SERVER_URL}{path}", timeout=3)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"GET {path} failed: {e}")
        return None


def api_put(path: str, payload: Dict[str, Any]) -> Optional[Any]:
    try:
        r = requests.put(f"{SERVER_URL}{path}", json=payload, timeout=3)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"PUT {path} failed: {e}")
        return None


def api_post(path: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    try:
        r = requests.post(f"{SERVER_URL}{path}", json=payload, timeout=5)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"POST {path} failed: {e}")
        return None


def _lines_to_list(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


st.set_page_config(page_title="probe-agent", layout="wide")
st.title("probe-agent dashboard")
st.caption(f"Control Server: {SERVER_URL}")

# --- System profile -------------------------------------------------------
with st.expander("System Profile（システム全体の目的・価値・制約）"):
    sp = api_get("/system-profile") or {}
    with st.form("system-profile-form"):
        sp_name = st.text_input("name", value=sp.get("name", ""))
        sp_purpose = st.text_area("purpose", value=sp.get("purpose", ""))
        sp_users = st.text_area(
            "target_users (1行1項目)", value="\n".join(sp.get("target_users", []))
        )
        sp_value = st.text_area("stakeholder_value", value=sp.get("stakeholder_value", ""))
        sp_constraints = st.text_area(
            "constraints (1行1項目)", value="\n".join(sp.get("constraints", []))
        )
        sp_success = st.text_area(
            "success_criteria (1行1項目)", value="\n".join(sp.get("success_criteria", []))
        )
        if st.form_submit_button("Save system profile"):
            api_put(
                "/system-profile",
                {
                    "name": sp_name,
                    "purpose": sp_purpose,
                    "target_users": _lines_to_list(sp_users),
                    "stakeholder_value": sp_value,
                    "constraints": _lines_to_list(sp_constraints),
                    "success_criteria": _lines_to_list(sp_success),
                },
            )
            st.success("system profile を保存しました")
            st.rerun()

components: List[Dict[str, Any]] = api_get("/components") or []
if not components:
    st.info("まだ component がありません。`@probe` を付けた関数を実行してください。")
    st.stop()

ids = [c["component_id"] for c in components]
selected = st.sidebar.selectbox("Component", ids)
component = next(c for c in components if c["component_id"] == selected)

st.sidebar.markdown("### Components")
for c in components:
    st.sidebar.write(
        f"- **{c['component_id']}** "
        f"`{c['mode']}` "
        f"({c.get('trace_count', 0)} traces)"
    )

# --- Header / mode toggle -------------------------------------------------
col1, col2, col3 = st.columns([2, 1, 2])
col1.metric("Component", component["component_id"])
col2.metric("Traces", component.get("trace_count", 0))
col3.metric("Last seen", fmt_ts(component.get("last_seen")))

st.subheader("Mode")
current_mode = component["mode"]
new_mode = st.radio(
    "実行モード",
    MODES,
    index=MODES.index(current_mode) if current_mode in MODES else 1,
    horizontal=True,
    key=f"mode-{selected}",
)
if new_mode != current_mode:
    if st.button(f"Switch to {new_mode}"):
        api_put(f"/components/{selected}/policy", {"mode": new_mode})
        st.success(f"mode を {new_mode} に変更しました")
        st.rerun()

st.divider()

# --- Component profile -----------------------------------------------------
st.subheader("Component Profile（責務・入出力期待・失敗時の影響）")
cp = api_get(f"/components/{selected}/profile") or {}
with st.form(f"component-profile-{selected}"):
    cp_purpose = st.text_area("purpose", value=cp.get("purpose", ""))
    cp_resp = st.text_area("responsibility", value=cp.get("responsibility", ""))
    cp_in = st.text_area("expected_input", value=cp.get("expected_input", ""))
    cp_out = st.text_area("expected_output", value=cp.get("expected_output", ""))
    cp_fail = st.text_area("failure_impact", value=cp.get("failure_impact", ""))
    cp_notes = st.text_area("notes", value=cp.get("notes", ""))
    if st.form_submit_button("Save component profile"):
        api_put(
            f"/components/{selected}/profile",
            {
                "purpose": cp_purpose,
                "responsibility": cp_resp,
                "expected_input": cp_in,
                "expected_output": cp_out,
                "failure_impact": cp_fail,
                "notes": cp_notes,
            },
        )
        st.success("component profile を保存しました")
        st.rerun()

st.divider()

# --- Evaluation criteria ---------------------------------------------------
st.subheader("Evaluation Criteria（評価基準）")
criteria = api_get(f"/components/{selected}/criteria") or []
if not criteria:
    st.write("評価基準なし。下のフォームで追加してください。")
for c in criteria:
    with st.expander(
        f"#{c['id']} {c['name']} [{c['criterion_type']}] "
        f"{'有効' if c.get('enabled') else '無効'}"
    ):
        with st.form(f"criterion-{c['id']}"):
            name = st.text_input("name", value=c.get("name", ""))
            desc = st.text_area("description", value=c.get("description") or "")
            ctype = st.selectbox(
                "criterion_type",
                CRITERION_TYPES,
                index=CRITERION_TYPES.index(c["criterion_type"]),
            )
            expected = st.text_area(
                "expected_value", value=c.get("expected_value") or ""
            )
            weight = st.number_input("weight", value=float(c.get("weight", 1.0)))
            enabled = st.checkbox("enabled", value=bool(c.get("enabled", True)))
            if st.form_submit_button("Save"):
                api_put(
                    f"/criteria/{c['id']}",
                    {
                        "name": name,
                        "description": desc,
                        "criterion_type": ctype,
                        "expected_value": expected or None,
                        "weight": weight,
                        "enabled": enabled,
                    },
                )
                st.success("criterion を更新しました")
                st.rerun()

with st.form(f"new-criterion-{selected}"):
    st.markdown("**新しい評価基準を追加**")
    new_name = st.text_input("name", key=f"newname-{selected}")
    new_type = st.selectbox("criterion_type", CRITERION_TYPES, key=f"newtype-{selected}")
    new_expected = st.text_area(
        "expected_value (contains は部分文字列, required_keys/json_equal は JSON)",
        key=f"newexp-{selected}",
    )
    new_desc = st.text_area("description", key=f"newdesc-{selected}")
    if st.form_submit_button("Add criterion"):
        if not new_name.strip():
            st.error("name は必須です")
        else:
            api_post(
                f"/components/{selected}/criteria",
                {
                    "name": new_name,
                    "description": new_desc,
                    "criterion_type": new_type,
                    "expected_value": new_expected or None,
                },
            )
            st.success("criterion を追加しました")
            st.rerun()

st.divider()

# --- Traces ---------------------------------------------------------------
st.subheader("Latest Traces")
traces = api_get(f"/components/{selected}/traces?limit=50") or []
if not traces:
    st.write("traces なし")
else:
    for t in traces:
        with st.expander(
            f"{fmt_ts(t['timestamp'])} | {t.get('mode') or '-'} "
            f"| {t.get('duration_ms', 0):.2f}ms "
            f"{'⚠ error' if t.get('error') else ''}"
        ):
            st.markdown("**Input**")
            st.code(json.dumps(t.get("input"), ensure_ascii=False, indent=2))
            st.markdown("**Output**")
            st.code(t.get("output") or "")
            if t.get("error"):
                st.markdown("**Error**")
                st.code(t["error"])
            st.caption(f"trace_id: {t['trace_id']}")

            st.markdown("**Evaluation**")
            if st.button("Evaluate against criteria", key=f"eval-btn-{t['trace_id']}"):
                api_post(f"/traces/{t['trace_id']}/evaluate")
                st.rerun()
            evals = api_get(f"/traces/{t['trace_id']}/evaluations") or []
            if not evals:
                st.caption("未評価")
            for e in evals:
                icon = STATUS_ICON.get(e["status"], "")
                score = e.get("score")
                score_txt = f" (score={score})" if score is not None else ""
                st.write(
                    f"{icon} criterion #{e['criterion_id']} → "
                    f"**{e['status']}**{score_txt} — {e.get('reason', '')}"
                )

st.divider()

# --- Shadow comparison ----------------------------------------------------
st.subheader("Shadow Comparison")
shadows = api_get(f"/components/{selected}/shadow-results?limit=50") or []
if not shadows:
    st.write("shadow 実行結果なし。mode を `shadow` にして候補を登録してください。")
else:
    for s in shadows:
        same = (s.get("current_output") == s.get("candidate_output")) and not s.get(
            "candidate_error"
        )
        marker = "=" if same else "≠"
        with st.expander(
            f"{fmt_ts(s['timestamp'])} | {marker} | "
            f"candidate {s.get('candidate_duration_ms', 0):.2f}ms "
            f"| eval={s.get('evaluation') or 'unknown'}"
        ):
            left, right = st.columns(2)
            left.markdown("**current output**")
            left.code(s.get("current_output") or "")
            right.markdown("**candidate output**")
            if s.get("candidate_error"):
                right.error(s["candidate_error"])
            else:
                right.code(s.get("candidate_output") or "")

            st.markdown("**diff**")
            if s.get("candidate_error"):
                st.warning("candidate raised an error — no diff available")
            elif same:
                st.success("no diff (current == candidate)")
            else:
                diff = _unified_diff(s.get("current_output"), s.get("candidate_output"))
                if diff:
                    st.code(diff, language="diff")
                else:
                    # Outputs differ as raw strings but pretty-prints collapsed
                    # them to the same form (e.g. whitespace-only differences).
                    st.info("difference present only in raw representation; pretty form is equal")

            current_eval = s.get("evaluation") or "unknown"
            new_eval = st.selectbox(
                "manual evaluation",
                EVALUATIONS,
                index=EVALUATIONS.index(current_eval) if current_eval in EVALUATIONS else 0,
                key=f"eval-{s['id']}",
            )
            if new_eval != current_eval:
                if st.button("Save", key=f"save-{s['id']}"):
                    api_put(
                        f"/shadow-results/{s['id']}/evaluation",
                        {"evaluation": new_eval},
                    )
                    st.success("評価を保存しました")
                    st.rerun()
            st.caption(f"trace_id: {s['trace_id']}")

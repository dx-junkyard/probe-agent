import datetime as dt
import json
import os
from typing import Any, Dict, List, Optional

import requests
import streamlit as st

SERVER_URL = os.getenv("PROBE_SERVER_URL", "http://localhost:8000").rstrip("/")
MODES = ["off", "trace", "shadow"]
EVALUATIONS = ["unknown", "better", "worse", "same"]


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


def fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


st.set_page_config(page_title="probe-agent", layout="wide")
st.title("probe-agent dashboard")
st.caption(f"Control Server: {SERVER_URL}")

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

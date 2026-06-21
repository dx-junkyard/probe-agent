import ast
import datetime as dt
import difflib
import json
import os
from typing import Any, Dict, List, Optional

import requests
import streamlit as st
from streamlit_cookies_controller import CookieController, RemoveEmptyElementContainer


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
CLIENT_SERVER_URL = os.getenv("PROBE_CLIENT_SERVER_URL", "").strip().rstrip("/")
SDK_INSTALL_URL = os.getenv(
    "PROBE_SDK_INSTALL_URL",
    "git+https://github.com/dx-junkyard/probe-agent.git@main#subdirectory=packages/python-probe",
)
# Service/fallback credential. A browser login session (kept in
# ``st.session_state``) takes precedence; without one, ``DASHBOARD_API_KEY``
# (falling back to ``PROBE_API_KEY``, shared with the SDK) is sent as
# ``X-Api-Key``. With neither, requests are sent unauthenticated as before.
ENV_API_KEY = os.getenv("DASHBOARD_API_KEY") or os.getenv("PROBE_API_KEY")
SESSION_COOKIE_NAME = "probe_dashboard_session"
SESSION_COOKIE_SECURE = os.getenv(
    "DASHBOARD_COOKIE_SECURE", ""
).strip().lower() in ("1", "true", "yes", "on")
MODES = ["off", "trace", "shadow"]
EVALUATIONS = ["unknown", "better", "worse", "same"]
GENERATION_VERDICT_ICON = {
    "better": "✅",
    "worse": "❌",
    "same": "➖",
    "unsafe": "⚠",
    "error": "⛔",
    "unknown": "❔",
}
CRITERION_TYPES = [
    "natural_language",
    "exact_match",
    "json_equal",
    "required_keys",
    "contains",
    "regex",
]
STATUS_ICON = {"ok": "✅", "ng": "❌", "needs_review": "🔍"}
ROLES = ["user", "admin"]


def _session_token() -> Optional[str]:
    return st.session_state.get("session_token")


def _restore_session_from_cookie() -> None:
    if _session_token():
        return
    token = cookie_controller.get(SESSION_COOKIE_NAME)
    if isinstance(token, str) and token:
        st.session_state["session_token"] = token


def _apply_pending_cookie_change() -> None:
    change = st.session_state.pop("pending_session_cookie", None)
    if not change:
        return
    if change["action"] == "set":
        expires_at = change.get("expires_at")
        expires = (
            dt.datetime.fromtimestamp(expires_at)
            if expires_at is not None
            else dt.datetime.now() + dt.timedelta(days=7)
        )
        cookie_controller.set(
            SESSION_COOKIE_NAME,
            change["token"],
            expires=expires,
            secure=SESSION_COOKIE_SECURE,
            same_site="strict",
        )
    elif cookie_controller.get(SESSION_COOKIE_NAME) is not None:
        cookie_controller.remove(
            SESSION_COOKIE_NAME,
            secure=SESSION_COOKIE_SECURE,
            same_site="strict",
        )


def _queue_session_cookie(token: str, expires_at: Optional[float]) -> None:
    st.session_state["pending_session_cookie"] = {
        "action": "set",
        "token": token,
        "expires_at": expires_at,
    }


def _clear_session_state_and_cookie() -> None:
    st.session_state.pop("session_token", None)
    st.session_state.pop("selected_system_id", None)
    st.session_state.pop("system_selector", None)
    st.session_state["pending_session_cookie"] = {"action": "remove"}


def _auth_headers() -> Dict[str, str]:
    token = _session_token()
    if token:
        headers = {"Authorization": f"Bearer {token}"}
        system_id = st.session_state.get("selected_system_id")
        if system_id is not None:
            headers["X-Probe-System-Id"] = str(system_id)
        return headers
    if ENV_API_KEY:
        return {"X-Api-Key": ENV_API_KEY}
    return {}


def api_get(path: str) -> Optional[Any]:
    try:
        r = requests.get(f"{SERVER_URL}{path}", headers=_auth_headers(), timeout=3)
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"GET {path} failed: {e}")
        return None


def api_put(path: str, payload: Dict[str, Any]) -> Optional[Any]:
    try:
        r = requests.put(
            f"{SERVER_URL}{path}", json=payload, headers=_auth_headers(), timeout=3
        )
        r.raise_for_status()
        return r.json()
    except requests.RequestException as e:
        st.error(f"PUT {path} failed: {e}")
        return None


def api_post(path: str, payload: Optional[Dict[str, Any]] = None) -> Optional[Any]:
    try:
        r = requests.post(
            f"{SERVER_URL}{path}", json=payload, headers=_auth_headers(), timeout=5
        )
        r.raise_for_status()
        if r.status_code == 204 or not r.content:
            return {}
        return r.json()
    except requests.RequestException as e:
        st.error(f"POST {path} failed: {e}")
        return None


def api_delete(path: str) -> bool:
    try:
        r = requests.delete(
            f"{SERVER_URL}{path}", headers=_auth_headers(), timeout=5
        )
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        st.error(f"DELETE {path} failed: {e}")
        return False


def _fetch_me() -> Optional[Dict[str, Any]]:
    """Resolve the current principal (`/auth/me`), or None if unauthorized.

    Kept quiet (no st.error) so non-admin or unauthenticated dashboards
    simply hide the management UI instead of showing an error.
    """
    try:
        r = requests.get(
            f"{SERVER_URL}/auth/me", headers=_auth_headers(), timeout=3
        )
        if r.status_code != 200:
            return None
        return r.json()
    except requests.RequestException:
        return None


def _do_login(username: str, password: str) -> bool:
    try:
        r = requests.post(
            f"{SERVER_URL}/auth/login",
            json={"username": username, "password": password},
            timeout=5,
        )
    except requests.RequestException as e:
        st.error(f"login failed: {e}")
        return False
    if r.status_code != 200:
        detail = ""
        try:
            detail = r.json().get("detail", "")
        except ValueError:
            pass
        st.error(f"login failed: {detail or r.status_code}")
        return False
    body = r.json()
    token = body["access_token"]
    st.session_state["session_token"] = token
    _queue_session_cookie(token, body.get("expires_at"))
    return True


def _do_logout() -> None:
    # Revoke the session server-side, then drop all auth-related state.
    if _session_token():
        try:
            requests.post(
                f"{SERVER_URL}/auth/logout", headers=_auth_headers(), timeout=5
            )
        except requests.RequestException:
            pass
    _clear_session_state_and_cookie()
    st.session_state.pop("issued_token", None)


def _lines_to_list(text: str) -> List[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def fmt_ts(ts: Optional[float]) -> str:
    if ts is None:
        return "-"
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


def _client_server_url() -> str:
    if CLIENT_SERVER_URL:
        return CLIENT_SERVER_URL
    if SERVER_URL in ("http://control-server:8000", "https://control-server:8000"):
        return SERVER_URL.replace("control-server", "localhost")
    return SERVER_URL


def _current_or_placeholder_token() -> str:
    issued = st.session_state.get("issued_token")
    if issued and issued.get("token"):
        return issued["token"]
    return "<issued-api-token>"


def _probe_env_text() -> str:
    return "\n".join(
        [
            "PROBE_ENABLED=true",
            f"PROBE_SERVER_URL={_client_server_url()}",
            f"PROBE_API_KEY={_current_or_placeholder_token()}",
            "PROBE_DEFAULT_MODE=trace",
            "",
        ]
    )


def _sample_probe_source() -> str:
    return '''"""Minimal probe-agent client sample.

Run:
    python sample_probe_client.py
"""

from probe_agent import flush, probe, set_candidate


def summarize_v2(text: str) -> str:
    """Candidate implementation used when the component is in shadow mode."""
    return text.split(".")[0].strip()


set_candidate("summarizer", summarize_v2)


@probe(component_id="summarizer")
def summarize(text: str) -> str:
    return text[:80]


@probe(component_id="classifier")
def classify(text: str) -> str:
    return "long" if len(text) > 80 else "short"


if __name__ == "__main__":
    text = (
        "probe-agent records component inputs and outputs. "
        "Shadow mode compares a candidate implementation safely."
    )
    print("summary:", summarize(text))
    print("class:", classify(text))
    flush()
'''


def _sample_dockerfile() -> str:
    return f"""FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \\
    && apt-get install -y --no-install-recommends git \\
    && rm -rf /var/lib/apt/lists/*

RUN pip install "{SDK_INSTALL_URL}"

COPY sample_probe_client.py /app/sample_probe_client.py

ENV PROBE_ENABLED=true
ENV PROBE_SERVER_URL={_client_server_url()}
ENV PROBE_API_KEY=<issued-api-token>

CMD ["python", "sample_probe_client.py"]
"""


# --- Sidebar: account / login ----------------------------------------------


def render_account_sidebar(me: Optional[Dict[str, Any]]) -> None:
    st.sidebar.markdown("### Account")
    user = (me or {}).get("user")

    if _session_token():
        if user is None:
            # Session expired or revoked: drop it and fall through to the form.
            _clear_session_state_and_cookie()
            st.session_state["session_invalidated"] = True
            st.rerun()
        else:
            st.sidebar.write(f"**{user['username']}** ({user['role']})")
            if st.sidebar.button("Logout"):
                _do_logout()
                st.rerun()
            return

    st.sidebar.caption("未ログイン")

    if st.session_state.pop("session_invalidated", False):
        st.sidebar.warning("セッションが無効になりました。再ログインしてください。")

    with st.sidebar.form("login-form"):
        st.markdown("**Login**")
        username = st.text_input("username")
        password = st.text_input("password", type="password")
        if st.form_submit_button("Login"):
            if _do_login(username.strip(), password):
                st.rerun()


# --- SDK connection tab -----------------------------------------------------


def render_access_tokens(system: Dict[str, Any], *, compact: bool = False) -> None:
    st.subheader("Access Tokens")
    st.caption(
        f"このtokenで送信されたプローブは「{system['name']}」に記録されます。"
        "raw token は発行直後にしか表示されません。"
    )

    issued = st.session_state.get("issued_token")
    if issued:
        st.success(
            f"{system['name']}用のtokenを発行しました。"
            "raw token は今だけ表示されます。"
        )
        st.code(issued["token"])
        st.markdown("クライアント設定用 snippet:")
        st.code(_probe_env_text(), language="bash")
        if st.button("表示を閉じる（以後表示されません）"):
            st.session_state.pop("issued_token", None)
            st.rerun()

    with st.form(f"issue-token-form-{system['id']}"):
        st.markdown("**新しい API token を発行**")
        name = st.text_input("name（例: production、staging）")
        days = st.number_input(
            "expires_in_days（0 = 無期限）", min_value=0, value=0, step=1
        )
        if st.form_submit_button("Issue token"):
            payload: Dict[str, Any] = {
                "name": name.strip() or None,
                "system_id": system["id"],
            }
            if days:
                payload["expires_in_days"] = int(days)
            res = api_post("/tokens/me", payload)
            if res:
                st.session_state["issued_token"] = res
                st.rerun()

    st.markdown("#### 自分の token 一覧")
    tokens: List[Dict[str, Any]] = [
        token
        for token in (api_get("/tokens/me") or [])
        if token.get("kind") == "api" and token.get("system_id") == system["id"]
    ]
    if not tokens:
        st.write("token なし")
        return
    if compact:
        active_count = sum(1 for t in tokens if not t["revoked"])
        st.caption(f"このSystemのAPI token: {len(tokens)}件（active {active_count}件）")
        return
    header = st.columns([1, 3, 2, 2, 3, 3, 2])
    for col, label in zip(
        header, ["id", "name", "kind", "status", "created_at", "expires_at", "操作"]
    ):
        col.markdown(f"**{label}**")
    for t in tokens:
        c = st.columns([1, 3, 2, 2, 3, 3, 2])
        c[0].write(t["id"])
        c[1].write(t.get("name") or "-")
        c[2].write(t["kind"])
        c[3].write("revoked" if t["revoked"] else "active")
        c[4].write(fmt_ts(t["created_at"]))
        c[5].write(fmt_ts(t.get("expires_at")))
        with c[6]:
            if not t["revoked"]:
                if st.button("Revoke", key=f"my-revoke-{t['id']}"):
                    if api_post(f"/tokens/me/{t['id']}/revoke") is not None:
                        st.success(f"token #{t['id']} を失効しました")
                        st.rerun()


def render_connect_sdk_tab(system: Dict[str, Any]) -> None:
    st.subheader("Connect SDK")
    st.caption(
        f"選択中System「{system['name']}」へ、クライアント側のプローブを接続します。"
    )

    st.markdown("### 1. API tokenを発行")
    render_access_tokens(system, compact=True)

    st.divider()
    st.markdown("### 2. SDKをインストール")
    st.write(
        "現状はpackage registryへpublishしていないため、Git URLから"
        "`packages/python-probe`だけをインストールします。"
    )
    st.code(f'pip install "{SDK_INSTALL_URL}"', language="bash")

    st.markdown("ローカルでこのリポジトリをチェックアウト済みの場合:")
    st.code("pip install -e packages/python-probe", language="bash")

    st.divider()
    st.markdown("### 3. クライアント環境変数")
    st.write(
        "`PROBE_API_KEY`には、このタブで発行したraw tokenを設定します。"
        "raw tokenは発行直後にしか表示されません。"
    )
    st.code(_probe_env_text(), language="bash")
    st.caption(
        "この形式の.envをshellで読む場合は、下の実行例のように`set -a`で"
        "子プロセスへexportしてください。"
    )
    st.download_button(
        "Download .env.sample",
        data=_probe_env_text(),
        file_name=f"probe-agent-{system['id']}.env.sample",
        mime="text/plain",
    )

    st.divider()
    st.markdown("### 4. サンプルソースコード")
    st.write(
        "`@probe(component_id=...)`を対象関数に付けるだけで、入出力、エラー、"
        "実行時間がこのSystemへ送信されます。`set_candidate`を使うと、"
        "Dashboardでmodeを`shadow`へ切り替えたときに候補実装との比較ができます。"
    )
    sample_source = _sample_probe_source()
    st.code(sample_source, language="python")
    sample_cols = st.columns(2)
    with sample_cols[0]:
        st.download_button(
            "Download sample_probe_client.py",
            data=sample_source,
            file_name="sample_probe_client.py",
            mime="text/x-python",
        )
    with sample_cols[1]:
        st.download_button(
            "Download Dockerfile.sample",
            data=_sample_dockerfile(),
            file_name="Dockerfile.probe-sample",
            mime="text/plain",
        )

    st.markdown("実行例:")
    st.code(
        "set -a\n"
        "source probe-agent-<system-id>.env.sample\n"
        "set +a\n"
        "python sample_probe_client.py",
        language="bash",
    )

    st.markdown("環境変数を直接渡して実行する場合:")
    st.code(
        f"PROBE_SERVER_URL={_client_server_url()} \\\n"
        f"PROBE_API_KEY={_current_or_placeholder_token()} \\\n"
        "python sample_probe_client.py",
        language="bash",
    )


# --- Admin: User Management tab ----------------------------------------------


def render_admin_tab(me_user: Dict[str, Any]) -> None:
    st.subheader("User Management（管理者用）")
    users: List[Dict[str, Any]] = api_get("/users") or []
    usernames = {u["id"]: u["username"] for u in users}

    st.markdown("#### ユーザー一覧")
    header = st.columns([1, 3, 2, 2, 3, 3])
    for col, label in zip(
        header, ["id", "username", "role", "status", "created_at", "操作"]
    ):
        col.markdown(f"**{label}**")
    for u in users:
        c = st.columns([1, 3, 2, 2, 3, 3])
        c[0].write(u["id"])
        c[1].write(u["username"])
        c[2].write(u["role"])
        c[3].write("active" if u["is_active"] else "inactive")
        c[4].write(fmt_ts(u["created_at"]))
        with c[5]:
            if u["is_active"]:
                if st.checkbox("停止確認", key=f"deact-confirm-{u['id']}"):
                    if st.button("Deactivate", key=f"deact-{u['id']}"):
                        if api_post(f"/users/{u['id']}/deactivate") is not None:
                            st.success(f"{u['username']} を停止しました")
                            st.rerun()
            if st.checkbox("削除確認", key=f"del-confirm-{u['id']}"):
                if st.button("Delete", key=f"del-{u['id']}"):
                    if api_delete(f"/users/{u['id']}"):
                        st.success(f"{u['username']} を削除しました")
                        st.rerun()

    st.markdown("#### 新規ユーザー作成")
    with st.form("create-user-form"):
        new_username = st.text_input("username")
        new_password = st.text_input("password", type="password")
        new_role = st.selectbox("role", ROLES)
        if st.form_submit_button("Create user"):
            if not new_username.strip() or not new_password:
                st.error("username と password は必須です")
            elif (
                api_post(
                    "/users",
                    {
                        "username": new_username.strip(),
                        "password": new_password,
                        "role": new_role,
                    },
                )
                is not None
            ):
                st.success(f"ユーザー {new_username.strip()} を作成しました")
                st.rerun()

    if users:
        user_label = lambda uid: f"#{uid} {usernames.get(uid, '?')}"  # noqa: E731

        st.markdown("#### パスワードリセット")
        st.caption("リセットすると対象ユーザーの既存ログインセッションは失効します。")
        with st.form("reset-password-form"):
            target = st.selectbox(
                "対象ユーザー", [u["id"] for u in users], format_func=user_label
            )
            new_pw = st.text_input("新しい password", type="password")
            if st.form_submit_button("Reset password"):
                if not new_pw:
                    st.error("password は必須です")
                elif (
                    api_post(f"/users/{target}/password", {"password": new_pw})
                    is not None
                ):
                    st.success(f"{usernames.get(target)} のパスワードを変更しました")
                    st.rerun()

        st.markdown("#### Role 変更")
        with st.form("change-role-form"):
            target = st.selectbox(
                "対象ユーザー", [u["id"] for u in users],
                format_func=user_label, key="role-target",
            )
            role = st.selectbox("新しい role", ROLES)
            if st.form_submit_button("Change role"):
                if api_put(f"/users/{target}/role", {"role": role}) is not None:
                    st.success(f"{usernames.get(target)} の role を {role} にしました")
                    st.rerun()

    st.markdown("#### Token 一覧（全ユーザー）")
    tokens: List[Dict[str, Any]] = api_get("/tokens") or []
    if not tokens:
        st.write("token なし")
        return
    systems: List[Dict[str, Any]] = api_get("/systems") or []
    system_names = {s["id"]: s["name"] for s in systems}
    header = st.columns([1, 3, 2, 3, 3, 2, 3, 3, 2])
    for col, label in zip(
        header,
        [
            "id", "name", "kind", "user", "system", "status",
            "created_at", "expires_at", "操作",
        ],
    ):
        col.markdown(f"**{label}**")
    for t in tokens:
        c = st.columns([1, 3, 2, 3, 3, 2, 3, 3, 2])
        c[0].write(t["id"])
        c[1].write(t.get("name") or "-")
        c[2].write(t["kind"])
        c[3].write(f"#{t['user_id']} {usernames.get(t['user_id'], '?')}")
        c[4].write(system_names.get(t.get("system_id"), "-"))
        c[5].write("revoked" if t["revoked"] else "active")
        c[6].write(fmt_ts(t["created_at"]))
        c[7].write(fmt_ts(t.get("expires_at")))
        with c[8]:
            if not t["revoked"]:
                if st.button("Revoke", key=f"adm-revoke-{t['id']}"):
                    if api_post(f"/tokens/{t['id']}/revoke") is not None:
                        st.success(f"token #{t['id']} を失効しました")
                        st.rerun()


# --- Components tab ----------------------------------------------------------


def render_components_tab(system: Dict[str, Any]) -> None:
    components: List[Dict[str, Any]] = api_get("/components") or []
    if not components:
        st.info("まだ component がありません。`@probe` を付けた関数を実行してください。")
        return

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

    # --- Header / mode toggle ---------------------------------------------
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
        if st.button(f"Switch {system['name']} / {selected} to {new_mode}"):
            api_put(f"/components/{selected}/policy", {"mode": new_mode})
            st.success(f"{system['name']} / {selected} を {new_mode} に変更しました")
            st.rerun()

    st.divider()

    # --- Component profile --------------------------------------------------
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

    # --- Evaluation criteria --------------------------------------------------
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


def _format_trace_label(trace: Dict[str, Any]) -> str:
    error = " error" if trace.get("error") else ""
    return (
        f"{fmt_ts(trace.get('timestamp'))} | {trace.get('mode') or '-'} | "
        f"{trace.get('duration_ms', 0):.2f}ms{error} | {trace['trace_id'][:8]}"
    )


def _render_generation_run(run: Dict[str, Any]) -> None:
    verdict = run.get("llm_verdict") or "unknown"
    icon = GENERATION_VERDICT_ICON.get(verdict, "")
    st.markdown(f"### {icon} Verdict: `{verdict}`")
    if run.get("llm_reason"):
        st.markdown("**Reason**")
        st.write(run["llm_reason"])
    if run.get("llm_risks"):
        st.markdown("**Risks**")
        st.write(run["llm_risks"])
    if run.get("llm_recommendation"):
        st.markdown("**Recommendation**")
        st.write(run["llm_recommendation"])

    left, right = st.columns(2)
    with left:
        st.markdown("**Current output**")
        st.code(run.get("current_output") or "")
    with right:
        st.markdown("**Candidate output**")
        if run.get("execution_error"):
            st.error(run["execution_error"])
        else:
            st.code(run.get("candidate_output") or "")

    st.markdown("**Diff**")
    if run.get("execution_error"):
        st.warning("candidate execution failed; no output diff available")
    else:
        diff = _unified_diff(run.get("current_output"), run.get("candidate_output"))
        st.code(diff or "no diff", language="diff")

    st.markdown("**Generated code**")
    st.code(run.get("generated_code") or "", language="python")
    st.download_button(
        "Download candidate.py",
        data=run.get("generated_code") or "",
        file_name=f"candidate_run_{run['id']}.py",
        mime="text/x-python",
    )
    if run.get("generation_notes"):
        st.caption(run["generation_notes"])


def render_generate_evaluate_tab(system: Dict[str, Any]) -> None:
    st.subheader("Generate & Evaluate")
    st.caption(
        "転送済みtraceの入力パラメーターを使って候補コードを生成し、"
        "同じ入力で実行してLLM評価します。生成コードは自動適用されません。"
    )

    components: List[Dict[str, Any]] = api_get("/components") or []
    if not components:
        st.info("まだcomponentがありません。まずConnect SDKのサンプルを実行してください。")
        return

    component_ids = [component["component_id"] for component in components]
    selected_component = st.selectbox(
        "Component",
        component_ids,
        key=f"gen-component-{system['id']}",
    )
    traces = api_get(f"/components/{selected_component}/traces?limit=50") or []
    if not traces:
        st.info("このcomponentにはtraceがありません。")
        return

    trace_ids = [trace["trace_id"] for trace in traces]
    selected_trace_id = st.selectbox(
        "Trace",
        trace_ids,
        format_func=lambda trace_id: _format_trace_label(
            next(trace for trace in traces if trace["trace_id"] == trace_id)
        ),
        key=f"gen-trace-{system['id']}-{selected_component}",
    )
    selected_trace = next(trace for trace in traces if trace["trace_id"] == selected_trace_id)

    left, right = st.columns(2)
    with left:
        st.markdown("**Input parameters**")
        st.code(json.dumps(selected_trace.get("input"), ensure_ascii=False, indent=2))
    with right:
        st.markdown("**Current output**")
        st.code(selected_trace.get("output") or "")

    objective = st.text_area(
        "Objective",
        placeholder="例: 出力を短くしつつ、重要な情報を落とさない",
        key=f"gen-objective-{system['id']}-{selected_component}-{selected_trace_id}",
    )
    if st.button(
        "Generate candidate and evaluate",
        key=f"gen-run-{system['id']}-{selected_component}-{selected_trace_id}",
    ):
        if not objective.strip():
            st.error("Objective is required")
        else:
            with st.spinner("Generating, executing, and evaluating candidate..."):
                run = api_post(
                    "/generation-runs",
                    {
                        "component_id": selected_component,
                        "trace_id": selected_trace_id,
                        "objective": objective.strip(),
                    },
                )
            if run:
                st.session_state["selected_generation_run_id"] = run["id"]
                st.success(f"generation run #{run['id']} を作成しました")
                st.rerun()

    runs = api_get(
        f"/generation-runs?component_id={selected_component}&trace_id={selected_trace_id}&limit=20"
    ) or []
    if not runs:
        st.caption("まだgeneration runはありません。")
        return

    st.divider()
    st.markdown("### Generation runs")
    run_ids = [run["id"] for run in runs]
    selected_run_id = st.selectbox(
        "Run",
        run_ids,
        index=run_ids.index(st.session_state.get("selected_generation_run_id"))
        if st.session_state.get("selected_generation_run_id") in run_ids
        else 0,
        format_func=lambda run_id: next(
            (
                f"#{run['id']} {fmt_ts(run['created_at'])} "
                f"{GENERATION_VERDICT_ICON.get(run.get('llm_verdict'), '')} "
                f"{run.get('llm_verdict')}"
                for run in runs
                if run["id"] == run_id
            ),
            str(run_id),
        ),
        key=f"gen-run-select-{system['id']}-{selected_component}-{selected_trace_id}",
    )
    run = next(run for run in runs if run["id"] == selected_run_id)
    _render_generation_run(run)


def render_overview_tab(system: Dict[str, Any]) -> None:
    components: List[Dict[str, Any]] = api_get("/components") or []
    mode_counts = {mode: 0 for mode in MODES}
    for component in components:
        mode = component.get("mode")
        if mode in mode_counts:
            mode_counts[mode] += 1

    cols = st.columns(4)
    cols[0].metric("Components", system.get("component_count", 0))
    cols[1].metric("Traces", system.get("trace_count", 0))
    cols[2].metric("Last seen", fmt_ts(system.get("last_seen")))
    cols[3].metric(
        "Active modes",
        f"trace {mode_counts['trace']} / shadow {mode_counts['shadow']}",
    )

    st.subheader("System")
    st.write(system.get("description") or "説明はまだ設定されていません。")
    if system.get("last_seen") is None:
        st.info(
            "まだプローブからデータを受信していません。Connect SDKでAPI tokenを発行し、"
            "クライアント側の`PROBE_API_KEY`へ設定してください。"
        )

    st.subheader("Components")
    if not components:
        st.write("component なし")
        return
    for component in components:
        st.write(
            f"**{component['component_id']}** · `{component['mode']}` · "
            f"{component.get('trace_count', 0)} traces · "
            f"last seen {fmt_ts(component.get('last_seen'))}"
        )


def render_system_settings(system: Dict[str, Any]) -> None:
    st.subheader("System Settings")
    with st.form(f"system-settings-{system['id']}"):
        name = st.text_input("System name", value=system.get("name", ""))
        environment = st.text_input(
            "Environment", value=system.get("environment", "")
        )
        description = st.text_area(
            "Description", value=system.get("description", "")
        )
        if st.form_submit_button("Save system settings"):
            if not name.strip():
                st.error("System name は必須です")
            elif api_put(
                f"/systems/{system['id']}",
                {
                    "name": name.strip(),
                    "environment": environment.strip(),
                    "description": description,
                },
            ) is not None:
                st.success("System設定を保存しました")
                st.rerun()

    st.divider()
    st.subheader("System Profile")
    sp = api_get("/system-profile") or {}
    with st.form(f"system-profile-form-{system['id']}"):
        sp_name = st.text_input("name", value=sp.get("name", ""))
        sp_purpose = st.text_area("purpose", value=sp.get("purpose", ""))
        sp_users = st.text_area(
            "target_users (1行1項目)", value="\n".join(sp.get("target_users", []))
        )
        sp_value = st.text_area(
            "stakeholder_value", value=sp.get("stakeholder_value", "")
        )
        sp_constraints = st.text_area(
            "constraints (1行1項目)", value="\n".join(sp.get("constraints", []))
        )
        sp_success = st.text_area(
            "success_criteria (1行1項目)",
            value="\n".join(sp.get("success_criteria", [])),
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
            st.success("System Profileを保存しました")
            st.rerun()


def render_system_selector(
    systems: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    st.sidebar.markdown("### System")
    if not systems:
        st.sidebar.info("Systemを作成してください")
        return None

    ids = [system["id"] for system in systems]
    pending_id = st.session_state.pop("pending_system_id", None)
    selected_id = pending_id or st.session_state.get("selected_system_id")
    if selected_id not in ids:
        selected_id = ids[0]
    if pending_id is not None or st.session_state.get("system_selector") not in ids:
        st.session_state["system_selector"] = selected_id
    selected_id = st.sidebar.selectbox(
        "Current system",
        ids,
        index=ids.index(selected_id),
        key="system_selector",
        format_func=lambda system_id: next(
            (
                f"{s['name']} / {s.get('environment') or 'default'}"
                for s in systems
                if s["id"] == system_id
            ),
            str(system_id),
        ),
    )
    if selected_id != st.session_state.get("selected_system_id"):
        st.session_state.pop("issued_token", None)
    st.session_state["selected_system_id"] = selected_id
    return next(system for system in systems if system["id"] == selected_id)


def render_create_system() -> None:
    with st.sidebar.expander("新しいSystemを登録"):
        with st.form("create-system-form"):
            name = st.text_input("System name")
            environment = st.text_input("Environment", placeholder="production")
            description = st.text_area("Description")
            if st.form_submit_button("Create system"):
                if not name.strip():
                    st.error("System name は必須です")
                else:
                    created = api_post(
                        "/systems",
                        {
                            "name": name.strip(),
                            "environment": environment.strip(),
                            "description": description,
                        },
                    )
                    if created:
                        st.session_state["pending_system_id"] = created["id"]
                        st.rerun()


# --- Page layout -------------------------------------------------------------

st.set_page_config(page_title="probe-agent", layout="wide")
cookie_controller = CookieController(key="probe-dashboard-cookies")
RemoveEmptyElementContainer()
_apply_pending_cookie_change()
_restore_session_from_cookie()
st.title("probe-agent dashboard")
st.caption(f"Control Server: {SERVER_URL}")

me = _fetch_me()
render_account_sidebar(me)
# Re-resolve after the sidebar: it may have dropped an invalid session token.
if _session_token() is None and me is None:
    me = _fetch_me()
user = (me or {}).get("user")

if _session_token() is None or user is None:
    st.info("コンポーネントを表示するにはログインしてください。")
    st.stop()

is_admin = bool(user) and user.get("role") == "admin"

systems: List[Dict[str, Any]] = api_get("/systems") or []
selected_system = render_system_selector(systems)
render_create_system()

if selected_system is None:
    st.info("左側の「新しいSystemを登録」から最初のSystemを作成してください。")
    st.stop()

environment = selected_system.get("environment") or "default"
st.header(f"{selected_system['name']} / {environment}")
st.caption(
    f"System ID: {selected_system['id']} · "
    f"Last seen: {fmt_ts(selected_system.get('last_seen'))}"
)

tab_labels = ["Overview", "Connect SDK", "Generate & Evaluate", "Components", "Settings"]
if is_admin:
    tab_labels.append("User Management")
tabs = st.tabs(tab_labels)

with tabs[0]:
    render_overview_tab(selected_system)
with tabs[1]:
    render_connect_sdk_tab(selected_system)
with tabs[2]:
    render_generate_evaluate_tab(selected_system)
with tabs[3]:
    render_components_tab(selected_system)
with tabs[4]:
    render_system_settings(selected_system)
if is_admin:
    with tabs[tab_labels.index("User Management")]:
        render_admin_tab(user)

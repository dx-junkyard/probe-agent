"""Issue draft generation and persistence (Issue #107).

probe-agent is the source of truth for issue drafts. A draft is generated from
a System Understanding gap: its title, evidence, suggested next actions, and the
pinned snapshot / commit are rendered into a Markdown body. The user copies that
Markdown into whatever tracker they use (GitHub / GitLab / Jira / ...), then
registers the resulting external issue URL back here so it can be shown next to
the originating gap.

Design notes:
- Rendering a gap into Markdown is a deterministic, structural template of an
  already-derived gap (Principle 6). It makes no open-ended decision, so it does
  not call a reasoning model. The upstream gap detection (docs-code reconcile,
  claim scan) is where reasoning happens.
- probe-agent never writes to the target repository's tracked branches
  (Principle 5). Registering `external_url` accepts any user-supplied
  http(s) string for any tracker. When GitHub is configured (Issue #158,
  see `github_integration.py`), the dashboard can also ask probe-agent to
  create the GitHub issue itself and it calls `update_draft` with the
  resulting URL the same way a manually-registered URL would be stored.

probe-agent:
  role: Issue draft generation and lifecycle store
  capability: repository-understanding
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: transformation
  state_effects: [database-read, database-write]
  probe_value: Verify draft Markdown embeds snapshot id / commit sha / evidence
    and that status/external_url transitions stay within the finite vocabulary.
"""

import hashlib
import time
from typing import Any, Dict, List, Optional, Tuple

from .db import get_conn

# Finite, explicitly enumerated vocabularies (Principle 6).
SOURCE_TYPE_VALUES = {
    "system_understanding_gap",
    "interview",
    "probe_proposal",
}

STATUS_VALUES = {
    "draft",
    "copied",
    "external_created",
    "closed",
    "rejected",
}


def gap_source_key(gap: Dict[str, Any]) -> str:
    """Deterministic identity for a gap so drafts can be matched back to it.

    Gaps are recomputed on every read (they have no stable DB id), so the key is
    derived deterministically from the gap's identity. gap_type + node_name alone
    collide when a system has several same-typed/same-named gaps, which would mix
    up their drafts (and the wrong external URL would surface on the wrong gap).
    To disambiguate, the key also folds in the capability and the docs / code /
    entrypoint evidence that locate the gap, hashed into a short stable suffix.
    """
    gap_type = (gap.get("gap_type") or "unknown").strip()
    node_name = (gap.get("node_name") or "unknown").strip()

    parts: List[str] = [
        # The strongest identifier: a stable per-gap source id (graph node id or
        # entrypoint identity). Evidence/capability can be empty (e.g.
        # missing_evidence gaps), so without this two same-named gaps collide.
        f"source_id:{gap.get('source_id') or ''}",
        f"capability:{gap.get('capability_key') or ''}",
    ]
    for dr in gap.get("doc_refs") or []:
        parts.append(
            f"doc:{dr.get('path', '')}:{dr.get('start_line', '')}:{dr.get('end_line', '')}"
        )
    for sr in gap.get("symbol_refs") or []:
        parts.append(f"sym:{sr.get('path', '')}:{sr.get('qualified_name', '')}")
    for er in gap.get("entrypoint_refs") or []:
        parts.append(
            f"ep:{er.get('entrypoint_type', '')}:{er.get('entrypoint_ref', '')}"
        )
    # Sort so the key is independent of evidence ordering, then hash.
    digest = hashlib.sha1("|".join(sorted(parts)).encode("utf-8")).hexdigest()[:12]
    return f"system_understanding_gap:{gap_type}:{node_name}:{digest}"


def _fmt_doc_ref(dr: Dict[str, Any]) -> str:
    path = dr.get("path") or ""
    start = dr.get("start_line")
    end = dr.get("end_line")
    if start is not None and end is not None:
        return f"`{path}:{start}-{end}`"
    if start is not None:
        return f"`{path}:{start}`"
    return f"`{path}`"


def _fmt_symbol_ref(sr: Dict[str, Any]) -> str:
    path = sr.get("path")
    qname = sr.get("qualified_name")
    if path and qname:
        return f"`{path}` — `{qname}`"
    return f"`{qname or path or 'unknown'}`"


def _fmt_entrypoint_ref(er: Dict[str, Any]) -> str:
    etype = er.get("entrypoint_type")
    eref = er.get("entrypoint_ref")
    label = ": ".join([p for p in (etype, eref) if p]) or "unknown"
    return f"`{label}`"


def render_gap_markdown(
    gap: Dict[str, Any],
    snapshot_id: Optional[int],
    commit_sha: Optional[str],
) -> Tuple[str, str]:
    """Render a gap into an (title, body_markdown) pair.

    Deterministic template. The body always embeds the gap classification, the
    docs/code/entrypoint evidence, the suggested next actions, and the pinned
    snapshot id / commit sha so the observation point is reproducible.
    """
    node_name = gap.get("node_name") or "unknown"
    title = gap.get("title") or f"System Understanding gap: {node_name}"

    lines: List[str] = []
    gap_type = gap.get("gap_type") or "unknown"
    severity = gap.get("severity") or "info"
    lines.append(
        f"Generated by probe-agent from a System Understanding gap "
        f"(`{gap_type}`, severity `{severity}`)."
    )
    lines.append("")

    lines.append("## Gap")
    lines.append("")
    lines.append(f"- **Type:** `{gap_type}`")
    lines.append(f"- **Severity:** `{severity}`")
    lines.append(f"- **Node:** `{node_name}`")
    if gap.get("capability_key"):
        lines.append(f"- **Capability:** `{gap['capability_key']}`")
    if gap.get("notes"):
        lines.append("")
        lines.append(gap["notes"])
    lines.append("")

    doc_refs = gap.get("doc_refs") or []
    lines.append("## Documentation evidence")
    lines.append("")
    if doc_refs:
        for dr in doc_refs:
            lines.append(f"- {_fmt_doc_ref(dr)}")
    else:
        lines.append("- _None_")
    lines.append("")

    symbol_refs = gap.get("symbol_refs") or []
    entrypoint_refs = gap.get("entrypoint_refs") or []
    lines.append("## Code evidence")
    lines.append("")
    if symbol_refs or entrypoint_refs:
        for sr in symbol_refs:
            lines.append(f"- {_fmt_symbol_ref(sr)}")
        for er in entrypoint_refs:
            lines.append(f"- Entrypoint: {_fmt_entrypoint_ref(er)}")
    else:
        lines.append("- _None_")
    lines.append("")

    next_actions = gap.get("next_actions") or []
    if next_actions:
        lines.append("## Suggested next actions")
        lines.append("")
        for na in next_actions:
            action = na.get("action") or ""
            link = na.get("link")
            if link:
                lines.append(f"- {action} ({link})")
            else:
                lines.append(f"- {action}")
        lines.append("")

    lines.append("## Observation snapshot")
    lines.append("")
    lines.append(f"- **Snapshot id:** `{snapshot_id if snapshot_id is not None else 'unknown'}`")
    lines.append(f"- **Commit sha:** `{commit_sha or 'unknown'}`")
    lines.append("")

    body = "\n".join(lines).rstrip() + "\n"
    return title, body


def _row_to_dict(row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "system_id": row["system_id"],
        "snapshot_id": row["snapshot_id"],
        "commit_sha": row["commit_sha"],
        "source_type": row["source_type"],
        "source_key": row["source_key"],
        "gap_type": row["gap_type"],
        "severity": row["severity"],
        "node_name": row["node_name"],
        "title": row["title"],
        "body_markdown": row["body_markdown"],
        "status": row["status"],
        "external_url": row["external_url"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_gap_draft(
    system_id: int,
    gap: Dict[str, Any],
    snapshot_id: Optional[int],
    commit_sha: Optional[str],
    source_type: str = "system_understanding_gap",
) -> Dict[str, Any]:
    """Generate and persist an issue draft from a gap."""
    source_key = gap_source_key(gap)
    title, body = render_gap_markdown(gap, snapshot_id, commit_sha)
    now = time.time()
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO issue_drafts
                (system_id, snapshot_id, commit_sha, source_type, source_key,
                 gap_type, severity, node_name, title, body_markdown, status,
                 external_url, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'draft', NULL, ?, ?)
            """,
            (
                system_id,
                snapshot_id,
                commit_sha,
                source_type,
                source_key,
                gap.get("gap_type"),
                gap.get("severity"),
                gap.get("node_name"),
                title,
                body,
                now,
                now,
            ),
        )
        row = conn.execute(
            "SELECT * FROM issue_drafts WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
    return _row_to_dict(row)


def list_drafts(system_id: int) -> List[Dict[str, Any]]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM issue_drafts WHERE system_id = ? ORDER BY id DESC",
            (system_id,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_draft(system_id: int, draft_id: int) -> Optional[Dict[str, Any]]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM issue_drafts WHERE system_id = ? AND id = ?",
            (system_id, draft_id),
        ).fetchone()
    return _row_to_dict(row) if row else None


def drafts_by_source_key(system_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Map source_key -> drafts for a system, for attaching to live gaps."""
    result: Dict[str, List[Dict[str, Any]]] = {}
    for d in list_drafts(system_id):
        key = d.get("source_key")
        if key:
            result.setdefault(key, []).append(d)
    return result


class DraftValidationError(ValueError):
    """Raised for out-of-contract update payloads."""


def _validate_external_url(url: str) -> None:
    if not (url.startswith("http://") or url.startswith("https://")):
        raise DraftValidationError(
            "external_url must start with http:// or https://"
        )


def update_draft(
    system_id: int,
    draft_id: int,
    *,
    title: Optional[str] = None,
    body_markdown: Optional[str] = None,
    status: Optional[str] = None,
    external_url: Optional[str] = None,
    external_url_set: bool = False,
) -> Optional[Dict[str, Any]]:
    """Update editable fields of a draft.

    `external_url_set` distinguishes "leave external_url alone" (default) from
    "explicitly set/clear it" so that a caller can register a URL, clear it (with
    an empty string), or update other fields without touching it.
    """
    if status is not None and status not in STATUS_VALUES:
        raise DraftValidationError(f"invalid status: {status}")

    normalized_url: Optional[str] = None
    if external_url_set and external_url:
        stripped = external_url.strip()
        if stripped:
            _validate_external_url(stripped)
            normalized_url = stripped

    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM issue_drafts WHERE system_id = ? AND id = ?",
            (system_id, draft_id),
        ).fetchone()
        if row is None:
            return None

        sets: List[str] = []
        params: List[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if body_markdown is not None:
            sets.append("body_markdown = ?")
            params.append(body_markdown)
        if status is not None:
            sets.append("status = ?")
            params.append(status)
        if external_url_set:
            sets.append("external_url = ?")
            params.append(normalized_url)

        sets.append("updated_at = ?")
        params.append(time.time())
        params.extend([system_id, draft_id])

        conn.execute(
            f"UPDATE issue_drafts SET {', '.join(sets)} WHERE system_id = ? AND id = ?",
            params,
        )
        updated = conn.execute(
            "SELECT * FROM issue_drafts WHERE system_id = ? AND id = ?",
            (system_id, draft_id),
        ).fetchone()
    return _row_to_dict(updated)

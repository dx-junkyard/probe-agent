"""UiDraftContext validation and LLM-payload preparation (Issue #445, Epic
#443 Phase 2).

`docs/ai-discussion-adapter.md` §2 is the canonical contract. This module is
the SINGLE place that decides whether an incoming `UiDraftContextIn`
(`app/models.py`) may reach the LLM context pack, and in what shape. It does
NOT live in `routes/assistant.py` (per the phase instructions) because the
checks below need the `discussion_adapters` registry and the resolved thread
row, which is domain logic, not routing.

Three things this module explicitly does NOT do, on purpose:

- It never persists a draft VALUE. `ResolvedUiDraft` carries only the
  redacted-for-LLM payload (never written to any table) and the three §2.7
  audit facts (`state` / `form_id` / `digest`) a caller writes onto the USER
  turn.
- It never re-derives `target_state` (`current`/`stale`/...) -- that stays
  `assistant_discussion.evaluate_target_state`'s job. A draft mismatch and a
  stale target are different failures with different remediations.
- It never truncates an over-bound draft. §2.3: a silently shortened draft is
  not the draft the developer is looking at, so bound violations are a hard
  422 (`app/models.py`'s `UiDraftContextIn.validate_ui_draft_bounds`), not a
  clamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import discussion_adapters, trace_redaction
from .models import UiDraftContextIn, UiDraftState


class UiDraftValidationError(ValueError):
    """Raised for every §2.3/§2.7 fail-closed rejection below. `code` is one
    of the finite §1.7/§2.3 422 codes; `str(exc)` always starts with
    `f"{code}: "` so `assert code in response.text` keeps working the same
    way it does for every other DiscussionError in this Epic."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass
class ResolvedUiDraft:
    """What `validate_and_prepare_ui_draft` hands back to the route.

    `payload` / `sources` are `None` / `[]` whenever no draft applies to this
    turn (not provided, or the target's adapter has no `ui_draft_forms`) --
    the caller passes them straight through to
    `assistant.build_context_pack`/`answer_question` unchanged, so a request
    that never sends `ui_draft` behaves byte-for-byte as it did before this
    module existed (the Epic's "additive only" contract).
    """

    state: UiDraftState
    payload: Optional[Dict[str, Any]] = None
    sources: List[Dict[str, str]] = field(default_factory=list)
    form_id: str = ""
    digest: str = ""


def _adapter_supports_ui_draft(target_kind: str) -> bool:
    adapter = discussion_adapters.DISCUSSION_ADAPTERS.get(target_kind)
    return bool(adapter and adapter.ui_draft_forms)


def derive_ui_draft_state(
    ui_draft: Optional[UiDraftContextIn], *, adapter_supports_ui_draft: bool
) -> UiDraftState:
    """§2.6's finite first-match table.

    All five values are reachable. `unreadable` in particular is NOT folded
    into `not_provided`: "a form is open for this target but the client could
    not read it" and "no form was open" lead the developer to different next
    steps, and an assistant that answers the second when the first is true is
    describing a screen the developer is not looking at. The client says
    which one it is via `UiDraftContextIn.readable`; it is never inferred
    from an empty `fields` list, because that is already what
    `no_unsaved_changes` looks like.
    """
    if ui_draft is None:
        # `unsupported` outranks `not_provided` here: when the target's
        # adapter has no form at all, saying "not provided" would suggest a
        # draft could have been sent and simply wasn't -- the Dashboard
        # acceptance criterion (§2.6 / "adapter 未対応画面では canonical-only
        # であることを明示する") needs this distinction even when the client
        # never attempted to send one.
        return "unsupported" if not adapter_supports_ui_draft else "not_provided"
    if not ui_draft.readable:
        return "unreadable"
    if not any(f.dirty for f in ui_draft.fields):
        return "no_unsaved_changes"
    return "applied"


def validate_and_prepare_ui_draft(
    ui_draft: Optional[UiDraftContextIn],
    thread_row: Optional[Dict[str, Any]],
) -> ResolvedUiDraft:
    """§2.3's fail-closed gate, run BEFORE any LLM call.

    `thread_row` is the already-resolved discussion thread dict (or `None`
    for a thread-less/legacy ask) -- this function never opens its own
    connection, matching "never hold get_conn() across an external call":
    the caller already read the thread earlier in the same request.
    """
    if ui_draft is None:
        # `AssistantAskRequest.validate_assistant_context_bounds` already
        # refuses `ui_draft` set with no `thread_id` at the Pydantic layer,
        # so reaching here with `ui_draft is None` and `thread_row is None`
        # is the ordinary thread-less/legacy request -- always `not_provided`
        # (there is no adapter to check "unsupported" against).
        if thread_row is None:
            return ResolvedUiDraft(state="not_provided")
        supports = _adapter_supports_ui_draft(thread_row["target_kind"])
        return ResolvedUiDraft(state=derive_ui_draft_state(None, adapter_supports_ui_draft=supports))

    # `ui_draft is not None` from here on. `thread_row is None` cannot happen
    # in practice (the Pydantic validator above already refuses it), but this
    # module must not TRUST that another layer enforced it -- defense in
    # depth for any future direct caller.
    if thread_row is None:
        raise UiDraftValidationError(
            "ui_draft_requires_thread", "ui_draft requires an active discussion thread"
        )

    adapter = discussion_adapters.DISCUSSION_ADAPTERS.get(thread_row["target_kind"])
    if adapter is None or not adapter.ui_draft_forms:
        raise UiDraftValidationError(
            "ui_draft_unsupported",
            f"target_kind={thread_row['target_kind']!r} has no registered ui_draft_forms",
        )

    if ui_draft.target_kind != thread_row["target_kind"] or ui_draft.target_ref != thread_row["target_ref"]:
        raise UiDraftValidationError(
            "ui_draft_target_mismatch",
            "ui_draft target_kind/target_ref must equal the thread's own target",
        )

    form_spec = next((f for f in adapter.ui_draft_forms if f.form_id == ui_draft.form_id), None)
    if form_spec is None:
        raise UiDraftValidationError(
            "ui_draft_form_unregistered",
            f"form_id={ui_draft.form_id!r} is not registered for target_kind={ui_draft.target_kind!r}",
        )

    allowed_fields = set(form_spec.fields)
    unknown = sorted({f.field_name for f in ui_draft.fields if f.field_name not in allowed_fields})
    if unknown:
        # §2.3: reject the WHOLE request -- never silently drop the
        # offending field, which would let the client believe it was sent.
        raise UiDraftValidationError(
            "ui_draft_field_unregistered",
            f"unregistered field_name(s) for form_id={ui_draft.form_id!r}: {unknown}",
        )

    payload = _build_redacted_payload(ui_draft)
    state = derive_ui_draft_state(ui_draft, adapter_supports_ui_draft=True)
    source_id = f"ui_draft:{ui_draft.form_id}"
    return ResolvedUiDraft(
        state=state,
        payload=payload,
        sources=[{"id": source_id, "title": f"未保存の下書き: {ui_draft.form_id}"}],
        form_id=ui_draft.form_id,
        digest=ui_draft.local_revision_token,
    )


def _build_redacted_payload(ui_draft: UiDraftContextIn) -> Dict[str, Any]:
    """Principle 9, both layers, over every free-text value in the draft
    before it can reach the LLM prompt: `trace_redaction.redact_value`
    already combines the key-name denylist (`probe_agent.redaction.
    SENSITIVE_KEYS`) and the value-shape credential signatures
    (`probe_agent.secret_patterns`) in one pass -- reused verbatim rather
    than reimplemented, per this phase's instructions. Field names come from
    the adapter's own allowlist (never user-supplied), so the key-name layer
    is defense in depth here; the value-shape layer is the one that matters
    for a developer who pasted a credential into a draft field.
    """
    fields_dict = {f.field_name: f.value for f in ui_draft.fields}
    redacted_fields, _entries = trace_redaction.redact_value(fields_dict, field_name="ui_draft_fields")

    def _redact_meta(value: str) -> str:
        redacted, _entries = trace_redaction.redact_text(value, field_name="ui_draft_meta")
        return redacted if redacted is not None else value

    return {
        "form_id": ui_draft.form_id,
        "fields": redacted_fields,
        "selected_item_ref": _redact_meta(ui_draft.selected_item_ref),
        "active_tab": _redact_meta(ui_draft.active_tab),
        "comparison_target": _redact_meta(ui_draft.comparison_target),
        "captured_at": ui_draft.captured_at,
    }


def compute_ui_draft_changed(
    conn: Any, *, thread_id: int, form_id: str, local_revision_token: str
) -> bool:
    """§2.6: `true` when `local_revision_token` differs from the most recent
    PRIOR USER turn's stored `ui_draft_digest` for the SAME `form_id` on this
    thread. Deliberately derived at read time on every call rather than
    stored (the same discipline #337/#338/#349 apply to every other derived
    lifecycle value in this codebase) -- a `changed` column could drift from
    the turns it is supposed to describe.

    No prior draft turn to compare against (first draft ever sent on this
    thread/form) is `False`, not `True`: there is nothing to have changed
    FROM.
    """
    if not form_id or not local_revision_token:
        return False
    row = conn.execute(
        "SELECT ui_draft_digest FROM assistant_discussion_turn "
        "WHERE thread_id = ? AND role = 'user' AND ui_draft_form_id = ? "
        "ORDER BY turn_number DESC LIMIT 1",
        (thread_id, form_id),
    ).fetchone()
    if row is None or row["ui_draft_digest"] is None:
        return False
    return row["ui_draft_digest"] != local_revision_token

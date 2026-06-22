"""Deterministic proposal-to-draft handoff (Issue #39).

Converts an *accepted* Decision Workspace proposal into a small, tracked
prefill record for an existing screen (Probe Planner or Experiments). This
module never generates a probe plan, creates a probe point, runs an
experiment, or applies a patch -- it only maps and re-validates the
already-accepted proposal body into the shape the destination screen's
existing create flow expects, and flags which fields the user still has to
supply themselves.

This is explicit, finite mapping logic (CLAUDE.md principle 6): the set of
proposal types is fixed (`PROPOSAL_BODY_MODELS`), and the destination screen
and required fields for each type are hard-coded, not inferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from pydantic import ValidationError

from .workspace_agent import (
    PROPOSAL_BODY_MODELS,
    ExperimentDraftProposalBody,
    ProbePlanDraftProposalBody,
)

TARGET_SCREENS: Dict[str, str] = {
    "probe_plan_draft": "probe_planner",
    "experiment_draft": "experiments",
}


@dataclass
class ProposalDraftResult:
    target_screen: str
    payload: Dict[str, Any]
    missing_fields: List[str]


class UnsupportedProposalTypeError(ValueError):
    pass


def _missing_fields_for_probe_plan(conn, system_id: int, feature_id: str) -> List[str]:
    missing: List[str] = []

    snapshot_row = conn.execute(
        """SELECT id FROM repository_snapshots
           WHERE system_id = ? AND status = 'ready'
           ORDER BY id DESC LIMIT 1""",
        (system_id,),
    ).fetchone()
    if snapshot_row is None:
        missing.append("ready_repository_snapshot")
        return missing
    snapshot_id = snapshot_row["id"]

    fd_row = conn.execute(
        """SELECT fd.id FROM feature_drafts fd
           JOIN intelligence_runs ir ON fd.intelligence_run_id = ir.id
           WHERE fd.system_id = ? AND fd.feature_id = ?
             AND fd.snapshot_id = ? AND ir.status = 'completed'
           ORDER BY fd.id DESC LIMIT 1""",
        (system_id, feature_id, snapshot_id),
    ).fetchone()
    if fd_row is None:
        missing.append("completed_feature_draft")

    link_row = conn.execute(
        """SELECT fcl.id FROM feature_code_links fcl
           WHERE fcl.system_id = ? AND fcl.feature_id = ?
             AND fcl.snapshot_id = ? AND fcl.review_status = 'accepted'
           LIMIT 1""",
        (system_id, feature_id, snapshot_id),
    ).fetchone()
    if link_row is None:
        missing.append("accepted_code_links")

    return missing


def build_proposal_draft(
    conn,
    system_id: int,
    proposal_type: str,
    body: Dict[str, Any],
) -> ProposalDraftResult:
    """Re-validate `body` against the proposal type's schema and build the
    deterministic prefill payload plus the list of fields the destination
    screen still requires from the user before it can run its existing
    create/generate action."""
    body_model = PROPOSAL_BODY_MODELS.get(proposal_type)
    target_screen = TARGET_SCREENS.get(proposal_type)
    if body_model is None or target_screen is None:
        raise UnsupportedProposalTypeError(f"unsupported proposal type: {proposal_type}")

    try:
        validated = body_model.model_validate(body)
    except ValidationError as exc:
        raise UnsupportedProposalTypeError(
            f"proposal body failed re-validation: {exc}"
        ) from exc

    if isinstance(validated, ProbePlanDraftProposalBody):
        payload = {
            "feature_id": validated.feature_id,
            "objective": validated.objective,
            "target_components": validated.target_components,
        }
        missing_fields = _missing_fields_for_probe_plan(
            conn, system_id, validated.feature_id
        )
    elif isinstance(validated, ExperimentDraftProposalBody):
        payload = {
            "feature_id": validated.feature_id,
            "objective": validated.objective,
            "variant_summaries": validated.variant_summaries,
        }
        # An experiment_draft proposal never carries a snapshot or patch
        # text -- those are always user-supplied in the Experiments screen.
        missing_fields = ["snapshot_id", "patch_text"]
    else:  # pragma: no cover - defensive, unreachable given TARGET_SCREENS
        raise UnsupportedProposalTypeError(f"unsupported proposal type: {proposal_type}")

    return ProposalDraftResult(
        target_screen=target_screen, payload=payload, missing_fields=missing_fields
    )

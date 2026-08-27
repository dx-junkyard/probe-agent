"""Authoritative finite vocabulary for ``intelligence_runs.run_type``.

The database guards installed by :func:`install_intelligence_run_type_guards`
are generated from :class:`IntelligenceRunType`.  Adding a new reasoning run
therefore requires an intentional addition to this enum before the run can be
persisted.
"""

from enum import Enum
import sqlite3
from typing import FrozenSet


class IntelligenceRunType(str, Enum):
    ALIGNMENT_BUILD = "alignment_build"
    ANALYZER_PROPOSAL = "analyzer_proposal"
    API_SCAN = "api_scan"
    CANDIDATE_STUDIO_PROPOSAL = "candidate_studio_proposal"
    CAPABILITY_HIERARCHY = "capability_hierarchy"
    CELL_IMPROVEMENT_DRAFT = "cell_improvement_draft"
    CELL_QUALITY_AUDIT = "cell_quality_audit"
    CELL_TRIAGE = "cell_triage"
    ENTRYPOINT_INDEX = "entrypoint_index"
    EXPLANATION_REFRESH = "explanation_refresh"
    FEATURE_CODE_MAPPING = "feature_code_mapping"
    # Epic #412 Phase 3 (#415): a reasoning run that DRAFTS a Flow-scoped
    # experiment proposal. It is reachable only through
    # `execution_mode.build_experiment_llm_adapter` (so only in the `propose`
    # / `shadow` execution modes), it drafts only, and the `proposed` event
    # that puts a proposal into the approval queue is a separate
    # `decision_method: manual` record written by a human (§7.7).
    FLOW_EXPERIMENT_DRAFT = "flow_experiment_draft"
    INQUIRY_ANSWER = "inquiry_answer"
    INTENT_PROPOSAL = "intent_proposal"
    INTERVIEW_DIALOGUE = "interview_dialogue"
    INTERVIEW_EVIDENCE_SELECTION = "interview_evidence_selection"
    INTERVIEW_PROPOSAL = "interview_proposal"
    INVESTIGATION = "investigation"
    JOINT_INVESTIGATION = "joint_investigation"
    JOINT_TRANSLATION = "joint_translation"
    NL_CHANGE_SET = "nl_change_set"
    # Epic #394 Phase 2 (#397): a reasoning run that proposes ways to cut a
    # scope into Evolution Nodes. It proposes only -- adopting a cut is a
    # separate `decision_method: manual` record (node_decomposition_candidate).
    NODE_DECOMPOSITION = "node_decomposition"
    PATTERN_INVESTIGATE = "pattern_investigate"
    PATTERN_RECONCILE = "pattern_reconcile"
    PROBE_PLAN = "probe_plan"
    PROBE_PLAN_FROM_FLOW = "probe_plan_from_flow"
    PROBE_PLAN_FROM_PATTERN = "probe_plan_from_pattern"
    QUESTION_ROUTE = "question_route"
    REPLAY_REGRESSION_SCAFFOLD = "replay_regression_scaffold"
    REPLAY_VARIANT_DRAFT = "replay_variant_draft"
    REPOSITORY_DRAFTS = "repository_drafts"
    RUNTIME_MATCH = "runtime_match"
    RUNTIME_REALITY_CHECK = "runtime_reality_check"
    SYMBOL_INDEX = "symbol_index"
    UNDERSTANDING_REVIEW = "understanding_review"


INTELLIGENCE_RUN_TYPES: FrozenSet[str] = frozenset(
    run_type.value for run_type in IntelligenceRunType
)

_INSERT_GUARD = "guard_intelligence_runs_run_type_insert"
_UPDATE_GUARD = "guard_intelligence_runs_run_type_update"
_INVALID_RUN_TYPE_MESSAGE = "invalid intelligence_runs.run_type"


def install_intelligence_run_type_guards(conn: sqlite3.Connection) -> None:
    """Install DB-level guards derived from the authoritative vocabulary.

    SQLite cannot add a ``CHECK`` constraint to an existing table without
    rebuilding it.  ``BEFORE`` triggers give fresh and upgraded databases the
    same enforcement without rewriting ``intelligence_runs`` or its many
    foreign-key dependants.  Recreating them on startup means extending the
    enum automatically updates existing databases.

    Existing rows are deliberately left untouched for upgrade compatibility;
    every subsequent insert or attempt to change ``run_type`` is guarded.
    """

    allowed_sql = ", ".join(
        f"'{run_type}'" for run_type in sorted(INTELLIGENCE_RUN_TYPES)
    )
    conn.executescript(
        f"""
        DROP TRIGGER IF EXISTS {_INSERT_GUARD};
        DROP TRIGGER IF EXISTS {_UPDATE_GUARD};

        CREATE TRIGGER {_INSERT_GUARD}
        BEFORE INSERT ON intelligence_runs
        WHEN NEW.run_type NOT IN ({allowed_sql})
        BEGIN
            SELECT RAISE(ABORT, '{_INVALID_RUN_TYPE_MESSAGE}');
        END;

        CREATE TRIGGER {_UPDATE_GUARD}
        BEFORE UPDATE OF run_type ON intelligence_runs
        WHEN NEW.run_type NOT IN ({allowed_sql})
        BEGIN
            SELECT RAISE(ABORT, '{_INVALID_RUN_TYPE_MESSAGE}');
        END;
        """
    )

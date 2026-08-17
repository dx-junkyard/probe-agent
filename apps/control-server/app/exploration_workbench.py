"""Exploration Workbench: comparing implementation MODALITIES against one
Node contract (Issue #398, Phase 3 of the evolution control plane Epic #394).

The canonical contract is `docs/evolutionary-pipeline.md`. Phase 3's question
is not "which LLM candidate is best" -- it is "for the SAME Node contract and
the SAME evaluation refs, how do an LLM implementation, a rule implementation
and a deterministic-code implementation compare". That question is only
expressible because ADR-3 versioned what a Node PROMISES separately from how
it currently keeps that promise.

Four rules make this module correct, and every one of them is enforced
structurally rather than by convention:

1. **This is a record of comparisons, not a second execution engine.** Replay
   (#242-#246), Experiments (#26) and the offline shadow sandbox remain the
   only things that run code. A variant REFERENCES the run that produced its
   numbers; it never carries source, a patch, or a command. Accepting those
   through this API would bypass the pinned-snapshot, pinned-command,
   network-off sandbox those features already enforce (Principle 8).

2. **What must be held constant lives on the run, not the variant.** Dataset,
   snapshot, environment and evaluation policy refs are columns of
   `exploration_run`, so two variants in one run cannot have been measured
   against different datasets -- which would make their numbers incomparable
   while still looking like a comparison.

3. **Nothing is composited.** Each dimension is its own measurement row with
   its own coverage, and there is no score, weight or total anywhere. #398
   forbids collapsing quality / latency / cost / safety into one number, and
   the reliable enforcement is to give that number nowhere to live. A caller
   that wants a ranking must name the dimension it is ranking by --
   `rank_by_dimension` requires it as an argument for exactly that reason.

4. **A gap is never rounded into a win or a loss.** `value_state` separates
   `measured` / `not_applicable` / `not_measured` / `unsupported`, and
   `compare_variants` reports a dimension where the two sides disagree about
   measurability as `incomparable`, never as a difference of zero. Coverage
   is per dimension because it genuinely differs between them, and two
   variants measured at different coverage are reported as such rather than
   compared silently.

probe-agent:
  role: Modality-neutral exploration run/variant contract and deterministic, non-composited comparison
  capability: evolution-control-plane
  element_type: core
  operation_kind: analysis
  consumers: [control-server-routes]
  state_effects: [database-read, database-write]
  probe_value: Verify variants in one run always share dataset/snapshot/evaluation refs, no composite score exists anywhere, an unmeasured or unsupported dimension is never reported as a zero difference, differing coverage is surfaced rather than compared silently, and no source/patch/command can enter through this contract.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Tuple, get_args

from . import evolution_node

__all__ = [
    "MEASUREMENT_DIMENSIONS",
    "VALUE_STATES",
    "EXECUTION_STATES",
    "EXECUTION_REF_KINDS",
    "GENERATORS",
    "DATASET_KINDS",
    "RUN_STATUSES",
    "MEASUREMENT_SOURCES",
    "COMPARISON_VERDICTS",
    "HIGHER_IS_BETTER",
    "ExplorationError",
    "ExplorationNotFoundError",
    "ExplorationValidationError",
    "ExplorationConflictError",
    "DimensionComparison",
    "create_run",
    "add_variant",
    "record_measurement",
    "attach_execution",
    "compare_variants",
    "build_run_projection",
    "rank_by_dimension",
    "complete_run",
]


# ---------------------------------------------------------------------------
# Finite vocabularies (Principle 6)
# ---------------------------------------------------------------------------

MeasurementDimension = Literal[
    "output_quality", "error_rate", "latency", "cost", "resource", "safety", "coverage"
]
MEASUREMENT_DIMENSIONS: Tuple[str, ...] = get_args(MeasurementDimension)

ValueState = Literal["measured", "not_applicable", "not_measured", "unsupported"]
VALUE_STATES: Tuple[str, ...] = get_args(ValueState)

ExecutionState = Literal["not_executed", "executed", "not_executable", "unsupported"]
EXECUTION_STATES: Tuple[str, ...] = get_args(ExecutionState)

ExecutionRefKind = Literal["replay_run", "replay_variant", "experiment"]
EXECUTION_REF_KINDS: Tuple[str, ...] = get_args(ExecutionRefKind)

Generator = Literal["manual", "reasoning_llm", "existing_implementation"]
GENERATORS: Tuple[str, ...] = get_args(Generator)

DatasetKind = Literal["replay_set", "golden_set", "edge_cases", "mixed"]
DATASET_KINDS: Tuple[str, ...] = get_args(DatasetKind)

RunStatus = Literal["open", "completed", "abandoned"]
RUN_STATUSES: Tuple[str, ...] = get_args(RunStatus)

MeasurementSource = Literal["deterministic", "reasoning_llm", "manual"]
MEASUREMENT_SOURCES: Tuple[str, ...] = get_args(MeasurementSource)

# The verdict for ONE dimension of ONE variant against the baseline.
#
# `incomparable` is the value this whole module exists to make possible: it
# is what a dimension reads as when the two sides disagree about whether it
# was measurable at all. Without it, "the rule variant has no token cost" and
# "the rule variant's token cost is zero" would produce the same display, and
# a modality comparison would silently reward whichever variant simply failed
# to be measured.
ComparisonVerdict = Literal[
    "better", "worse", "equal", "incomparable", "coverage_mismatch"
]
COMPARISON_VERDICTS: Tuple[str, ...] = get_args(ComparisonVerdict)

# Which direction counts as an improvement, per dimension. This table is the
# ONLY place direction is encoded, and it is deliberately explicit rather
# than inferred from a metric's name: guessing that "error_rate" is
# lower-is-better from its spelling would silently invert the verdict for any
# metric whose name did not match the guess.
HIGHER_IS_BETTER: Dict[str, bool] = {
    "output_quality": True,
    "error_rate": False,
    "latency": False,
    "cost": False,
    "resource": False,
    "safety": True,
    "coverage": True,
}


class ExplorationError(ValueError):
    """Base class for every failure this module raises."""


class ExplorationNotFoundError(ExplorationError):
    """A referenced row does not exist, or belongs to another System.

    Deliberately the same error for both: distinguishing them would let a
    caller probe another System's ids.
    """


class ExplorationValidationError(ExplorationError):
    """A value outside a finite vocabulary, or a structurally invalid input."""


class ExplorationConflictError(ExplorationError):
    """The row exists but is not in a state where this operation is legal."""


def _check_membership(value: str, vocabulary: Tuple[str, ...], field_name: str) -> None:
    if value not in vocabulary:
        raise ExplorationValidationError(
            f"{field_name} must be one of {', '.join(vocabulary)}; got {value!r}"
        )


def _json_or_default(text: Optional[str], default: Any) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return default


def _require_run(conn: sqlite3.Connection, system_id: int, run_id: int) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM exploration_run WHERE id = ? AND system_id = ?",
        (run_id, system_id),
    ).fetchone()
    if row is None:
        raise ExplorationNotFoundError(f"Exploration run {run_id} not found")
    return row


def _require_variant(
    conn: sqlite3.Connection, system_id: int, variant_id: int
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM exploration_variant WHERE id = ? AND system_id = ?",
        (variant_id, system_id),
    ).fetchone()
    if row is None:
        raise ExplorationNotFoundError(f"Exploration variant {variant_id} not found")
    return row


def _require_open_run(
    conn: sqlite3.Connection, system_id: int, run_id: int, action: str
) -> sqlite3.Row:
    """A completed run is exactly what #399's establishment gate reads as
    evidence, so its variants and measurements must be immutable from the
    moment it closes -- otherwise the numbers a human completed against could
    be silently rewritten afterwards."""
    run = _require_run(conn, system_id, run_id)
    if run["status"] != "open":
        raise ExplorationConflictError(
            f"Exploration run {run_id} is {run['status']}; {action} is only "
            "possible while it is open"
        )
    return run


def _direction(dimension: str) -> bool:
    """Fail-closed direction lookup. A dimension with no entry in
    `HIGHER_IS_BETTER` has no comparison direction, and defaulting one would
    silently decide better/worse by a guess -- the exact inversion risk the
    table exists to prevent."""
    try:
        return HIGHER_IS_BETTER[dimension]
    except KeyError:
        raise ExplorationValidationError(
            f"dimension {dimension!r} has no entry in HIGHER_IS_BETTER; a "
            "dimension without an explicit direction cannot be compared"
        ) from None


# ---------------------------------------------------------------------------
# Runs and variants
# ---------------------------------------------------------------------------


def create_run(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    node_id: int,
    node_version_id: int,
    objective: str = "",
    dataset_kind: str = "replay_set",
    dataset_ref: str = "",
    snapshot_id: Optional[int] = None,
    commit_sha: str = "",
    environment_ref: str = "",
    evaluation_policy_ids: Sequence[int] = (),
    handoff_id: Optional[int] = None,
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Open a comparison.

    The contract version is pinned HERE, and every variant in the run
    implements that exact version. A run whose variants implemented different
    contract versions would be comparing two different promises, which is the
    one thing a modality comparison must not do -- so `add_variant` refuses an
    implementation belonging to a different version.

    Every evaluation policy id is resolved against this System before it is
    stored. An unresolvable one is refused rather than dropped: a run that
    quietly lost its safety contract would still look like a complete
    comparison.
    """
    _check_membership(dataset_kind, DATASET_KINDS, "dataset_kind")

    node = conn.execute(
        "SELECT id FROM evolution_node WHERE id = ? AND system_id = ?",
        (node_id, system_id),
    ).fetchone()
    if node is None:
        raise ExplorationNotFoundError(f"Evolution Node {node_id} not found")

    version = conn.execute(
        """SELECT id FROM evolution_node_version
               WHERE id = ? AND node_id = ? AND system_id = ?""",
        (node_version_id, node_id, system_id),
    ).fetchone()
    if version is None:
        raise ExplorationNotFoundError(
            f"Node version {node_version_id} does not belong to Node {node_id}"
        )

    for policy_id in evaluation_policy_ids:
        row = conn.execute(
            "SELECT id FROM evolution_evaluation_policy WHERE id = ? AND system_id = ?",
            (policy_id, system_id),
        ).fetchone()
        if row is None:
            raise ExplorationNotFoundError(
                f"Evaluation policy {policy_id} not found in this System"
            )

    if handoff_id is not None:
        row = conn.execute(
            "SELECT id FROM evolution_design_handoff WHERE id = ? AND system_id = ?",
            (handoff_id, system_id),
        ).fetchone()
        if row is None:
            raise ExplorationNotFoundError(f"Design handoff {handoff_id} not found")

    now = time.time()
    cur = conn.execute(
        """INSERT INTO exploration_run
               (system_id, node_id, node_version_id, handoff_id, objective,
                dataset_kind, dataset_ref, snapshot_id, commit_sha, environment_ref,
                evaluation_policy_ids_json, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            system_id, node_id, node_version_id, handoff_id, objective,
            dataset_kind, dataset_ref, snapshot_id, commit_sha, environment_ref,
            json.dumps(list(evaluation_policy_ids)), created_by, now,
        ),
    )
    return conn.execute(
        "SELECT * FROM exploration_run WHERE id = ?", (cur.lastrowid,)
    ).fetchone()


def add_variant(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    run_id: int,
    variant_key: str,
    modality: str,
    label: str = "",
    is_baseline: bool = False,
    implementation_id: Optional[int] = None,
    config: Optional[Mapping[str, Any]] = None,
    provenance: Optional[Mapping[str, Any]] = None,
    generator: str = "manual",
    applicability_envelope: Optional[Mapping[str, Any]] = None,
    created_by: Optional[str] = None,
) -> sqlite3.Row:
    """Register one implementation in a comparison.

    Note what this signature does NOT accept: source, a patch, or a command.
    A variant references an implementation row and, later, the run that
    executed it. Accepting executable content here would let a caller run
    code outside the pinned-snapshot, network-off sandbox that Replay and
    Experiments enforce (Principle 8).

    Exactly one baseline per run. A second one is refused rather than
    silently replacing the first, because "which of these numbers is the
    reference" is not a question a comparison may be ambiguous about.
    """
    _check_membership(modality, evolution_node.IMPLEMENTATION_MODALITIES, "modality")
    _check_membership(generator, GENERATORS, "generator")
    if not (variant_key or "").strip():
        raise ExplorationValidationError("variant_key must not be empty")

    run = _require_open_run(conn, system_id, run_id, "adding a variant")

    if implementation_id is not None:
        implementation = conn.execute(
            """SELECT id, node_version_id, modality FROM evolution_node_implementation
                   WHERE id = ? AND system_id = ?""",
            (implementation_id, system_id),
        ).fetchone()
        if implementation is None:
            raise ExplorationNotFoundError(
                f"Node implementation {implementation_id} not found"
            )
        if implementation["node_version_id"] != run["node_version_id"]:
            # Comparing implementations of two different contract versions is
            # comparing two different promises. That is the one thing a
            # modality comparison must never do.
            raise ExplorationConflictError(
                f"Implementation {implementation_id} implements contract version "
                f"{implementation['node_version_id']}, but this run pins "
                f"{run['node_version_id']}"
            )
        if implementation["modality"] != modality:
            raise ExplorationConflictError(
                f"Implementation {implementation_id} has modality "
                f"{implementation['modality']!r}, not {modality!r}"
            )

    if is_baseline:
        existing = conn.execute(
            "SELECT id FROM exploration_variant WHERE run_id = ? AND is_baseline = 1",
            (run_id,),
        ).fetchone()
        if existing is not None:
            raise ExplorationConflictError(
                f"Run {run_id} already has a baseline (variant {existing['id']})"
            )

    duplicate = conn.execute(
        "SELECT id FROM exploration_variant WHERE run_id = ? AND variant_key = ?",
        (run_id, variant_key),
    ).fetchone()
    if duplicate is not None:
        raise ExplorationConflictError(
            f"variant_key {variant_key!r} already exists in run {run_id}"
        )

    now = time.time()
    conn.execute("BEGIN")
    try:
        cur = conn.execute(
            """INSERT INTO exploration_variant
                   (run_id, system_id, variant_key, label, is_baseline, modality,
                    implementation_id, config_json, provenance_json, generator,
                    applicability_envelope_json, created_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, system_id, variant_key, label or variant_key,
                1 if is_baseline else 0, modality, implementation_id,
                json.dumps(dict(config or {})),
                json.dumps(dict(provenance or {})),
                generator,
                json.dumps(dict(applicability_envelope or {})),
                created_by, now,
            ),
        )
        variant_id = cur.lastrowid
        if is_baseline:
            conn.execute(
                "UPDATE exploration_run SET baseline_variant_id = ? WHERE id = ?",
                (variant_id, run_id),
            )
        row = conn.execute(
            "SELECT * FROM exploration_variant WHERE id = ?", (variant_id,)
        ).fetchone()
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return row


def attach_execution(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    variant_id: int,
    execution_state: str,
    execution_ref_kind: Optional[str] = None,
    execution_ref_id: Optional[int] = None,
    note: str = "",
) -> sqlite3.Row:
    """Record WHICH existing run produced this variant's numbers.

    `executed` requires a reference that actually resolves in this System:
    a variant claiming to be executed with nothing behind it would let
    unmeasured numbers enter a comparison.

    `not_executable` and `unsupported` are first-class outcomes, not
    failures to be smoothed over -- #398 requires that they are never
    rounded into a loss. A rule variant that cannot express a case has not
    lost on that case; it has not been measured on it.
    """
    _check_membership(execution_state, EXECUTION_STATES, "execution_state")
    variant = _require_variant(conn, system_id, variant_id)
    _require_open_run(conn, system_id, variant["run_id"], "attaching an execution")

    if execution_state == "executed":
        if execution_ref_kind is None or execution_ref_id is None:
            raise ExplorationValidationError(
                "execution_state 'executed' requires execution_ref_kind and "
                "execution_ref_id -- an executed variant must name the run that "
                "produced its numbers"
            )
        _check_membership(execution_ref_kind, EXECUTION_REF_KINDS, "execution_ref_kind")
        table = {
            "replay_run": "replay_runs",
            "replay_variant": "replay_variants",
            "experiment": "experiments",
        }[execution_ref_kind]
        row = conn.execute(
            f"SELECT id, status FROM {table} WHERE id = ? AND system_id = ?",
            (execution_ref_id, system_id),
        ).fetchone()
        if row is None:
            raise ExplorationNotFoundError(
                f"{execution_ref_kind} {execution_ref_id} not found in this System"
            )
        # §6.1: the cited id must point at an existing COMPLETED run.
        # 'completed' is the one terminal successful state in all three
        # status vocabularies (replay_runs / replay_variants:
        # running|completed|failed; experiments: draft|running|completed|
        # failed). A draft, running or failed run has no numbers to stand
        # behind, so accepting it would let unmeasured numbers enter a
        # comparison under an "executed" label.
        if row["status"] != "completed":
            raise ExplorationConflictError(
                f"{execution_ref_kind} {execution_ref_id} is {row['status']!r}; "
                "only a completed run can back execution_state 'executed'"
            )
    elif execution_ref_kind is not None or execution_ref_id is not None:
        raise ExplorationValidationError(
            f"execution_state {execution_state!r} must not carry an execution "
            "reference -- only 'executed' names a run"
        )

    conn.execute(
        """UPDATE exploration_variant
               SET execution_state = ?, execution_ref_kind = ?, execution_ref_id = ?,
                   execution_note = ?
               WHERE id = ?""",
        (execution_state, execution_ref_kind, execution_ref_id, note, variant_id),
    )
    return conn.execute(
        "SELECT * FROM exploration_variant WHERE id = ?", (variant_id,)
    ).fetchone()


def record_measurement(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    variant_id: int,
    dimension: str,
    metric_name: str = "",
    value_state: str = "measured",
    numeric_value: Optional[float] = None,
    unit: str = "",
    covered_case_count: Optional[int] = None,
    total_case_count: Optional[int] = None,
    source: str = "deterministic",
    note: str = "",
) -> sqlite3.Row:
    """Record ONE dimension of ONE variant.

    `measured` requires a number, and every other state forbids one. That
    pairing is the whole guarantee: a `not_measured` row carrying a value
    would be indistinguishable from a real reading the moment anything read
    the column instead of the state.

    `source` keeps a deterministic fact apart from a reasoning model's
    judgement in storage, which is what stops a judge model's opinion from
    quietly becoming the adoption basis (#398, and CLAUDE.md's rule on
    keeping raw facts separate from LLM interpretations).
    """
    _check_membership(dimension, MEASUREMENT_DIMENSIONS, "dimension")
    _check_membership(value_state, VALUE_STATES, "value_state")
    _check_membership(source, MEASUREMENT_SOURCES, "source")
    variant = _require_variant(conn, system_id, variant_id)
    # The upsert below can OVERWRITE an existing reading, so the open-run
    # gate is what keeps a completed run's numbers immutable -- they are the
    # evidence #399's establishment gate reads.
    _require_open_run(conn, system_id, variant["run_id"], "recording a measurement")

    if value_state == "measured" and numeric_value is None:
        raise ExplorationValidationError(
            "value_state 'measured' requires a numeric_value"
        )
    if value_state != "measured" and numeric_value is not None:
        raise ExplorationValidationError(
            f"value_state {value_state!r} must not carry a numeric_value -- a "
            "value paired with a non-measured state is indistinguishable from a "
            "real reading"
        )
    if (
        covered_case_count is not None
        and total_case_count is not None
        and covered_case_count > total_case_count
    ):
        raise ExplorationValidationError(
            "covered_case_count cannot exceed total_case_count"
        )

    now = time.time()
    conn.execute(
        """INSERT INTO exploration_measurement
               (variant_id, run_id, system_id, dimension, metric_name, value_state,
                numeric_value, unit, covered_case_count, total_case_count, source,
                note, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT (variant_id, dimension, metric_name) DO UPDATE SET
               value_state = excluded.value_state,
               numeric_value = excluded.numeric_value,
               unit = excluded.unit,
               covered_case_count = excluded.covered_case_count,
               total_case_count = excluded.total_case_count,
               source = excluded.source,
               note = excluded.note,
               created_at = excluded.created_at""",
        (
            variant_id, variant["run_id"], system_id, dimension, metric_name,
            value_state, numeric_value, unit, covered_case_count, total_case_count,
            source, note, now,
        ),
    )
    return conn.execute(
        """SELECT * FROM exploration_measurement
               WHERE variant_id = ? AND dimension = ? AND metric_name = ?""",
        (variant_id, dimension, metric_name),
    ).fetchone()


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DimensionComparison:
    dimension: str
    metric_name: str
    verdict: str
    baseline_state: str
    variant_state: str
    baseline_value: Optional[float]
    variant_value: Optional[float]
    delta: Optional[float]
    baseline_coverage: Optional[Tuple[int, int]]
    variant_coverage: Optional[Tuple[int, int]]
    reason: str

    def as_dict(self) -> Dict[str, Any]:
        return {
            "dimension": self.dimension,
            "metric_name": self.metric_name,
            "verdict": self.verdict,
            "baseline_state": self.baseline_state,
            "variant_state": self.variant_state,
            "baseline_value": self.baseline_value,
            "variant_value": self.variant_value,
            "delta": self.delta,
            "baseline_coverage": list(self.baseline_coverage)
            if self.baseline_coverage
            else None,
            "variant_coverage": list(self.variant_coverage)
            if self.variant_coverage
            else None,
            "reason": self.reason,
        }


def _coverage(row: Mapping[str, Any]) -> Optional[Tuple[int, int]]:
    covered = row["covered_case_count"]
    total = row["total_case_count"]
    if covered is None or total is None:
        return None
    return (covered, total)


def compare_variants(
    baseline_measurements: Sequence[Mapping[str, Any]],
    variant_measurements: Sequence[Mapping[str, Any]],
) -> List[DimensionComparison]:
    """Compare one variant against the baseline, dimension by dimension.

    Pure: same rows in, same verdicts out. No clock, no DB, no ordering
    dependence.

    The verdict per dimension is decided by a first-match table, and the
    order of its rows is the contract:

    1. Either side not `measured` -> `incomparable`. This comes FIRST, ahead
       of any numeric comparison, because it is the case #398 exists to
       protect: "the rule variant has no token cost" and "the rule variant's
       token cost is zero" must never produce the same verdict. Rounding an
       unmeasured dimension to a zero delta rewards whichever variant simply
       failed to be measured.
    2. Coverage differs -> `coverage_mismatch`. Two numbers computed over
       different case counts are not comparable, and reporting the larger one
       as better is the "coverage 差を丸める" failure by another route. The
       numbers are still reported so a human can judge; the verdict says they
       were not compared.
    3. Equal values -> `equal`.
    4. Otherwise `better` / `worse`, using `HIGHER_IS_BETTER` for direction.

    A dimension present on only one side is reported, not dropped: a missing
    safety measurement on the candidate is exactly the thing a reader must
    see.
    """
    def _index(rows: Sequence[Mapping[str, Any]]) -> Dict[Tuple[str, str], Mapping[str, Any]]:
        return {(row["dimension"], row["metric_name"] or ""): row for row in rows}

    baseline_index = _index(baseline_measurements)
    variant_index = _index(variant_measurements)

    comparisons: List[DimensionComparison] = []
    for key in sorted(set(baseline_index) | set(variant_index)):
        dimension, metric_name = key
        baseline = baseline_index.get(key)
        variant = variant_index.get(key)

        baseline_state = baseline["value_state"] if baseline is not None else "not_measured"
        variant_state = variant["value_state"] if variant is not None else "not_measured"
        baseline_value = baseline["numeric_value"] if baseline is not None else None
        variant_value = variant["numeric_value"] if variant is not None else None
        baseline_coverage = _coverage(baseline) if baseline is not None else None
        variant_coverage = _coverage(variant) if variant is not None else None

        # Row 1 -- measurability disagreement, before any arithmetic.
        if baseline_state != "measured" or variant_state != "measured":
            comparisons.append(
                DimensionComparison(
                    dimension=dimension,
                    metric_name=metric_name,
                    verdict="incomparable",
                    baseline_state=baseline_state,
                    variant_state=variant_state,
                    baseline_value=baseline_value,
                    variant_value=variant_value,
                    delta=None,
                    baseline_coverage=baseline_coverage,
                    variant_coverage=variant_coverage,
                    reason=(
                        f"baseline is {baseline_state}, variant is {variant_state}; "
                        "an unmeasured dimension is not a difference of zero"
                    ),
                )
            )
            continue

        # Row 2 -- different coverage means the two numbers describe
        # different case sets.
        if baseline_coverage != variant_coverage:
            comparisons.append(
                DimensionComparison(
                    dimension=dimension,
                    metric_name=metric_name,
                    verdict="coverage_mismatch",
                    baseline_state=baseline_state,
                    variant_state=variant_state,
                    baseline_value=baseline_value,
                    variant_value=variant_value,
                    delta=None,
                    baseline_coverage=baseline_coverage,
                    variant_coverage=variant_coverage,
                    reason=(
                        "the two readings cover different case sets "
                        f"({baseline_coverage} vs {variant_coverage}); they are "
                        "reported but not compared"
                    ),
                )
            )
            continue

        delta = variant_value - baseline_value
        if delta == 0:
            verdict = "equal"
        elif _direction(dimension):
            verdict = "better" if delta > 0 else "worse"
        else:
            verdict = "better" if delta < 0 else "worse"

        comparisons.append(
            DimensionComparison(
                dimension=dimension,
                metric_name=metric_name,
                verdict=verdict,
                baseline_state=baseline_state,
                variant_state=variant_state,
                baseline_value=baseline_value,
                variant_value=variant_value,
                delta=delta,
                baseline_coverage=baseline_coverage,
                variant_coverage=variant_coverage,
                reason="",
            )
        )
    return comparisons


def rank_by_dimension(
    variants: Sequence[Mapping[str, Any]],
    dimension: str,
    metric_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Order variants by ONE named dimension.

    The dimension is a required argument, and there is no overall ranking
    function anywhere in this module. That is #398's rule made into an API
    shape: a caller that wants an order has to say what it is ordering by,
    so a latency ranking can never be presented as "the best variant".

    A reading is identified by (dimension, metric_name) -- the same key
    `compare_variants` uses -- so a baseline measured on one metric can
    never be ranked against a candidate measured on another. When the
    variants carry more than one metric_name for the dimension, the caller
    must name the one to rank by; picking one silently would present a
    ranking across different metrics under a single heading.

    Coverage follows `compare_variants`' rule too: two numbers computed
    over different case sets are not comparable. The basis is the baseline
    variant's coverage when a measured baseline exists; without one, every
    measured reading must share a single coverage, and if they disagree
    nothing is ranked -- there is no deterministic way to decide which
    group is "the" ranking.

    Variants whose reading is not `measured`, measured under a different
    metric, or measured at a different coverage are returned in a separate
    `unranked` group with an explicit `reason`, rather than sorted to the
    bottom or dropped -- sorting them last would read as "worst", and
    dropping them would hide exactly what a reader must see.
    """
    _check_membership(dimension, MEASUREMENT_DIMENSIONS, "dimension")
    higher_is_better = _direction(dimension)

    metric_names = sorted(
        {
            measurement["metric_name"] or ""
            for variant in variants
            for measurement in variant.get("measurements", [])
            if measurement["dimension"] == dimension
        }
    )
    if metric_name is None:
        if len(metric_names) > 1:
            raise ExplorationValidationError(
                f"dimension {dimension!r} was measured under multiple metrics "
                f"({', '.join(repr(name) for name in metric_names)}); name the "
                "metric_name to rank by -- an order across different metrics is "
                "not a ranking"
            )
        metric_name = metric_names[0] if metric_names else ""

    measured: List[Tuple[Optional[Tuple[int, int]], bool, Dict[str, Any]]] = []
    unranked: List[Dict[str, Any]] = []
    for variant in variants:
        reading = next(
            (
                m
                for m in variant.get("measurements", [])
                if m["dimension"] == dimension
                and (m["metric_name"] or "") == metric_name
            ),
            None,
        )
        entry = {
            "variant_id": variant["id"],
            "variant_key": variant["variant_key"],
            "modality": variant["modality"],
            "value": reading["numeric_value"] if reading else None,
            "value_state": reading["value_state"] if reading else "not_measured",
            "reason": "",
        }
        if reading is None:
            other_metrics = sorted(
                {
                    m["metric_name"] or ""
                    for m in variant.get("measurements", [])
                    if m["dimension"] == dimension
                }
            )
            if other_metrics:
                entry["reason"] = (
                    f"measured under {', '.join(repr(n) for n in other_metrics)}, "
                    f"not {metric_name!r}; different metrics are never ranked "
                    "against each other"
                )
            else:
                entry["reason"] = f"no {dimension} measurement"
            unranked.append(entry)
        elif reading["value_state"] != "measured":
            entry["reason"] = f"value_state is {reading['value_state']}"
            unranked.append(entry)
        else:
            measured.append(
                (_coverage(reading), bool(variant.get("is_baseline")), entry)
            )

    coverages = {coverage for coverage, _, _ in measured}
    baseline_coverages = [
        coverage for coverage, is_baseline, _ in measured if is_baseline
    ]
    ranked: List[Dict[str, Any]] = []
    if baseline_coverages:
        basis: Optional[Tuple[int, int]] = baseline_coverages[0]
        basis_known = True
    elif len(coverages) <= 1:
        basis = next(iter(coverages), None)
        basis_known = True
    else:
        basis = None
        basis_known = False

    for coverage, _, entry in measured:
        if not basis_known or coverage != basis:
            entry["reason"] = (
                "coverage differs across the measured readings "
                f"({coverage} vs basis {basis if basis_known else 'undecidable'}); "
                "numbers over different case sets are reported, not ranked"
            )
            unranked.append(entry)
        else:
            ranked.append(entry)

    ranked.sort(key=lambda e: e["value"], reverse=higher_is_better)
    return {
        "ranked": ranked,
        "unranked": unranked,
        "dimension": dimension,
        "metric_name": metric_name,
    }


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _measurement_doc(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "dimension": row["dimension"],
        "metric_name": row["metric_name"],
        "value_state": row["value_state"],
        "numeric_value": row["numeric_value"],
        "unit": row["unit"],
        "covered_case_count": row["covered_case_count"],
        "total_case_count": row["total_case_count"],
        "source": row["source"],
        "note": row["note"],
    }


def _variant_doc(row: sqlite3.Row, measurements: Sequence[sqlite3.Row]) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "variant_key": row["variant_key"],
        "label": row["label"],
        "is_baseline": bool(row["is_baseline"]),
        "modality": row["modality"],
        "implementation_id": row["implementation_id"],
        "config": _json_or_default(row["config_json"], {}),
        "provenance": _json_or_default(row["provenance_json"], {}),
        "generator": row["generator"],
        "applicability_envelope": _json_or_default(
            row["applicability_envelope_json"], {}
        ),
        "execution_state": row["execution_state"],
        "execution_ref_kind": row["execution_ref_kind"],
        "execution_ref_id": row["execution_ref_id"],
        "execution_note": row["execution_note"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "measurements": [_measurement_doc(m) for m in measurements],
    }


def build_run_projection(
    conn: sqlite3.Connection, *, system_id: int, run_id: int
) -> Dict[str, Any]:
    """The whole comparison as one document.

    Contains no ranking and no overall verdict. The per-dimension
    comparisons against the baseline are included; deciding which of them
    matters is the developer's judgement and, at establishment time, #399's
    gate -- not this projection's.

    `comparable` is reported as its own flag rather than being implied by an
    empty comparison list: a run with no baseline yet is a legitimate
    in-progress state, and it must not read as "compared, and nothing
    differed".
    """
    run = _require_run(conn, system_id, run_id)
    variant_rows = conn.execute(
        "SELECT * FROM exploration_variant WHERE run_id = ? ORDER BY id",
        (run_id,),
    ).fetchall()

    variants: List[Dict[str, Any]] = []
    measurements_by_variant: Dict[int, List[sqlite3.Row]] = {}
    for variant_row in variant_rows:
        measurement_rows = conn.execute(
            """SELECT * FROM exploration_measurement WHERE variant_id = ?
                   ORDER BY dimension, metric_name""",
            (variant_row["id"],),
        ).fetchall()
        measurements_by_variant[variant_row["id"]] = measurement_rows
        variants.append(_variant_doc(variant_row, measurement_rows))

    baseline_id = run["baseline_variant_id"]
    # Keyed by the variant id as a STRING: this document is returned as JSON,
    # whose object keys are strings by definition. Keying by int here would
    # round-trip as a string anyway, leaving the server and every client
    # disagreeing about the key type.
    comparisons: Dict[str, List[Dict[str, Any]]] = {}
    if baseline_id is not None:
        baseline_measurements = [
            _measurement_doc(m) for m in measurements_by_variant.get(baseline_id, [])
        ]
        for variant in variants:
            if variant["id"] == baseline_id:
                continue
            comparisons[str(variant["id"])] = [
                comparison.as_dict()
                for comparison in compare_variants(
                    baseline_measurements, variant["measurements"]
                )
            ]

    return {
        "id": run["id"],
        "system_id": system_id,
        "node_id": run["node_id"],
        "node_version_id": run["node_version_id"],
        "handoff_id": run["handoff_id"],
        "objective": run["objective"],
        # Held constant for every variant -- that is what makes the numbers
        # comparable at all, so it is reported once at the run level.
        "dataset_kind": run["dataset_kind"],
        "dataset_ref": run["dataset_ref"],
        "snapshot_id": run["snapshot_id"],
        "commit_sha": run["commit_sha"],
        "environment_ref": run["environment_ref"],
        "evaluation_policy_ids": _json_or_default(
            run["evaluation_policy_ids_json"], []
        ),
        "status": run["status"],
        "conclusion_note": run["conclusion_note"],
        "baseline_variant_id": baseline_id,
        "comparable": baseline_id is not None,
        "variants": variants,
        "comparisons": comparisons,
        "created_by": run["created_by"],
        "created_at": run["created_at"],
        "completed_at": run["completed_at"],
    }


def complete_run(
    conn: sqlite3.Connection,
    *,
    system_id: int,
    run_id: int,
    conclusion_note: str = "",
) -> sqlite3.Row:
    """Close a comparison.

    Refused without a baseline: a difference measured against nothing is not
    a difference, and a completed run is what #399's establishment gate will
    read as evidence.

    Completing decides nothing about adoption. It does not change a Node's
    maturity, pin an implementation, or approve anything -- those are
    separate human gates (ADR-9), and a comparison that quietly promoted its
    winner would be exactly the automatic adoption this Epic forbids.
    """
    run = _require_run(conn, system_id, run_id)
    if run["status"] != "open":
        raise ExplorationConflictError(
            f"Exploration run {run_id} is already {run['status']}"
        )
    if run["baseline_variant_id"] is None:
        raise ExplorationConflictError(
            f"Exploration run {run_id} has no baseline; a comparison without a "
            "reference point cannot be completed"
        )
    conn.execute(
        """UPDATE exploration_run
               SET status = 'completed', conclusion_note = ?, completed_at = ?
               WHERE id = ?""",
        (conclusion_note, time.time(), run_id),
    )
    return conn.execute(
        "SELECT * FROM exploration_run WHERE id = ?", (run_id,)
    ).fetchone()

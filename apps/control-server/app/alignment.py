"""Alignment Review / Review Queue (Issue #287).

Contrasts confirmed/proposed Intent Brief items (``interview_intent_item``,
Issue #284) against the evidence-backed Current System understanding (the
latest ``understanding_revision``, Issue #136) and produces "alignment
items": one row per contrast point, each carrying its own claim + evidence
and a *deterministic* review classification.

Split per Principle 6:

- Reasoning model (``generate_alignment_proposal``, ``prompt_version`` /
  ``schema_version`` = ``alignment-v1``): proposes *content* -- which Intent
  field a current-system claim relates to, the claim text, its evidence,
  the alignment state (aligned/gap/unknown/conflict/not_applicable), risk
  flags (from a finite vocabulary), confidence, and a gap/interpretation
  summary. Every finite-set field (``alignment_state`` / ``confidence`` /
  ``risk_flags`` / ``intent_field``) is schema-validated against its finite
  set here; any value outside the set fails the whole build closed, exactly
  like Issue #286's Question Router.
- ``validate_evidence_against_snapshot``: deterministic structural check
  (path exists in the pinned snapshot's tree, line range inside the file)
  -- never a reasoning decision. Mirrors ``investigation_agent``'s evidence
  check: unverifiable citations are pruned from an item (recorded); an item
  left with zero evidence is dropped entirely.
- ``classify_alignment_item``: a pure, data-driven rule table (first match
  wins) mapping the reasoning model's finite output fields to
  ``review_category`` / ``reason_code``. No numeric scoring, no LLM
  ordering -- this is direct structural classification into an explicit
  finite set (Principle 6). The ordered table and fixed Japanese reason
  templates live in the separately reviewable
  ``app/policies/alignment_review.yaml``; its strict loader accepts no
  executable expressions and fails closed on any invalid schema or enum.
- ``USER_REASON_TEMPLATES`` / ``user_reason_for``: a fixed Japanese template
  per ``reason_code`` loaded from that validated policy -- never LLM free
  text (Principle 7's "concise reasons" requirement, made deterministic here
  since the reason a category was assigned is itself a structural fact, not
  an interpretation).
- ``review_sort_key``: deterministic queue ordering (category rank, then
  reason-code rank, then id) -- no numeric score multiplication.

Persistence (the rebuild-merge strategy, evidence-against-snapshot lookups,
and the ``intelligence_runs`` audit row) lives in
``routes/interview_alignment.py``, matching how ``routes/interview_intent.py``
and ``routes/interview_inquiry.py`` keep DB orchestration in the route layer
while the reasoning/validation logic stays in its own importable module.

probe-agent:
  role: Reasoning-model Alignment proposal + deterministic review
    classification rule table (Intent vs Current System)
  capability: interactive-system-understanding
  element_type: element
  consumers: [interview-alignment-routes]
  operation_kind: analysis
  state_effects: [external-api]
  probe_value: Verify classify_alignment_item's rule table stays exhaustive and deterministic, and that generate_alignment_proposal fails closed on mock/non-reasoning models or any out-of-finite-set field.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import yaml

from .git_ops import GitError, read_file_at_commit
from .interview_intent_agent import INTENT_FIELDS
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model
from .runtime_alignment import RUNTIME_CHECK_STATES

PROMPT_VERSION = "alignment-v1"
SCHEMA_VERSION = "alignment-v1"

ALIGNMENT_STATES = ("aligned", "gap", "unknown", "conflict", "not_applicable")
RISK_FLAGS = ("security", "high_risk", "core_intent")
CONFIDENCE_LEVELS = ("confirmed", "likely", "uncertain", "conflicting")

# Ordered (rank == index): "first" is the highest-priority category/reason
# for queue ordering (review_sort_key below).
REVIEW_CATEGORIES = (
    "must_review", "batch_reviewable", "no_review_required", "unchanged", "informational",
)
REASON_CODES = (
    "security_related", "high_risk", "core_intent", "conflict_detected",
    "low_confidence", "runtime_mismatch", "routine_update", "no_change",
    "informational_only", "unchanged_since_confirmation",
)

_CATEGORY_RANK: Dict[str, int] = {name: i for i, name in enumerate(REVIEW_CATEGORIES)}
_REASON_RANK: Dict[str, int] = {name: i for i, name in enumerate(REASON_CODES)}

# --- Reviewable, fail-closed review policy (Issue #313) ----------------------
#
# The rule table used to live as Python lambdas in this module.  Its behaviour
# remains deterministic and first-match ordered, but the policy is now a
# separate YAML artifact which can be reviewed without editing application
# code.  YAML never executes expressions: this loader accepts only a small,
# finite condition vocabulary and rejects unknown fields, duplicate keys, bad
# enum values, missing terminal coverage, and malformed templates.

POLICY_SCHEMA_VERSION = "alignment-review-policy-v1"
_DEFAULT_POLICY_PATH = Path(__file__).with_name("policies") / "alignment_review.yaml"


class AlignmentPolicyError(ValueError):
    """A review-classification policy is missing or unsafe to use.

    Callers must not replace this with a default policy.  A failed load is a
    fail-closed configuration error, preserving Principle 6.
    """


class _UniqueKeySafeLoader(yaml.SafeLoader):
    """Safe YAML loader which rejects duplicate mapping keys."""


def _construct_unique_mapping(loader, node, deep=False):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise AlignmentPolicyError(f"duplicate policy key: {key!r}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeySafeLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_unique_mapping,
)


@dataclass(frozen=True)
class AlignmentPolicyRule:
    """One finite, conjunction-only matcher in first-match order."""

    rule_id: str
    review_category: str
    reason_code: str
    alignment_state_in: Tuple[str, ...] = ()
    risk_flags_contains: Optional[str] = None
    confidence_in: Tuple[str, ...] = ()
    intent_field_in: Tuple[str, ...] = ()
    runtime_check_in: Tuple[str, ...] = ()

    def matches(
        self,
        *,
        alignment_state: str,
        risk_flags: List[str],
        confidence: str,
        intent_field: Optional[str],
        runtime_check: Optional[str],
    ) -> bool:
        return (
            (not self.alignment_state_in or alignment_state in self.alignment_state_in)
            and (self.risk_flags_contains is None or self.risk_flags_contains in risk_flags)
            and (not self.confidence_in or confidence in self.confidence_in)
            and (not self.intent_field_in or intent_field in self.intent_field_in)
            and (not self.runtime_check_in or runtime_check in self.runtime_check_in)
        )


@dataclass(frozen=True)
class AlignmentReviewPolicy:
    policy_version: str
    digest: str
    reason_templates: Mapping[str, str]
    rules: Tuple[AlignmentPolicyRule, ...]

    def classify(
        self,
        *,
        alignment_state: str,
        risk_flags: List[str],
        confidence: str,
        intent_field: Optional[str],
        runtime_check: Optional[str],
    ) -> Tuple[str, str]:
        review_category, reason_code, _rule_id = self.classify_with_rule(
            alignment_state=alignment_state,
            risk_flags=risk_flags,
            confidence=confidence,
            intent_field=intent_field,
            runtime_check=runtime_check,
        )
        return review_category, reason_code

    def classify_with_rule(
        self,
        *,
        alignment_state: str,
        risk_flags: List[str],
        confidence: str,
        intent_field: Optional[str],
        runtime_check: Optional[str],
    ) -> Tuple[str, str, str]:
        """Return the classification together with its unique policy rule."""
        for rule in self.rules:
            if rule.matches(
                alignment_state=alignment_state,
                risk_flags=risk_flags,
                confidence=confidence,
                intent_field=intent_field,
                runtime_check=runtime_check,
            ):
                return rule.review_category, rule.reason_code, rule.rule_id
        raise AlignmentPolicyError(
            "policy has no matching rule for "
            f"alignment_state={alignment_state!r}, confidence={confidence!r}"
        )


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise AlignmentPolicyError(f"{label} must be an object with string keys")
    return value


def _require_exact_keys(value: Mapping[str, Any], required: set, label: str) -> None:
    actual = set(value)
    if actual != required:
        missing = sorted(required - actual)
        unexpected = sorted(actual - required)
        raise AlignmentPolicyError(
            f"{label} must contain exactly {sorted(required)!r}; "
            f"missing={missing!r}, unexpected={unexpected!r}"
        )


def _require_nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise AlignmentPolicyError(f"{label} must be a non-empty string")
    return value


def _parse_enum_list(value: Any, allowed: Tuple[str, ...], label: str) -> Tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise AlignmentPolicyError(f"{label} must be a non-empty list")
    if any(not isinstance(item, str) or item not in allowed for item in value):
        raise AlignmentPolicyError(f"{label} must be drawn from {allowed!r}")
    if len(set(value)) != len(value):
        raise AlignmentPolicyError(f"{label} must not contain duplicate values")
    return tuple(value)


def _parse_rule(raw_rule: Any, index: int) -> AlignmentPolicyRule:
    label = f"rules[{index}]"
    rule = _require_mapping(raw_rule, label)
    _require_exact_keys(rule, {"id", "match", "review_category", "reason_code"}, label)
    rule_id = _require_nonempty_string(rule["id"], f"{label}.id")
    review_category = _require_nonempty_string(
        rule["review_category"], f"{label}.review_category"
    )
    reason_code = _require_nonempty_string(rule["reason_code"], f"{label}.reason_code")
    if review_category not in REVIEW_CATEGORIES:
        raise AlignmentPolicyError(f"{label}.review_category is not a known category")
    if reason_code not in REASON_CODES:
        raise AlignmentPolicyError(f"{label}.reason_code is not a known reason code")

    match = _require_mapping(rule["match"], f"{label}.match")
    allowed_match_keys = {
        "alignment_state_in", "risk_flags_contains", "confidence_in",
        "intent_field_in", "runtime_check_in",
    }
    unknown_match_keys = set(match) - allowed_match_keys
    if unknown_match_keys:
        raise AlignmentPolicyError(
            f"{label}.match has unsupported conditions: {sorted(unknown_match_keys)!r}"
        )
    if not match:
        raise AlignmentPolicyError(f"{label}.match must contain at least one condition")

    risk_flag = match.get("risk_flags_contains")
    if risk_flag is not None:
        if not isinstance(risk_flag, str) or risk_flag not in RISK_FLAGS:
            raise AlignmentPolicyError(f"{label}.match.risk_flags_contains is invalid")

    return AlignmentPolicyRule(
        rule_id=rule_id,
        review_category=review_category,
        reason_code=reason_code,
        alignment_state_in=(
            _parse_enum_list(match["alignment_state_in"], ALIGNMENT_STATES,
                             f"{label}.match.alignment_state_in")
            if "alignment_state_in" in match else ()
        ),
        risk_flags_contains=risk_flag,
        confidence_in=(
            _parse_enum_list(match["confidence_in"], CONFIDENCE_LEVELS,
                             f"{label}.match.confidence_in")
            if "confidence_in" in match else ()
        ),
        intent_field_in=(
            _parse_enum_list(match["intent_field_in"], INTENT_FIELDS,
                             f"{label}.match.intent_field_in")
            if "intent_field_in" in match else ()
        ),
        runtime_check_in=(
            _parse_enum_list(match["runtime_check_in"], RUNTIME_CHECK_STATES,
                             f"{label}.match.runtime_check_in")
            if "runtime_check_in" in match else ()
        ),
    )


def _validate_policy_coverage(policy: AlignmentReviewPolicy) -> None:
    """Reject a policy that leaves any validated finite input unclassified."""
    risk_combinations = [
        [flag for bit, flag in enumerate(RISK_FLAGS) if mask & (1 << bit)]
        for mask in range(1 << len(RISK_FLAGS))
    ]
    for state in ALIGNMENT_STATES:
        for risk_flags in risk_combinations:
            for confidence in CONFIDENCE_LEVELS:
                for intent_field in (None, *INTENT_FIELDS):
                    for runtime_check in (None, *RUNTIME_CHECK_STATES):
                        policy.classify(
                            alignment_state=state,
                            risk_flags=risk_flags,
                            confidence=confidence,
                            intent_field=intent_field,
                            runtime_check=runtime_check,
                        )


def load_alignment_review_policy(path: Optional[Path] = None) -> AlignmentReviewPolicy:
    """Load and strictly validate the external alignment review policy.

    There is intentionally no fallback path.  A missing or invalid policy
    prevents classification rather than silently reverting to embedded rules.
    """
    policy_path = path or _DEFAULT_POLICY_PATH
    try:
        raw_bytes = policy_path.read_bytes()
    except OSError as exc:
        raise AlignmentPolicyError(f"cannot read alignment review policy: {policy_path}") from exc
    try:
        raw = yaml.load(raw_bytes.decode("utf-8"), Loader=_UniqueKeySafeLoader)
    except (UnicodeDecodeError, yaml.YAMLError, AlignmentPolicyError) as exc:
        raise AlignmentPolicyError(f"invalid alignment review policy: {policy_path}: {exc}") from exc

    policy = _require_mapping(raw, "policy")
    _require_exact_keys(
        policy, {"schema_version", "policy_version", "reason_templates", "rules"}, "policy",
    )
    if policy["schema_version"] != POLICY_SCHEMA_VERSION:
        raise AlignmentPolicyError(
            f"policy.schema_version must be {POLICY_SCHEMA_VERSION!r}"
        )
    policy_version = _require_nonempty_string(policy["policy_version"], "policy.policy_version")
    templates = _require_mapping(policy["reason_templates"], "policy.reason_templates")
    if set(templates) != set(REASON_CODES):
        raise AlignmentPolicyError("policy.reason_templates must cover exactly every reason code")
    validated_templates = {
        reason_code: _require_nonempty_string(templates[reason_code], f"reason_templates.{reason_code}")
        for reason_code in REASON_CODES
    }
    raw_rules = policy["rules"]
    if not isinstance(raw_rules, list) or not raw_rules:
        raise AlignmentPolicyError("policy.rules must be a non-empty list")
    rules = tuple(_parse_rule(raw_rule, index) for index, raw_rule in enumerate(raw_rules))
    rule_ids = [rule.rule_id for rule in rules]
    if len(set(rule_ids)) != len(rule_ids):
        raise AlignmentPolicyError("policy.rules ids must be unique")

    result = AlignmentReviewPolicy(
        policy_version=policy_version,
        digest=hashlib.sha256(raw_bytes).hexdigest(),
        reason_templates=validated_templates,
        rules=rules,
    )
    _validate_policy_coverage(result)
    return result


_ALIGNMENT_REVIEW_POLICY = load_alignment_review_policy()
# Kept as a module-level mapping for existing callers/tests.  Its contents are
# loaded from the external policy and are never a code fallback.
USER_REASON_TEMPLATES: Dict[str, str] = dict(_ALIGNMENT_REVIEW_POLICY.reason_templates)


def alignment_policy_version() -> str:
    return _ALIGNMENT_REVIEW_POLICY.policy_version


def alignment_policy_digest() -> str:
    return _ALIGNMENT_REVIEW_POLICY.digest


def user_reason_for(reason_code: str) -> str:
    return USER_REASON_TEMPLATES[reason_code]


def classify_alignment_item(
    *,
    alignment_state: str,
    risk_flags: List[str],
    confidence: str,
    intent_field: Optional[str],
    runtime_check: Optional[str] = None,
) -> Tuple[str, str]:
    """Deterministic (review_category, reason_code) for one alignment item.

    Requires ``alignment_state`` in ``ALIGNMENT_STATES`` and ``confidence``
    in ``CONFIDENCE_LEVELS`` (callers validate this before classifying, see
    ``generate_alignment_proposal``). ``runtime_check`` (Issue #290), when
    given, must be one of ``runtime_alignment.RUNTIME_CHECK_STATES``; it is
    ``None`` whenever the item has no deterministic component mapping (the
    default, and the case for every pre-#290 caller/test). Every valid
    ``alignment_state`` is covered by exactly one terminal rule below
    (conflict / unknown / gap / aligned / not_applicable) even when
    ``runtime_check`` is ``None``, so the rule table is exhaustive for any
    already-validated input; the ``ValueError`` below only guards against a
    future rule-table edit accidentally leaving a state uncovered.
    """
    return _ALIGNMENT_REVIEW_POLICY.classify(
        alignment_state=alignment_state,
        risk_flags=risk_flags,
        confidence=confidence,
        intent_field=intent_field,
        runtime_check=runtime_check,
    )


def classify_alignment_item_with_rule(
    *,
    alignment_state: str,
    risk_flags: List[str],
    confidence: str,
    intent_field: Optional[str],
    runtime_check: Optional[str] = None,
) -> Tuple[str, str, str]:
    """Deterministically classify an item and expose the matched rule id."""
    return _ALIGNMENT_REVIEW_POLICY.classify_with_rule(
        alignment_state=alignment_state,
        risk_flags=risk_flags,
        confidence=confidence,
        intent_field=intent_field,
        runtime_check=runtime_check,
    )


def review_sort_key(*, review_category: str, reason_code: str, item_id: int) -> tuple:
    """Deterministic queue ordering: category rank, then reason rank, then id.

    No numeric score multiplication, no LLM-provided ordering (Principle 6).
    """
    return (_CATEGORY_RANK[review_category], _REASON_RANK[reason_code], item_id)


# --- Deterministic content hash for unchanged-item carry-over (Issue #295) --
#
# ``unchanged`` (Issue #287's reserved-but-unreachable review_category) is
# realized here: a rebuild that produces an item whose content_hash exactly
# matches a terminal (answered/corrected, non-superseded) row from the
# immediately preceding build carries that row's identity forward instead of
# re-running it through ``classify_alignment_item``'s rule table. This is an
# EXACT structural match, never a similarity/heuristic comparison (Principle
# 6) -- a single differing character anywhere in the hashed payload produces
# a completely different hash and the item is reclassified normally.
#
# The hashed payload is current_claim + normalized evidence (the item's
# "content", per the Issue #295 brief) plus every field that feeds
# classify_alignment_item's rule table (alignment_state / risk_flags /
# confidence / intent_field / runtime_check), so a change in
# classification-relevant state can never be masked as "unchanged" even when
# the claim text itself happens to repeat verbatim.
#
# Review fix (PR #296, Finding 1): the hash previously missed three
# meaning-bearing text fields (intent_summary / gap_summary /
# proposed_interpretation -- an LLM-authored interpretation can change while
# every other field stays byte-identical) and never checked whether the
# evidence *citations themselves* still point at the same source text -- a
# reference-only hash ("path"/"start_line"/"end_line"/"summary") would
# silently call a build "unchanged" even if the underlying lines had since
# been edited. Both gaps are closed here:
#
# - intent_summary/gap_summary/proposed_interpretation are now hashed inputs.
# - each evidence item's normalized dict now carries a ``source_digest``: the
#   sha256 of the EXACT lines ``start_line``..``end_line`` of ``path`` read
#   from the pinned snapshot commit (Principle 5 -- never the working tree).
#   Reading reuses the same deterministic, pinned-commit read path this
#   module already uses for evidence validation (``git_ops.read_file_at_commit``,
#   mirrored by the ``_read_lines`` helper below -- see
#   ``validate_evidence_against_snapshot``/``_line_count`` just above for the
#   sibling read used for line-count validation).
#
# Both changes are intentionally NOT migrated: existing content_hash values
# stop matching the new formula, which only ever causes an item to fall back
# to normal (re-)classification instead of carrying over -- the safe
# direction (Issue #295 brief). ``repo_path``/``commit_sha`` are required
# (not optional) so every call site is forced to compute a real
# source_digest; when any evidence item's source text cannot be read from
# the pinned commit, the WHOLE hash is ``None`` so the item can never be
# treated as a carry-over candidate now, nor match a future rebuild's
# candidate lookup (the caller's ``content_hash IS NOT NULL`` filter already
# excludes it) -- it is simply reclassified fresh through the normal rule
# table instead.
#
# Second review round (PR #296, 2nd pass, Finding 1): the hash still missed
# the Intent Brief entity an item is *linked to* -- only ``intent_field``
# (the field NAME, e.g. "pain") was hashed, never the linked
# ``interview_intent_item`` row's own content. A developer editing an Intent
# Brief field (correcting its value_text, or confirming/declining it) while
# the reasoning model happens to re-propose byte-identical claim/summary text
# for a dependent alignment item would previously carry that item over as
# "unchanged" even though the intent it is contrasted against had genuinely
# changed. Two new (optional, additive-in-signature) inputs close this:
#
# - ``intent_item_id``: the raw FK id of the linked ``interview_intent_item``
#   row (``None`` when an item has no linked intent). Issue #284's /correct
#   flow always mints a NEW id when value_text changes (the old row is
#   superseded, never edited in place), so an id change alone already
#   invalidates the hash for that case.
# - ``linked_intent_digest``: a sha256 (``compute_intent_item_digest`` below)
#   over the linked intent row's CURRENT ``field``/``value_text``/``status``,
#   computed by the caller (``run_alignment_build``) from a fresh DB read --
#   never computed in here, since this module has no DB access. This catches
#   the case /correct does not: /confirm and /decline update status IN PLACE
#   (same id, same value_text), so the id alone would not change.
#
# Issue #313 additionally includes the validated external policy digest. A
# policy edit must never leave a previous ``accept_current`` result hidden as
# ``unchanged`` under a different review policy; the digest therefore forces
# safe reclassification after any reviewed policy change.


def _read_lines(
    repo_path: str, commit_sha: str, path: str, cache: Dict[str, Optional[List[str]]],
) -> Optional[List[str]]:
    """Read ``path`` at the pinned commit, split into lines. ``None`` on any
    read failure (missing path, binary content) -- never partial/guessed."""
    if path in cache:
        return cache[path]
    try:
        raw = read_file_at_commit(repo_path, commit_sha, path)
    except GitError:
        cache[path] = None
        return None
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        cache[path] = None
        return None
    lines = text.splitlines()
    cache[path] = lines
    return lines


def _evidence_source_digest(
    repo_path: str,
    commit_sha: str,
    path: object,
    start_line: object,
    end_line: object,
    cache: Dict[str, Optional[List[str]]],
) -> Optional[str]:
    """sha256 of the exact ``start_line``..``end_line`` source text of ``path``
    at the pinned commit, or ``None`` if it cannot be read/validated."""
    if not isinstance(path, str) or not isinstance(start_line, int) or not isinstance(end_line, int):
        return None
    lines = _read_lines(repo_path, commit_sha, path, cache)
    if lines is None:
        return None
    if start_line < 1 or end_line < start_line or end_line > len(lines):
        return None
    text = "\n".join(lines[start_line - 1:end_line])
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def compute_intent_item_digest(*, field: str, value_text: str, status: str) -> str:
    """sha256 over a linked ``interview_intent_item`` row's current content.

    Callers (``run_alignment_build``) compute this from a fresh DB read of
    the linked intent row and pass the result into ``compute_content_hash``
    as ``linked_intent_digest`` -- this function itself never touches the
    database (Principle 6: a pure structural digest, not a decision).
    """
    payload = {"field": field, "value_text": value_text, "status": status}
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_content_hash(
    *,
    current_claim: str,
    current_evidence: List[Dict[str, object]],
    alignment_state: str,
    risk_flags: List[str],
    confidence: str,
    intent_field: Optional[str],
    runtime_check: Optional[str],
    policy_digest: str,
    repo_path: str,
    commit_sha: str,
    intent_summary: Optional[str] = None,
    gap_summary: Optional[str] = None,
    proposed_interpretation: Optional[str] = None,
    intent_item_id: Optional[int] = None,
    linked_intent_digest: Optional[str] = None,
    source_digest_cache: Optional[Dict[str, Optional[List[str]]]] = None,
) -> Optional[str]:
    """Deterministic sha256 over one alignment item's identity-bearing fields.

    Returns ``None`` (never a partial/best-effort hash) when any evidence
    item's source text cannot be read/validated at ``(repo_path, commit_sha)``
    -- see the module-level comment above for why that is the safe direction.

    ``intent_item_id``/``linked_intent_digest`` (2nd review round, Finding 1)
    make the hash sensitive to the linked Intent Brief entity's own identity
    and current content, not just the field name it belongs to -- see the
    module-level comment above. ``policy_digest`` makes a reviewed policy
    change invalidate carry-over instead of silently preserving an old
    classification.
    """
    cache: Dict[str, Optional[List[str]]] = (
        source_digest_cache if source_digest_cache is not None else {}
    )
    normalized_evidence = []
    for e in current_evidence:
        path = e.get("path")
        start_line = e.get("start_line")
        end_line = e.get("end_line")
        source_digest = _evidence_source_digest(repo_path, commit_sha, path, start_line, end_line, cache)
        if source_digest is None:
            return None
        normalized_evidence.append({
            "path": path,
            "start_line": start_line,
            "end_line": end_line,
            "summary": e.get("summary", ""),
            "source_digest": source_digest,
        })
    normalized_evidence.sort(
        key=lambda e: (e["path"], e["start_line"], e["end_line"], e["summary"], e["source_digest"]),
    )
    payload = {
        "current_claim": current_claim,
        "current_evidence": normalized_evidence,
        "alignment_state": alignment_state,
        "risk_flags": sorted(risk_flags),
        "confidence": confidence,
        "intent_field": intent_field,
        "runtime_check": runtime_check,
        "policy_digest": policy_digest,
        "intent_summary": intent_summary,
        "gap_summary": gap_summary,
        "proposed_interpretation": proposed_interpretation,
        "intent_item_id": intent_item_id,
        "linked_intent_digest": linked_intent_digest,
    }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- Raw LLM response schema (what we require the model to return) ----------


class _RawEvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1, max_length=500)
    start_line: int = Field(..., ge=1)
    end_line: int = Field(..., ge=1)
    summary: str = Field(default="", max_length=1_000)


class _RawAlignmentItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_field: Optional[str] = Field(default=None, max_length=50)
    intent_ref_hint: Optional[str] = Field(default=None, max_length=500)
    current_claim: str = Field(..., min_length=1, max_length=2_000)
    evidence: List[_RawEvidenceItem] = Field(default_factory=list, max_length=10)
    alignment_state: str = Field(..., min_length=1, max_length=30)
    risk_flags: List[str] = Field(default_factory=list, max_length=5)
    confidence: str = Field(..., min_length=1, max_length=20)
    gap_summary: Optional[str] = Field(default=None, max_length=1_000)
    proposed_interpretation: Optional[str] = Field(default=None, max_length=1_000)


class _RawAlignmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: List[_RawAlignmentItem] = Field(default_factory=list, max_length=40)


# --- Public result types ------------------------------------------------------


@dataclass
class AlignmentEvidenceItem:
    path: str
    start_line: int
    end_line: int
    summary: str = ""


@dataclass
class AlignmentProposalItem:
    intent_field: Optional[str]
    intent_ref_hint: Optional[str]
    current_claim: str
    evidence: List[AlignmentEvidenceItem]
    alignment_state: str
    risk_flags: List[str]
    confidence: str
    gap_summary: Optional[str] = None
    proposed_interpretation: Optional[str] = None


@dataclass
class AlignmentProposalResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    items: List[AlignmentProposalItem] = field(default_factory=list)
    error: Optional[str] = None


def _strip_fences(raw: str) -> str:
    cleaned = raw.strip()
    if not cleaned.startswith("```"):
        return cleaned
    lines = cleaned.split("\n")
    lines = lines[1:] if lines[0].startswith("```") else lines
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines)


_SYSTEM_PROMPT = """\
You are the Alignment Reviewer of probe-agent's system-understanding \
Interview flow. You are given the developer's confirmed/proposed Intent \
Brief (goal / pain / success_criteria / priority / constraints / non_goals) \
and the evidence-backed Current System understanding (already derived from \
the pinned source snapshot). Contrast them and propose "alignment items": \
points where the current system's behavior aligns with, deviates from, or \
says nothing about the developer's intent.

Respond with a single JSON object and nothing else (no markdown fences, no \
commentary), matching exactly this shape:

{
  "items": [
    {
      "intent_field": "goal | pain | success_criteria | priority | constraints | non_goals | null",
      "intent_ref_hint": "a short paraphrase of the related Intent Brief text, or null if none",
      "current_claim": "the Current System claim this item is about",
      "evidence": [
        {"path": "src/module.py", "start_line": 1, "end_line": 20, "summary": "what this shows"}
      ],
      "alignment_state": "aligned | gap | unknown | conflict | not_applicable",
      "risk_flags": ["security" | "high_risk" | "core_intent", ...],
      "confidence": "confirmed | likely | uncertain | conflicting",
      "gap_summary": "what is missing or different, or null",
      "proposed_interpretation": "a short suggested resolution, or null"
    }
  ]
}

Rules:
- Only cite "path"/"start_line"/"end_line" that appear verbatim in the \
evidence already attached to a Current System understanding item supplied \
below. Never invent a path or a line range.
- "risk_flags" must only contain values from the finite set shown above (it \
may be empty). Only include "security" when the claim concerns \
authentication/authorization/secrets/data protection, "high_risk" when the \
claim concerns payment/irreversible side effects/production data \
mutation, and "core_intent" when the claim is central to the developer's \
stated goal.
- "alignment_state": "aligned" when the current system matches the intent; \
"gap" when the intent is not (yet) reflected in the current system; \
"conflict" when the current system contradicts the intent; "unknown" when \
there is not enough evidence to tell; "not_applicable" for a Current System \
claim with no related intent (informational only).
- "intent_field" identifies which Intent Brief field this item relates to, \
or null when the claim is purely about the current system with no related \
intent (typically paired with alignment_state "not_applicable").
- Do not propose more than one item per distinct current-system claim.
- Never decide, adopt, or apply anything yourself here; you only propose \
content for a human to review.
"""


def _build_user_prompt(
    intent_items: List[Dict[str, object]],
    current_understanding: Optional[Dict[str, object]],
    gap_analysis: Optional[List[Dict[str, object]]],
) -> str:
    parts = [
        "## Intent Brief (current, non-superseded items; only the user can decide these)",
        json.dumps(intent_items, ensure_ascii=False),
        "\n## Current System understanding (evidence-backed, from the latest build)",
        json.dumps(current_understanding or {}, ensure_ascii=False),
        "\n## Known gaps (docs/code reconciliation)",
        json.dumps(gap_analysis or [], ensure_ascii=False),
    ]
    return "\n".join(parts)


def generate_alignment_proposal(
    client: LLMClient,
    config: LLMConfig,
    *,
    intent_items: List[Dict[str, object]],
    current_understanding: Optional[Dict[str, object]],
    gap_analysis: Optional[List[Dict[str, object]]],
) -> AlignmentProposalResult:
    """Propose alignment items contrasting Intent vs Current System.

    Fail-closed (Principle 6): a mock client, a non-reasoning model, an API
    failure, a structured-output validation failure, or any item field
    outside its finite set (alignment_state / confidence / risk_flags /
    intent_field) all return a result with ``error`` set and no items --
    callers must not persist a partial/best-effort batch.
    """
    is_mock = isinstance(client, MockLLMClient)
    if is_mock or not is_reasoning_model(config.provider, config.model):
        return AlignmentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=is_mock,
            error=(
                "Alignment build requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
        )

    prompt = _build_user_prompt(intent_items, current_understanding, gap_analysis)

    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=4096,
        )
    except LLMError as exc:
        return AlignmentProposalResult(
            provider=config.provider, model=config.model, is_mock=False, error=str(exc),
        )

    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawAlignmentResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return AlignmentProposalResult(
            provider=config.provider,
            model=config.model,
            is_mock=False,
            error=f"Failed to parse structured response: {exc}",
        )

    items: List[AlignmentProposalItem] = []
    for raw_item in validated.items:
        if raw_item.alignment_state not in ALIGNMENT_STATES:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned an invalid alignment_state: {raw_item.alignment_state!r}",
            )
        if raw_item.confidence not in CONFIDENCE_LEVELS:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned an invalid confidence: {raw_item.confidence!r}",
            )
        invalid_flags = [f for f in raw_item.risk_flags if f not in RISK_FLAGS]
        if invalid_flags:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned invalid risk_flags: {invalid_flags!r}",
            )
        if raw_item.intent_field is not None and raw_item.intent_field not in INTENT_FIELDS:
            return AlignmentProposalResult(
                provider=config.provider, model=config.model, is_mock=False,
                error=f"Model returned an invalid intent_field: {raw_item.intent_field!r}",
            )
        items.append(
            AlignmentProposalItem(
                intent_field=raw_item.intent_field,
                intent_ref_hint=raw_item.intent_ref_hint,
                current_claim=raw_item.current_claim,
                evidence=[
                    AlignmentEvidenceItem(
                        path=e.path, start_line=e.start_line, end_line=e.end_line,
                        summary=e.summary,
                    )
                    for e in raw_item.evidence
                ],
                alignment_state=raw_item.alignment_state,
                risk_flags=list(raw_item.risk_flags),
                confidence=raw_item.confidence,
                gap_summary=raw_item.gap_summary,
                proposed_interpretation=raw_item.proposed_interpretation,
            )
        )

    return AlignmentProposalResult(
        provider=config.provider, model=config.model, is_mock=False, items=items,
    )


# --- Deterministic evidence validation against the pinned snapshot ----------
#
# Mirrors investigation_agent's evidence check (Principle 5/6): a citation
# is verifiable only if its path exists in the pinned commit and its line
# range fits inside that file's actual line count. Never a reasoning
# decision. ``line_count_cache`` lets a caller amortize repeated paths
# across many items in one build.


def _line_count(
    repo_path: str, commit_sha: str, path: str, cache: Dict[str, Optional[int]],
) -> Optional[int]:
    if path in cache:
        return cache[path]
    try:
        raw = read_file_at_commit(repo_path, commit_sha, path)
    except GitError:
        cache[path] = None
        return None
    text = raw.decode("utf-8", errors="replace")
    if "\x00" in text:
        cache[path] = None
        return None
    count = len(text.splitlines())
    cache[path] = count
    return count


def validate_evidence_against_snapshot(
    repo_path: str,
    commit_sha: str,
    evidence: List[AlignmentEvidenceItem],
    line_count_cache: Optional[Dict[str, Optional[int]]] = None,
) -> Tuple[List[AlignmentEvidenceItem], List[Dict[str, object]]]:
    """Split ``evidence`` into (valid, pruned) against the pinned snapshot.

    A citation is valid when its path exists in the pinned commit and
    ``1 <= start_line <= end_line <= <file's line count>``. Invalid
    citations are pruned (returned, not raised) so the caller can decide
    whether the owning item still has enough evidence to keep.
    """
    cache: Dict[str, Optional[int]] = line_count_cache if line_count_cache is not None else {}
    valid: List[AlignmentEvidenceItem] = []
    pruned: List[Dict[str, object]] = []
    for item in evidence:
        total = _line_count(repo_path, commit_sha, item.path, cache)
        ok = (
            total is not None
            and item.start_line >= 1
            and item.end_line >= item.start_line
            and item.end_line <= total
        )
        if ok:
            valid.append(item)
        else:
            pruned.append({"path": item.path, "start_line": item.start_line, "end_line": item.end_line})
    return valid, pruned

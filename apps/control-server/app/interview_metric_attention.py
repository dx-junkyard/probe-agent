"""Deterministic 要確認 (attention) evaluation for Interview UX metrics (Issue #341).

Issue #309 gave every metric a ``guardrail`` flag, but that flag only records
*which metrics are worth watching*; it says nothing about whether a metric is
currently in a bad state.  This module keeps those two ideas separate:

* ``InterviewMetricOut.guardrail``  -- the metric's designation (unchanged).
* ``InterviewMetricOut.attention``  -- the evaluated judgement for one System.

The judgement comes from an external, reviewable YAML artifact
(``app/policies/interview_metric_attention.yaml``) so the criteria can be
changed and audited without editing application code, exactly like the Issue
#313 alignment review policy.  The loader accepts only a finite vocabulary and
fails closed: a missing, malformed, or incomplete policy prevents startup
rather than silently reverting to a permissive default (Principle 6).

Deliberate limits of the v1 vocabulary -- each of these needs persisted
evaluation history or a scheduled evaluator, neither of which exists, and
neither of which #341 introduces:

* ``window`` accepts only ``all_time``.  Every Issue #309/#334 aggregate is an
  all-history count; a bounded window would have to be applied inside the
  aggregation, not bolted onto the judgement.
* ``trigger`` accepts only ``single_breach``.  "点灯は継続時のみ" would need
  consecutive evaluations, and this module is evaluated on demand by a GET --
  counting page loads as observations would be meaningless.
* ``clear`` accepts only ``value_within_threshold``.  A manual
  「確認済み」 acknowledgement would need its own persisted state.

A value that is absent is never treated as zero: an unmeasured metric can only
become ``insufficient_data`` or ``not_measurable``, never ``ok`` and never
``attention``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple

import yaml

from . import policy_loader
from .models import (
    ATTENTION_CLEAR_CONDITIONS,
    ATTENTION_DIRECTIONS,
    ATTENTION_TRIGGERS,
    ATTENTION_WINDOWS,
    INTERVIEW_METRIC_KEYS,
    InterviewMetricAttentionOut,
    InterviewMetricOut,
    InterviewMetricsAttentionSummaryOut,
)

POLICY_SCHEMA_VERSION = "interview-metric-attention-policy-v1"
_DEFAULT_POLICY_PATH = (
    Path(__file__).with_name("policies") / "interview_metric_attention.yaml"
)

# ``_measured`` degrades to this reason when a denominator is zero, i.e. the
# metric is calculable but nothing has been observed yet.  Every other
# ``unmeasured_reason`` in ``interview_metrics.py`` means the underlying fact is
# not recorded at all, which no amount of further usage will fix.
_NO_DATA_YET_REASONS = frozenset({"no_observations"})


class InterviewMetricAttentionPolicyError(ValueError):
    """The attention policy is missing or unsafe to use.

    Callers must not substitute a default policy: an unreadable policy means
    the 要確認 判定 cannot be trusted, so startup fails instead.
    """


@dataclass(frozen=True)
class MetricAttentionRule:
    """The attention criterion for exactly one metric key."""

    key: str
    watch: bool
    direction: Optional[str] = None
    threshold: Optional[float] = None
    min_sample: int = 0
    window: Optional[str] = None
    trigger: Optional[str] = None
    clear: Optional[str] = None


@dataclass(frozen=True)
class InterviewMetricAttentionPolicy:
    policy_version: str
    digest: str
    rules: Mapping[str, MetricAttentionRule]

    def rule_for(self, key: str) -> MetricAttentionRule:
        try:
            return self.rules[key]
        except KeyError:  # pragma: no cover - load-time coverage check prevents this
            raise InterviewMetricAttentionPolicyError(
                f"attention policy has no entry for metric {key!r}"
            ) from None


_WATCHED_KEYS = {"key", "watch", "direction", "threshold", "min_sample", "window", "trigger", "clear"}
_UNWATCHED_KEYS = {"key", "watch"}


def _parse_rule(raw_rule: Any, index: int) -> MetricAttentionRule:
    label = f"metrics[{index}]"
    rule = policy_loader.require_mapping(raw_rule, label, InterviewMetricAttentionPolicyError)
    key = policy_loader.require_enum(
        rule.get("key"), INTERVIEW_METRIC_KEYS, f"{label}.key",
        InterviewMetricAttentionPolicyError,
    )
    watch = rule.get("watch")
    if not isinstance(watch, bool):
        raise InterviewMetricAttentionPolicyError(f"{label}.watch must be a boolean")

    if not watch:
        # A 定期観測 metric carries no criterion at all, so unused fields are
        # rejected rather than silently ignored.
        policy_loader.require_exact_keys(
            rule, _UNWATCHED_KEYS, label, InterviewMetricAttentionPolicyError,
        )
        return MetricAttentionRule(key=key, watch=False)

    policy_loader.require_exact_keys(
        rule, _WATCHED_KEYS, label, InterviewMetricAttentionPolicyError,
    )
    threshold = rule["threshold"]
    if threshold is not None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
            raise InterviewMetricAttentionPolicyError(
                f"{label}.threshold must be a number or null"
            )
        threshold = float(threshold)
    min_sample = rule["min_sample"]
    if isinstance(min_sample, bool) or not isinstance(min_sample, int) or min_sample < 1:
        raise InterviewMetricAttentionPolicyError(
            f"{label}.min_sample must be an integer >= 1"
        )
    return MetricAttentionRule(
        key=key,
        watch=True,
        direction=policy_loader.require_enum(
            rule["direction"], ATTENTION_DIRECTIONS, f"{label}.direction",
            InterviewMetricAttentionPolicyError,
        ),
        threshold=threshold,
        min_sample=min_sample,
        window=policy_loader.require_enum(
            rule["window"], ATTENTION_WINDOWS, f"{label}.window",
            InterviewMetricAttentionPolicyError,
        ),
        trigger=policy_loader.require_enum(
            rule["trigger"], ATTENTION_TRIGGERS, f"{label}.trigger",
            InterviewMetricAttentionPolicyError,
        ),
        clear=policy_loader.require_enum(
            rule["clear"], ATTENTION_CLEAR_CONDITIONS, f"{label}.clear",
            InterviewMetricAttentionPolicyError,
        ),
    )


def load_interview_metric_attention_policy(
    path: Optional[Path] = None,
) -> InterviewMetricAttentionPolicy:
    """Load and strictly validate the external attention policy.

    There is intentionally no fallback: an unreadable or incomplete policy
    raises instead of leaving some metrics unjudged.
    """
    policy_path = path or _DEFAULT_POLICY_PATH
    try:
        raw_bytes = policy_path.read_bytes()
    except OSError as exc:
        raise InterviewMetricAttentionPolicyError(
            f"cannot read interview metric attention policy: {policy_path}"
        ) from exc
    try:
        raw = yaml.load(
            raw_bytes.decode("utf-8"), Loader=policy_loader.UniqueKeySafeLoader,
        )
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise InterviewMetricAttentionPolicyError(
            f"invalid interview metric attention policy: {policy_path}: {exc}"
        ) from exc

    policy = policy_loader.require_mapping(raw, "policy", InterviewMetricAttentionPolicyError)
    policy_loader.require_exact_keys(
        policy, {"schema_version", "policy_version", "metrics"}, "policy",
        InterviewMetricAttentionPolicyError,
    )
    if policy["schema_version"] != POLICY_SCHEMA_VERSION:
        raise InterviewMetricAttentionPolicyError(
            f"policy.schema_version must be {POLICY_SCHEMA_VERSION!r}"
        )
    policy_version = policy_loader.require_nonempty_string(
        policy["policy_version"], "policy.policy_version",
        InterviewMetricAttentionPolicyError,
    )
    raw_metrics = policy["metrics"]
    if not isinstance(raw_metrics, list) or not raw_metrics:
        raise InterviewMetricAttentionPolicyError("policy.metrics must be a non-empty list")
    rules = [_parse_rule(raw_rule, index) for index, raw_rule in enumerate(raw_metrics)]

    by_key = {rule.key: rule for rule in rules}
    if len(by_key) != len(rules):
        raise InterviewMetricAttentionPolicyError("policy.metrics keys must be unique")
    # Terminal coverage: no metric may be left without an explicit decision on
    # whether it can light the entry point.
    missing = sorted(set(INTERVIEW_METRIC_KEYS) - set(by_key))
    if missing:
        raise InterviewMetricAttentionPolicyError(
            f"policy.metrics must cover every metric key; missing={missing!r}"
        )

    return InterviewMetricAttentionPolicy(
        policy_version=policy_version,
        digest=hashlib.sha256(raw_bytes).hexdigest(),
        rules=by_key,
    )


# Loaded once at import so a broken policy fails the server start, not the
# first dashboard request.
_ATTENTION_POLICY = load_interview_metric_attention_policy()


def attention_policy_version() -> str:
    return _ATTENTION_POLICY.policy_version


def attention_policy_digest() -> str:
    return _ATTENTION_POLICY.digest


def evaluate_metric_attention(
    metric: InterviewMetricOut,
    rule: MetricAttentionRule,
) -> InterviewMetricAttentionOut:
    """Judge one metric against its criterion.

    Only a measured value with a sufficient sample can ever reach ``ok`` or
    ``attention``; a missing value stays visibly missing.
    """
    if not rule.watch:
        return InterviewMetricAttentionOut(
            state="observation_only", watched=False, reason="not_a_notification_target",
        )

    common = {
        "watched": True,
        "direction": rule.direction,
        "threshold": rule.threshold,
        "min_sample": rule.min_sample,
        "window": rule.window,
        "trigger": rule.trigger,
        "clear_condition": rule.clear,
    }

    if metric.status != "measured" or metric.value is None:
        no_data_yet = (metric.unmeasured_reason or "") in _NO_DATA_YET_REASONS
        return InterviewMetricAttentionOut(
            state="insufficient_data" if no_data_yet else "not_measurable",
            reason="no_observations_yet" if no_data_yet else "not_recorded",
            **common,
        )
    if metric.sample_size < rule.min_sample:
        return InterviewMetricAttentionOut(
            state="insufficient_data", reason="sample_below_minimum", **common,
        )
    if rule.threshold is None:
        # 監視対象ではあるが、閾値の数値は #341 の対象外として未設定。
        return InterviewMetricAttentionOut(
            state="criterion_unset", reason="threshold_not_configured", **common,
        )
    breached = (
        metric.value > rule.threshold
        if rule.direction == "high_is_bad"
        else metric.value < rule.threshold
    )
    return InterviewMetricAttentionOut(
        state="attention" if breached else "ok",
        reason="threshold_breached" if breached else "within_threshold",
        **common,
    )


def apply_attention(
    metrics: Sequence[InterviewMetricOut],
    *,
    policy: Optional[InterviewMetricAttentionPolicy] = None,
) -> Tuple[Sequence[InterviewMetricOut], InterviewMetricsAttentionSummaryOut]:
    """Attach an attention judgement to every metric and summarise the entry point.

    ``watch: true`` is only meaningful for a designated guardrail; a policy that
    tries to make a 定期観測 metric light the entry point is a configuration
    error, not something to silently honour.
    """
    active = policy or _ATTENTION_POLICY
    attention_count = 0
    insufficient_count = 0
    not_measurable_count = 0
    watched_count = 0

    for metric in metrics:
        rule = active.rule_for(metric.key)
        if rule.watch and not metric.guardrail:
            raise InterviewMetricAttentionPolicyError(
                f"metric {metric.key!r} is watched by the attention policy but is "
                "not designated as a guardrail"
            )
        metric.attention = evaluate_metric_attention(metric, rule)
        if not rule.watch:
            continue
        watched_count += 1
        if metric.attention.state == "attention":
            attention_count += 1
        elif metric.attention.state == "insufficient_data":
            insufficient_count += 1
        elif metric.attention.state == "not_measurable":
            not_measurable_count += 1

    if attention_count:
        state = "attention"
    elif insufficient_count:
        state = "insufficient_data"
    else:
        state = "normal"

    return metrics, InterviewMetricsAttentionSummaryOut(
        state=state,
        attention_count=attention_count,
        insufficient_data_count=insufficient_count,
        not_measurable_count=not_measurable_count,
        watched_count=watched_count,
        policy_version=active.policy_version,
        policy_digest=active.digest,
    )

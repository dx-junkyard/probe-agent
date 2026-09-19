"""Issue #459 §9.2: the semantic half of the Gap <-> Purpose/UX/Feature
matching operation (docs/01-specifications/ux/decision-discussion-workflow.md's 「照合結果」 stage).

`discussion_context_bundle.py` (#458) builds the bounded, budgeted context
bundle and owns the citation allow-list (`allowed_source_ids` /
`validate_citation_source_ids`) and the finite claim-kind vocabulary
(`DISCUSSION_CONTEXT_CLAIM_KINDS`). That module deliberately calls no LLM --
this module is the ONLY caller that turns a bundle into
`fact`/`inference`/`hypothesis`/`unknown`/`conflict` claims.

Two layers, matching ai-discussion-adapter.md §9.2's own split:

- STRUCTURAL absence is derived DETERMINISTICALLY, straight from a bundle
  section's own `operation_state` (`unsupported` = no relation extractor is
  registered for this target_kind at all; `not_applicable` = this root has no
  seed for this section; `unavailable` = a registered read failed just now).
  Principle 6 forbids asking a reasoning model to re-guess something a
  finite, already-computed field already answers -- and DD-CTX-04 requires
  telling "relation未登録" and "取得失敗" apart from a semantic "evidence不足",
  which only the deterministic pass can do honestly (the bundle already made
  that distinction; a claim built by the LLM might not preserve it).
- The SEMANTIC question -- "given what WAS actually read, does the evidence
  support / leave open / contradict this Gap's premise" -- goes to the
  reasoning model, and ONLY when at least one section actually has facts to
  reason over. No available facts anywhere means nothing to interpret, so no
  LLM call is made at all (`decision_method="deterministic"` for the whole
  result) -- calling a reasoning model to restate "there is nothing to read"
  would be exactly the kind of decision Principle 6 reserves for a finite,
  already-known answer.

Fail-closed throughout (Principle 6): a mock/non-reasoning client, a call
error, or invalid structured output fail the whole semantic pass (the
deterministic claims, if any, are still returned -- DD-UX-07's "読めた範囲を
残す"). An LLM response citing a source id outside
`discussion_context_bundle.allowed_source_ids(bundle)` -- on ANY claim, not
only a distinguished "primary" subset, which would need a second undocumented
vocabulary this module does not introduce -- triggers exactly ONE
regeneration attempt (two attempts total); a second invalid response fails
explicitly (`error_kind="invalid_citation"`). Never an unlimited retry loop,
never a heuristic partial success standing in for a real answer.

No database access anywhere in this module (mirrors
`assistant_discussion_proposal.generate_proposal`'s own contract) -- callers
hold no `get_conn()` connection while `generate_context_claims` runs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from pydantic import BaseModel, ConfigDict, Field as PydanticField, ValidationError

from .discussion_context_bundle import (
    DISCUSSION_CONTEXT_CLAIM_KINDS,
    DiscussionContextBundle,
    allowed_source_ids,
    validate_citation_source_ids,
)
from .llm import LLMClient, LLMConfig, LLMError, MockLLMClient, is_reasoning_model

PROMPT_VERSION = "discussion-context-claim-v1"
SCHEMA_VERSION = "discussion-context-claim-v1"

#: Basis of one claim -- never a THIRD, hand-invented vocabulary: every claim
#: this module ever returns is either read straight off a bundle field
#: (`"deterministic"`) or the reasoning model's own structured output
#: (`"reasoning_llm"`), matching `models.DecisionMethod`'s existing pair.
CLAIM_BASIS_VALUES: Tuple[str, ...] = ("deterministic", "reasoning_llm")

_MAX_REGENERATION_ATTEMPTS = 2  # the original attempt + exactly one retry


@dataclass(frozen=True)
class DiscussionClaim:
    kind: str  # one of DISCUSSION_CONTEXT_CLAIM_KINDS
    statement: str
    cited_source_ids: Tuple[str, ...]
    basis: str  # one of CLAIM_BASIS_VALUES


@dataclass
class ClaimsGenerationResult:
    provider: str
    model: str
    is_mock: bool
    prompt_version: str = PROMPT_VERSION
    schema_version: str = SCHEMA_VERSION
    #: The run's OWN decision_method (Principle 7): "deterministic" when no
    #: semantic section had anything to reason over (no LLM call was made at
    #: all), "reasoning_llm" whenever a call was attempted -- regardless of
    #: whether it ultimately succeeded, matching `discussion_proposal`'s own
    #: convention of recording the attempted method even on failure.
    decision_method: str = "deterministic"
    claims: List[DiscussionClaim] = field(default_factory=list)
    #: DD-CTX-04's "確認範囲...を照合結果に付ける" -- a short, human-readable
    #: summary of what was actually read (per-section counts/completeness),
    #: built deterministically from the bundle, never invented by the model.
    scope_note: str = ""
    #: DD-CTX-04's "時点" -- the pinned snapshot's commit this bundle read
    #: from, or `None` when the System has no ready snapshot.
    as_of_snapshot_commit: Optional[str] = None
    retried: bool = False
    #: One of "unavailable" (no usable reasoning-model client), "call_error"
    #: (a real attempt failed), "invalid_response" (structured output did not
    #: parse/validate), "invalid_citation" (parsed but cited an id outside
    #: the bundle's allow-list even after one retry), or `None` (success).
    error: Optional[str] = None
    error_kind: Optional[str] = None


class _RawClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    statement: str = PydanticField(..., max_length=2000)
    cited_source_ids: List[str] = PydanticField(default_factory=list, max_length=20)


class _RawClaimsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: List[_RawClaim] = PydanticField(..., min_length=1, max_length=20)
    scope_note: str = PydanticField(default="", max_length=1000)


def _strip_fences(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


# --- deterministic pass -------------------------------------------------------


def _root_source_id(bundle: DiscussionContextBundle) -> str:
    return f"{bundle.root.target_kind}:{bundle.root.target_ref}"


#: DD-CTX-04's structural row (relation未登録 / unavailable / 省略) mapped to a
#: fixed Japanese sentence and the claim kind that row's language implies
#: ("unknown" throughout -- a structural gap is never itself a `fact` about
#: the SUBJECT, only about what could be checked).
_DETERMINISTIC_SECTION_MESSAGE = {
    "unsupported": "この対象について、この関連の種類は正本に登録されていません(構造的な未登録)。関連の対応関係を別途確認してください。",
    "not_applicable": "この対象にはこの区分の関連情報がありません。",
    "unavailable": "この範囲は取得に失敗し、確認できていません。再取得を試してください。",
}


def _deterministic_claims_for_bundle(bundle: DiscussionContextBundle) -> List[DiscussionClaim]:
    root_source_id = _root_source_id(bundle)
    claims: List[DiscussionClaim] = []
    for section in bundle.sections:
        message = _DETERMINISTIC_SECTION_MESSAGE.get(section.operation_state)
        if message is None:
            continue
        claims.append(
            DiscussionClaim(
                kind="unknown",
                statement=f"「{section.section_id}」: {message}",
                cited_source_ids=(root_source_id,),
                basis="deterministic",
            )
        )
    return claims


def _has_semantic_material(bundle: DiscussionContextBundle) -> bool:
    """True when at least one section was actually read and returned
    something to interpret -- an `available` section with zero facts still
    counts (an empty related-set IS itself evidence, per DD-CTX-04: "取得
    結果だけで機能不存在を確定しない" -- deciding what an empty read MEANS is
    exactly the semantic judgement reserved for the reasoning model, never a
    deterministic "0 items -> nothing to say")."""
    return any(section.operation_state == "available" for section in bundle.sections)


def _scope_note(bundle: DiscussionContextBundle) -> str:
    parts: List[str] = []
    for section in bundle.sections:
        if section.operation_state == "available":
            total = section.coverage.total_count
            total_text = str(total) if total is not None else "不明"
            parts.append(
                f"{section.section_id}: {section.coverage.returned_count}/{total_text}件"
                f"({section.coverage.completeness})"
            )
        else:
            parts.append(f"{section.section_id}: {section.operation_state}")
    return "; ".join(parts)


# --- semantic pass (LLM) -------------------------------------------------------

_SYSTEM_PROMPT = """あなたはソフトウェアの Gap(現状と目標状態の差)が、Vision/UX/機能のどこに
実際に対応しているかを、与えられた根拠だけから照合するアシスタントです。

出力は次の JSON のみ(コードフェンス無し):
{"claims": [{"kind": "fact"|"inference"|"hypothesis"|"unknown"|"conflict",
             "statement": "...", "cited_source_ids": ["..."]}, ...],
 "scope_note": "..."}

厳守事項:
- kind は次の意味で使い分けること。
  fact: 提示された根拠に直接書かれている事実。
  inference: 複数の根拠から論理的に導ける推論。
  hypothesis: 根拠だけでは確定できない仮の説明(反証条件は別ステップで扱うため
    ここでは述べなくてよい)。
  unknown: 確認した範囲では判断できない、または確認できていない。
  conflict: 期待(目標状態)と観察された事実が食い違う。
- 証拠が見つからないことだけを理由に「機能が存在しない」と断定してはいけない。
  常に unknown を使うこと。
- 各 claim は cited_source_ids に、渡された allowed_source_ids に含まれる
  source_id を **最低 1 つ** 含めること。存在しない id を作ってはいけない。
- 与えられた sections のうち operation_state が "available" のものだけを
  解釈対象とすること。それ以外は既に決定的に扱われているので無視してよい。
- claims は 1 件以上、10 件以内。
"""


def _build_prompt(bundle: DiscussionContextBundle, question: str, *, retry_note: str = "") -> str:
    sources_catalog = [
        {
            "source_id": source.source_id,
            "target_kind": source.target_kind,
            "target_ref": source.target_ref,
            "freshness": source.freshness,
        }
        for source in bundle.sources
    ]
    sections_payload = [
        {
            "section_id": section.section_id,
            "operation_state": section.operation_state,
            "coverage": {
                "returned_count": section.coverage.returned_count,
                "total_count": section.coverage.total_count,
                "completeness": section.coverage.completeness,
            },
            "facts": [
                {
                    "source_id": f"{entry.target_kind}:{entry.target_ref}",
                    "target_kind": entry.target_kind,
                    "target_ref": entry.target_ref,
                    "title": entry.title,
                    "resolution": entry.resolution,
                    "truncated": entry.truncated,
                    "facts": entry.facts,
                }
                for entry in section.facts
                if section.operation_state == "available"
            ],
        }
        for section in bundle.sections
    ]
    parts = [
        f"root: {bundle.root.target_kind}:{bundle.root.target_ref}",
        f"question: {question}",
        f"allowed_source_ids: {json.dumps(sorted(s['source_id'] for s in sources_catalog))}",
        f"sources: {json.dumps(sources_catalog, ensure_ascii=False)}",
        f"sections: {json.dumps(sections_payload, ensure_ascii=False)}",
    ]
    if retry_note:
        parts.append(retry_note)
    return "\n\n".join(parts)


def _call_and_parse(
    client: LLMClient, prompt: str,
) -> Tuple[Optional[_RawClaimsResponse], Optional[str], Optional[str]]:
    try:
        raw = client.generate_text(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=2000,
        )
    except LLMError as exc:
        return None, str(exc), "call_error"
    try:
        parsed = json.loads(_strip_fences(raw))
        validated = _RawClaimsResponse.model_validate(parsed)
    except (json.JSONDecodeError, ValidationError) as exc:
        return None, f"Failed to parse structured response: {exc}", "invalid_response"
    return validated, None, None


def _validate_claims(
    validated: _RawClaimsResponse, bundle: DiscussionContextBundle,
) -> Tuple[List[DiscussionClaim], List[str]]:
    """`(claims, invalid_reasons)`. A non-empty second element means the
    WHOLE response is rejected -- an unknown `kind` or an out-of-allow-list
    citation on any single claim invalidates the response, never just that
    one claim (Principle 6: a partially-invented answer is not a
    partially-correct one)."""
    claims: List[DiscussionClaim] = []
    invalid: List[str] = []
    for raw_claim in validated.claims:
        if raw_claim.kind not in DISCUSSION_CONTEXT_CLAIM_KINDS:
            invalid.append(f"unknown claim kind: {raw_claim.kind!r}")
            continue
        ok, invalid_ids = validate_citation_source_ids(raw_claim.cited_source_ids, bundle)
        if not ok:
            invalid.append(f"{raw_claim.kind}: invalid or missing citations {invalid_ids!r}")
            continue
        claims.append(
            DiscussionClaim(
                kind=raw_claim.kind, statement=raw_claim.statement,
                cited_source_ids=tuple(raw_claim.cited_source_ids), basis="reasoning_llm",
            )
        )
    return claims, invalid


def generate_context_claims(
    client: Optional[LLMClient], config: LLMConfig, *,
    bundle: DiscussionContextBundle, question: str,
) -> ClaimsGenerationResult:
    """§9.2's full照合 step. Pure -- no database access.

    Always includes the deterministic structural claims (if any), even when
    the semantic pass below is skipped or fails -- DD-UX-07's "読めた範囲を
    残す" applies here exactly as it does to a partial context fetch.
    """
    is_mock = config.provider == "mock" or isinstance(client, MockLLMClient)
    deterministic_claims = _deterministic_claims_for_bundle(bundle)
    scope_note = _scope_note(bundle)
    as_of = bundle.snapshot.commit_sha

    if not _has_semantic_material(bundle):
        return ClaimsGenerationResult(
            provider=config.provider, model=config.model, is_mock=is_mock,
            decision_method="deterministic", claims=deterministic_claims,
            scope_note=scope_note, as_of_snapshot_commit=as_of,
        )

    if client is None or is_mock or not is_reasoning_model(config.provider, config.model):
        return ClaimsGenerationResult(
            provider=config.provider, model=config.model, is_mock=is_mock,
            decision_method="reasoning_llm", claims=deterministic_claims,
            scope_note=scope_note, as_of_snapshot_commit=as_of,
            error=(
                "Context matching requires a configured reasoning model; "
                "mock/heuristic fallback is prohibited"
            ),
            error_kind="unavailable",
        )

    retry_note = ""
    last_error: Optional[str] = None
    last_error_kind: Optional[str] = None
    for attempt in range(1, _MAX_REGENERATION_ATTEMPTS + 1):
        prompt = _build_prompt(bundle, question, retry_note=retry_note)
        validated, call_error, call_error_kind = _call_and_parse(client, prompt)
        if validated is None:
            return ClaimsGenerationResult(
                provider=config.provider, model=config.model, is_mock=is_mock,
                decision_method="reasoning_llm", claims=deterministic_claims,
                scope_note=scope_note, as_of_snapshot_commit=as_of,
                retried=attempt > 1, error=call_error, error_kind=call_error_kind,
            )
        llm_claims, invalid_reasons = _validate_claims(validated, bundle)
        if not invalid_reasons:
            return ClaimsGenerationResult(
                provider=config.provider, model=config.model, is_mock=is_mock,
                decision_method="reasoning_llm",
                claims=deterministic_claims + llm_claims,
                scope_note=validated.scope_note or scope_note,
                as_of_snapshot_commit=as_of, retried=attempt > 1,
            )
        last_error = "; ".join(invalid_reasons)
        last_error_kind = "invalid_citation"
        allowed = sorted(allowed_source_ids(bundle))
        retry_note = (
            "前回の応答には許可されていない引用がありました: "
            f"{last_error}. allowed_source_ids に含まれる id だけを使い、"
            f"引用できる id は次のみです: {json.dumps(allowed)}"
        )

    return ClaimsGenerationResult(
        provider=config.provider, model=config.model, is_mock=is_mock,
        decision_method="reasoning_llm", claims=deterministic_claims,
        scope_note=scope_note, as_of_snapshot_commit=as_of,
        retried=True, error=last_error, error_kind=last_error_kind,
    )

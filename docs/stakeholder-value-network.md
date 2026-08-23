# Stakeholder Value Network — canonical contract (Epic #418)

Status: canonical. Read **§0** before touching anything in this area.

Owning issues: #418 (Epic) / #419 (this contract) / #420 (persistence + API) /
#421 (lineage + staleness) / #422 (Value Network projection + Dashboard) /
#423 (Journey Service Blueprint) / #424 (Functional Lineage View + Gap /
Impact Overlay + E2E).

Related canonical contracts, none of which this layer replaces:
`docs/purpose-chain.md` (#387–#391), `docs/ux-design-lineage.md` (#405),
`docs/evolutionary-pipeline.md` (#394), `docs/execution-modes.md` (#412).

---

## §0. What this layer is, and the ten invariants

probe-agent can already say *what the system is for* (Purpose Chain), *what
experience it should deliver* (UX Design Lineage), *how a request actually
executes* (Flow / Evolution Node / Component / Probe Cell), and *what was
observed* (Trace / Replay / Experiment / Outcome). What it cannot say is
**who the parties are, and what each of them gives and gets**.

Today a "stakeholder" is four unrelated free-text fields —
`system_profile.target_users`, `system_profile.stakeholder_value`, the
Purpose Chain's `beneficiary_problem`, and `ux_journey_revision.beneficiary`.
None of them is an identity, so nothing can be said *about the same party
twice*: that the party who pays is not the party who benefits, that a
provider receives no feedback, that a Need has no Journey that meets it.

This Epic adds exactly one layer: **Stakeholder / Need / Environment
Observation / Value Exchange**, plus references from it into the canonical
rows that already exist. It is the sibling of #405 one step further
upstream, and it obeys the same discipline.

### The ten invariants

1. **No fifth understanding model.** Purpose / Vision / Capability / Journey
   / Requirement / Solution Design / Flow / Node / Component / Cell / Outcome
   keep their existing owners. This layer owns Stakeholder, Need,
   Environment Observation, and Value Exchange — content that is genuinely
   newly authored and lives nowhere else — and owns the **references** from
   those to everything upstream and downstream.

2. **Never copy an upstream or downstream body.** A reference stores
   `target_kind` + a stable `target_ref` (+ `target_row_id` when it helps a
   read) + a `captured_digest`, and is resolved at **READ** time against
   exactly ONE canonical source per kind (§5.1's table, the same discipline
   as `node_design._LINK_KIND_TARGET_SOURCE` and `ux_design.
   _resolve_upstream_target`). A copied Capability name still reads as
   current after the original is superseded — the defect #397's handoff hit
   and #405 was written to avoid.

3. **Identity is `(system_id, <kind>_key)`, a developer-supplied stable
   slug.** Never derived from a row id (rebuilds renumber), never from a
   name (a rename would sever the history), never from the Purpose element's
   hashed id (`core_capability:<sha256(name)>` changes when the claim is
   reworded). Same rule as Evolution Node ADR-2 and #405.

4. **Four independent axes, never collapsed.**
   `design_status` (derived from the decision ledger) /
   `recheck_state` (digest comparison) /
   `revision_state` (`superseded_by_id IS NULL`) /
   `authored_by_kind` (whose voice). A stale `confirmed` Exchange stays
   `confirmed` and asks to be re-confirmed; it is never silently demoted. A
   `reasoning_model`-authored revision that a human confirms is *"a human
   confirmed AI-written text"* — the author does not become the developer.

5. **`unknown` / `unavailable` / `not_applicable` / `stale` are four
   different answers.** "Nobody decided yet", "this request could not read
   it", "this lane structurally does not apply", and "the content it was
   agreed against has moved" never share a rendering, a count, or a copy
   string (#366's one-word-two-facts rule; #380's `degraded_sections`).

6. **The exchange and its consideration are not one edge.** A Value Exchange
   is directional: `provider → receiver`, with an `exchange_kind` from a
   finite set, a `value_statement`, and a **separately described**
   consideration (`consideration_state` + `consideration_kind` +
   `consideration_statement`). Money and experience are never folded into
   one "value" edge — the whole point of the layer is to see that the payer
   and the beneficiary differ.

7. **No score, no ranking, no centrality.** No weighted total, no
   completeness percentage, no confidence percentage, no "stakeholder
   importance" from degree/centrality/exchange count. Gap counts may be
   displayed; they are never a stand-in for value or priority (#353, #394
   ADR-7, #424's analysis discipline).

8. **A runtime trace never confirms a human fact.** The existence of traces
   never marks an Exchange delivered, a Need met, or an Outcome achieved.
   Outcome remains `purpose_outcome_criterion`'s to own (#391).

9. **AI proposes; only a human decides.** A reasoning model may author a
   Stakeholder, Need, Observation, Exchange, or reference — always stored as
   `design_status='proposed'` with `authored_by_kind='reasoning_model'`.
   `confirm` / `reject` / `retire` / `reinstate` are `decision_method:
   manual`, always. Semantic links are never auto-confirmed by string
   similarity, embeddings, or keyword scoring (Principle 6). **No LLM call
   exists anywhere in this Epic's modules** — every rule here is a direct
   read, a first-match classification over a finite vocabulary, or an
   append-only write of content the caller supplied.

10. **The diagram is never the record.** Node coordinates, auto-layout
    results, and rendered graphs are derived, read-only projections of
    canonical data. Only *display settings* (filters, collapsed refs, pinned
    refs) may be persisted, in a table that no projection reads as a fact.

### Non-goals (Epic-wide)

CRM of organizations or people; accounting, invoicing, or payment execution;
automatic conversion between monetary and UX value; LLM determination of
stakeholder importance, need satisfaction, or value arrival; persisting
layout as canonical state; replacing UX Design Studio or Flow Explorer.

---

## §1. Entities this layer owns

| Entity | Identity | Content lives in | Owns |
| --- | --- | --- | --- |
| Stakeholder | `(system_id, stakeholder_key)` | `stakeholder_revision` | who a party is, and its roles |
| Stakeholder Need | `(system_id, need_key)` | `stakeholder_need_revision` | a want/problem attributed to one Stakeholder |
| Environment Observation | `(system_id, observation_key)` | `environment_observation` (+ `environment_observation_impact`) | a change in the world, and what it does to which subject |
| Value Exchange | `(system_id, exchange_key)` | `value_exchange_revision` | provider → receiver, kind, value, consideration |

Everything else this layer touches is a **reference**, never a copy.

### §1.1 Stakeholder vs Stakeholder Role

A Stakeholder is a **party**, not a job title. `stakeholder_kind` says what
sort of party it is; a `stakeholder_role_assignment` says what that party
*does in a given context*. The separation is load-bearing: one party is
routinely a beneficiary in one Journey Step and the payer in another, and a
model with one role column cannot say that.

`stakeholder_kind` (finite): `end_user` | `customer_organization` |
`internal_operator` | `provider_team` | `partner` | `regulator` | `other`.

`stakeholder_role` (finite): `actor` | `beneficiary` | `payer` | `operator` |
`approver` | `supplier` | `regulator` | `observer`.

A role assignment is scoped: `(stakeholder_key, role, scope_kind,
scope_ref)`, where `scope_kind` is `system` | `journey` | `journey_step` |
`value_exchange`. `scope_ref` is always stored with its prefix, exactly as
#412 requires of mode scopes.

### §1.2 Need vs Problem

They are the same row with a finite `need_kind`, not two tables:
`unmet_need` | `problem` | `constraint` | `expectation`. Splitting them into
separate entities would force a judgement ("is this a want or a pain?") at
creation time that the developer often cannot make, and would double every
link kind downstream. The distinction that *does* matter — whether anything
is being done about it — is the Need's links, not its table.

**`beneficiary_problem` is not parsed and not migrated.** The Purpose Chain's
free-text pain stays exactly where it is and keeps its meaning (#388's rule:
splitting free text into who-and-what is open-ended interpretation). A
Stakeholder Need may *reference* the Purpose element, and a developer may
confirm that reference. Nothing infers it. Likewise
`ux_journey_revision.beneficiary` stays as a free-text field for backward
compatibility (#421); the Stakeholder role link is the new canonical answer,
introduced alongside it, and a string match between the two is never
promoted to a link.

### §1.3 Environment Observation

An observation of the world outside the system: a regulation, a competitor,
a price change, a platform deprecation, a demand shift. It is **not** a
runtime observation and must never be conflated with one — `#412`'s mode
observations and `state_facts`' freshness readings are about this system's
own execution.

`environment_observation_impact.impact_kind` (finite): `creates` | `worsens` |
`relieves` | `invalidates` | `constrains`. The impact target is a reference
(§5.1) — a Stakeholder, a Need, a Purpose element, a Journey, a Requirement,
or a Value Exchange.

`observation_confidence` is **not** a percentage. It is
`observed` | `reported` | `assumed`, three finite provenance values.

### §1.4 Value Exchange

The edge of the network. Required at every revision:

| Field | Rule |
| --- | --- |
| `provider_stakeholder_key` / `receiver_stakeholder_key` | both must resolve in this System; both may be the same party only when `exchange_kind='information'` (a party genuinely reporting to itself is a modelling error otherwise, so this is refused as `exchange_self_loop`) |
| `exchange_kind` | finite, §2 |
| `value_statement` | required, non-empty (`exchange_value_statement_required`) |
| `consideration_state` | `present` \| `none` \| `unknown` — three answers, never a nullable boolean |
| `consideration_kind` | required iff `consideration_state='present'`, from the same finite `exchange_kind` vocabulary |
| `consideration_statement` | required iff `consideration_state='present'` |
| `channel` | free text; `''` means unstated |
| `trigger` | free text; `''` means unstated |
| `cadence` | finite: `one_time` \| `recurring` \| `continuous` \| `on_demand` \| `unknown` |
| `valid_from` / `valid_to` | optional epoch seconds; `valid_to` must be `> valid_from` (`exchange_validity_inverted`) |

`validity_state` is **derived** at read time, never stored:
`not_started` | `active` | `ended` | `unbounded`. It is a fifth axis and is
independent of `design_status` — an ended Exchange is still `confirmed`
history, not a rejected one. (#412's EM-ADR-2 rule applied here: expiry and
revocation are different answers, and only expiry follows the clock.)

---

## §2. Finite vocabularies

Every value below is a `Literal` alias in `apps/control-server/app/models.py`,
mirrored into the domain module with `get_args`, and held to the Dashboard
TypeScript unions by `apps/control-server/tests/test_interview_type_parity.py`'s
`FINITE_TYPE_NAMES` (#351's rule — a bare `str` puts no enum in the OpenAPI
schema and lets the union drift unnoticed).

| Alias | Values |
| --- | --- |
| `StakeholderKind` | `end_user`, `customer_organization`, `internal_operator`, `provider_team`, `partner`, `regulator`, `other` |
| `StakeholderRole` | `actor`, `beneficiary`, `payer`, `operator`, `approver`, `supplier`, `regulator`, `observer` |
| `StakeholderRoleScopeKind` | `system`, `journey`, `journey_step`, `value_exchange` |
| `StakeholderNeedKind` | `unmet_need`, `problem`, `constraint`, `expectation` |
| `EnvironmentObservationConfidence` | `observed`, `reported`, `assumed` |
| `EnvironmentImpactKind` | `creates`, `worsens`, `relieves`, `invalidates`, `constrains` |
| `ValueExchangeKind` | `experience`, `service`, `information`, `money`, `authority`, `obligation`, `risk` |
| `ValueExchangeConsiderationState` | `present`, `none`, `unknown` |
| `ValueExchangeCadence` | `one_time`, `recurring`, `continuous`, `on_demand`, `unknown` |
| `ValueExchangeValidityState` | `not_started`, `active`, `ended`, `unbounded` |
| `StakeholderDesignStatus` | `proposed`, `confirmed`, `rejected`, `retired` |
| `StakeholderDecisionKind` | `confirm`, `reject`, `retire`, `reinstate` |
| `StakeholderRecheckState` | `current`, `stale` |
| `StakeholderRevisionState` | `current`, `superseded` |
| `StakeholderAuthorshipKind` | `developer`, `reasoning_model` |
| `StakeholderSubjectKind` | `stakeholder`, `stakeholder_need`, `environment_observation`, `value_exchange`, `stakeholder_ref`, `stakeholder_role_assignment` |
| `StakeholderRefKind` | §5.1's table |
| `StakeholderRefTargetResolution` | `resolved`, `unresolved`, `unavailable` |
| `StakeholderRefRecheckState` | `current`, `stale`, `not_captured` |
| `StakeholderRefRelationStatus` | `confirmed`, `proposed`, `derived` |
| `StakeholderEvidenceState` | `available`, `missing`, `unavailable`, `stale` |
| `ValueNetworkNoticeCode` | §7.2 |
| `JourneyDeliveryKind` | `frontstage`, `backstage`, `support`, `external` |
| `BlueprintLaneKind` | §8.1's nine lanes |
| `BlueprintLaneState` | `present`, `unknown`, `not_applicable`, `unavailable` |
| `FunctionalLineageKind` | §9.1's table |
| `LineageGapCode` | §9.2 |
| `LineageGapSeverity` | `blocking`, `attention`, `informational` |

### §2.1 What each `ValueExchangeKind` means

These are deliberately distinguished, and the boundaries are part of the
contract because a projection that folds any two of them loses the question
the Epic exists to answer.

- **`experience`** — what the receiver *undergoes*: the UX itself. Fulfilled
  by a Journey / Journey Step, never by an invoice.
- **`service`** — work performed for the receiver, whose value is the
  outcome rather than the experience of it (an SLA, an operation run on
  their behalf).
- **`information`** — facts transferred: reports, telemetry, feedback,
  disclosures. The one kind whose provider and receiver may coincide.
- **`money`** — a monetary flow *described*. probe-agent records that it
  happens, in what direction, on what cadence. It stores **no amount
  arithmetic, no ledger, no invoice, no settlement** — see §11.
- **`authority`** — a permission, mandate, or decision right granted.
- **`obligation`** — a duty accepted (a commitment, a retention promise, a
  support undertaking).
- **`risk`** — an exposure transferred or assumed (liability, availability
  risk, compliance exposure).

---

## §3. Revisions, decisions, and the four axes

Identical in shape to #405 §2.5, so the two layers behave the same way under
the developer's hands.

- **Identity table** (`stakeholder`, `stakeholder_need`, `value_exchange`):
  the stable key, `current_revision_id`, and nothing meaning-bearing.
- **Revision table**: append-only. A correction inserts
  `revision_number = max + 1` and sets the previous current row's
  `superseded_by_id`. Nothing is ever `UPDATE`d in place, because a
  confirmation was made against one specific revision's content and that
  history must stay readable.
- **Decision ledger** (`stakeholder_decision`): one append-only row per
  human decision, `(subject_kind, subject_key, decision, captured_digest,
  decided_by, decided_at)`, with `decision_method` CHECKed to `'manual'`.

`design_status` is **derived**, never stored — the latest non-superseded
decision row folded through the fixed table:

| latest decision | derived `design_status` |
| --- | --- |
| *(none)* | `proposed` |
| `confirm` | `confirmed` |
| `reject` | `rejected` |
| `retire` | `retired` |
| `reinstate` | `proposed` |

Legal transitions (anything else is 422 `stakeholder_not_decidable`):

| from | `confirm` | `reject` | `retire` | `reinstate` |
| --- | --- | --- | --- | --- |
| `proposed` | ✓ | ✓ | ✓ | ✗ |
| `confirmed` | ✗ | ✓ | ✓ | ✗ |
| `rejected` | ✗ | ✗ | ✗ | ✓ |
| `retired` | ✗ | ✗ | ✗ | ✓ |

`Environment Observation` has no revision chain: an observation is a dated
statement about the world at a moment. A correction is a **new** observation
that may declare `supersedes_observation_key`; the original is never edited
or deleted, for the same reason #329 makes findings append-only.

### §3.1 Digests

`content_digest` is SHA-256 over the canonical JSON of the meaning-bearing
fields only — `json.dumps(payload, sort_keys=True, ensure_ascii=False,
separators=(",", ":"))`, exactly `ux_design.content_digest`.

`created_by` / `created_at` / `revision_number` / `change_note` are
**excluded** from every digest. A recheck must fire on a change of MEANING,
never on the existence of a new record (#308 excludes `confirmation_id`;
#337 excludes Intent `status`; #405 excludes `change_note`).

| Subject | Digested fields |
| --- | --- |
| Stakeholder | `stakeholder_key`, `display_name`, `stakeholder_kind`, `description`, `context_note` |
| Need | `need_key`, `need_kind`, `statement`, `rationale`, `stakeholder_key` |
| Value Exchange | `exchange_key`, `provider_stakeholder_key`, `receiver_stakeholder_key`, `exchange_kind`, `value_statement`, `consideration_state`, `consideration_kind`, `consideration_statement`, `channel`, `trigger`, `cadence`, `valid_from`, `valid_to` |
| Environment Observation | `observation_key`, `statement`, `source_note`, `observation_confidence`, `observed_at` |

The Stakeholder's role assignments are deliberately **not** in its digest:
adding a role in one Journey must not invalidate a confirmation of *who this
party is*. Role assignments carry their own decision rows.

---

## §4. Staleness

Two independent producers, one finite vocabulary.

1. **Subject staleness** — a decision's `captured_digest` no longer equals
   the subject's current `content_digest`. `recheck_state='stale'`; the
   decision row is never deleted and `design_status` stays `confirmed`.
2. **Reference staleness** — a reference's `captured_digest` no longer
   equals the resolved target's digest, or the target no longer resolves.

`_ref_recheck_state` is copied in spirit from `ux_design`:

```
captured_digest == ""                 -> "not_captured"   (fail-closed)
resolution != "resolved"              -> "stale"
captured_digest != current_digest     -> "stale"
otherwise                             -> "current"
```

**Propagation is downstream only, one hop, through explicit links.** The
table below is exhaustive; nothing else propagates.

| When this changes | These go `stale` | These do NOT |
| --- | --- | --- |
| Purpose element / relation | refs pointing at it from Need / Exchange | the Stakeholder itself |
| Capability entity | Exchange → Capability refs | Journey, Requirement |
| Journey revision (incl. Step set) | Exchange → Journey/Step refs; Step → Stakeholder role links for removed steps | Need, Exchange content |
| Need revision | Exchange → Need refs | Stakeholder |
| Stakeholder revision | role assignments' captured digest; Exchange refs naming it as provider/receiver | Purpose, Journey |
| Value Exchange revision | Journey Step → Exchange links | Need, Purpose |
| Outcome criterion | Exchange → Outcome refs | everything upstream |

Fixing a downstream item never staleifies its upstream (#388's rule).

---

## §5. References

### §5.1 The reference table — one canonical source per kind

`stakeholder_ref` is the single reference table for this layer:
`(system_id, source_kind, source_key, ref_kind, target_ref, target_row_id,
captured_digest, relation_status derived from decision_method, note,
created_by, created_at, superseded_by_id)`.

`source_kind` ∈ `stakeholder` | `stakeholder_need` | `environment_observation`
| `value_exchange` | `journey_step`.

| `ref_kind` | `target_ref` format | Resolved at read time against |
| --- | --- | --- |
| `purpose_element` | Purpose element id | `purpose_chain.derive_purpose_chain(...).elements` |
| `purpose_relation` | Purpose relation id | `…derive_purpose_chain(...).relations` |
| `capability_entity` | `understanding_capability_entity.id` as text | that table (#312's stable System-scoped identity — **never** `capability_hierarchy_nodes.id`, regenerated per snapshot, and never the Purpose Chain's hashed name id) |
| `ux_journey` | `ux_journey.journey_key` | `ux_journey` + its current revision |
| `ux_journey_step` | `"<journey_key>#<step_key>"` | that Journey's **current** revision's Steps |
| `ux_requirement` | `ux_requirement.requirement_key` | `ux_requirement` + its current revision |
| `purpose_outcome_criterion` | `purpose_outcome_criterion.id` as text | that table |
| `stakeholder` | `stakeholder_key` | this layer |
| `stakeholder_need` | `need_key` | this layer |
| `value_exchange` | `exchange_key` | this layer |

A `ref_kind` outside this table is 422 `stakeholder_ref_kind_invalid`. A
target that does not resolve **in this System** is 404
`stakeholder_ref_target_not_found` — foreign-System and non-existent are
deliberately the same error, so a caller cannot probe another System's ids
(the rule `ux_design.NotFound` documents).

`relation_status` is the fixed translation of the ref's own
`decision_method`, never a second stored column:
`manual → confirmed`, `reasoning_llm → proposed`, `deterministic → derived`.

### §5.2 What is deliberately NOT a reference kind

- **`static_flow` / `runtime_flow` / `evolution_node` / `component` /
  `probe_cell`.** This layer reaches them only *through* the UX Design
  Lineage (#405) and Evolution Node (#394) links that already exist, which
  is where their identity rules live. Adding a second path to them here
  would create a second, differing answer for "which Node implements this"
  (#412's rule that a static Flow id like `flow-1` is not permanent, and
  #405's rule that static and runtime Flow identity are never merged).
- **`experiment` / `trace` / `replay_run`.** Evidence is referenced by the
  Outcome criterion that owns it (#391), never directly from an Exchange.

---

## §6. Evidence

`stakeholder_evidence_ref` attaches evidence to a Need, an Observation, or
an Exchange: `(subject_kind, subject_key, evidence_kind, evidence_ref,
statement, captured_digest, created_by, created_at)`.

`evidence_kind` (finite): `human_report` | `document` |
`runtime_observation` | `external_analytics`.

The three provenances are kept apart for the reason #328 keeps investigation,
translation, and developer findings apart: a human interview note, a
telemetry reading, and a third-party analytics figure support a claim in
different ways and must not read as one number.

`evidence_state`, derived per subject: `available` (at least one
non-superseded evidence row resolves) | `missing` (none) | `stale` (all
resolving rows have a moved `captured_digest`) | `unavailable` (the evidence
read itself failed). **`missing` and `unavailable` are never merged** — a
failed read is not proof of absence (#380).

A `runtime_observation` evidence row never changes any `design_status` and
never marks an Exchange delivered (invariant 8).

---

## §7. Stakeholder Value Network projection (#422)

`app/stakeholder_value_network.py` → `GET /stakeholder-value-network`.
Read-only, deterministic, no LLM, writes nothing.

### §7.1 Shape

- **nodes**: one per Stakeholder — `stakeholder_key`, `display_name`,
  `stakeholder_kind`, system-scope `roles`, `design_status`,
  `recheck_state`, `authored_by_kind`, `evidence_state`.
- **edges**: one per Value Exchange — `exchange_key`, provider/receiver
  keys, `exchange_kind`, `value_statement`, consideration summary,
  `design_status`, `recheck_state`, `validity_state`, `evidence_state`, and
  `related_refs` (Need / Purpose / Capability / Journey / Step / Outcome).
- **notices**: §7.2.
- **degraded_sections** / **degraded_detail**: per-section guarded loaders,
  #380's discipline. A failing section is dropped from the DISPLAY; no
  guessed value is substituted, and a failure never becomes `0`.

**Ordering is total and stable**: nodes by `(display_name, stakeholder_key)`,
edges by `(provider_key, receiver_key, exchange_kind, exchange_key)`, so the
same facts always render the same graph. **No coordinates are computed
server-side and none are stored** (invariant 10).

### §7.2 Structural notices — facts, never judgements

Each is a deterministic structural check with a finite code. None of them
says an item is unimportant or valueless; they say a link is absent.

| Code | Fires when |
| --- | --- |
| `stakeholder_without_exchange` | a Stakeholder is neither provider nor receiver of any non-rejected Exchange |
| `stakeholder_without_role` | no role assignment in any scope |
| `stakeholder_without_need` | no Need attributed to it |
| `payer_differs_from_beneficiary` | an `experience`/`service` Exchange's receiver is not the receiver of any `money` Exchange whose provider chain reaches the same provider |
| `exchange_without_need` | no `stakeholder_need` ref |
| `exchange_without_journey` | no `ux_journey` / `ux_journey_step` ref |
| `exchange_without_outcome` | no `purpose_outcome_criterion` ref |
| `confirmed_without_evidence` | `design_status='confirmed'` and `evidence_state='missing'` |
| `feedback_path_missing` | a provider receives no `information` Exchange from any party it provides to |
| `stale_link` | any reference on the subject is `stale` |
| `stale_confirmation` | subject `recheck_state='stale'` |

`payer_differs_from_beneficiary` is an **observation**, printed as such: in
plenty of systems the buyer is legitimately not the user. Its value is that
the developer can *see* it, not that the tool has an opinion about it.

### §7.3 Dashboard

`/stakeholder-value-network`. Stakeholders as nodes, Exchanges as directed
edges. `exchange_kind` is distinguished by **label + line style + legend**,
never colour alone (accessibility, and #358's "never colour alone" rule).
Selecting a node or edge opens a detail pane with content, provenance,
state, evidence, and every related lineage ref as a deep link. Filters:
exchange kind, role, `design_status`, staleness. The selection is in the
URL, so a reload or a shared link reproduces the view. Below ~360px the
graph degrades gracefully to the list + detail presentation — it does not
hide state. Empty, partial-failure, `unknown`, and `stale` are all rendered,
never suppressed. Layout is never written back to the server.

---

## §8. Journey Service Blueprint projection (#423)

`app/journey_blueprint.py` → `GET /journey-blueprint`. Read-only,
deterministic, no LLM, writes nothing. Steps of the Journey's **current**
revision are the horizontal axis; the nine lanes are the vertical one.

### §8.1 The nine lanes

| # | `BlueprintLaneKind` | Source of truth |
| --- | --- | --- |
| 1 | `stakeholder_action` | Step `user_intent` + Step→Stakeholder role links |
| 2 | `touchpoint` | Step→touchpoint link (`channel`) |
| 3 | `frontstage` | Step `system_response` + delivery links of kind `frontstage` |
| 4 | `backstage` | delivery links of kind `backstage` → #405's existing Requirement/Design → Flow/Node links |
| 5 | `support` | delivery links of kind `support` |
| 6 | `external` | delivery links of kind `external` |
| 7 | `requirement` | `ux_requirement_step_link` + acceptance criteria (#405) |
| 8 | `evidence` | Step `evidence_expectation` / `evidence_source_kind` + observed evidence refs |
| 9 | `failure_recovery` | Step `failure_mode` / `recovery_path` |

Every lane carries a `BlueprintLaneState`. **`unknown` and
`not_applicable` are distinct and neither is auto-filled**: "no backstage
process has been described" and "this step structurally has no backstage" are
different sentences, and only the developer can say which one is true.

Saying the second one needs somewhere to be written down, which the first
draft of this section did not provide. The mechanism is exactly one sentinel:
`journey_step_delivery_link.target_kind = 'not_applicable'`, reachable **only
by an explicit developer write** and never inferred from an absent link — an
absent link always reads `unknown`. A real link on the same lane outranks the
sentinel (`present`). The sentinel exists on the four delivery lanes only
(`frontstage` / `backstage` / `support` / `external`); the other five lanes
report `present` / `unknown` / `unavailable` and never invent
`not_applicable`, because none of them describes a thing a Step can
structurally lack.

### §8.2 Added links (owned here, content never copied)

- `journey_step_stakeholder_link` — `(journey_key, step_key,
  stakeholder_key, role)`; several Stakeholders per Step is normal and is
  the point.
- `journey_step_delivery_link` — `(journey_key, step_key, delivery_kind,
  target_kind, target_ref, captured_digest)`, where `delivery_kind` is
  `JourneyDeliveryKind` and `target_kind` resolves through §5.1 or, for
  backstage, through #405's own Requirement → Solution Design → Flow/Node
  chain.
- `journey_step_exchange_link` — `(journey_key, step_key, exchange_key)`:
  which Value Exchange this Step delivers.

Journey / Step / Requirement / Flow / Node bodies are **never** copied into
these rows.

### §8.3 as-is / to-be diff

The existing `ux_journey.perspective` + `baseline_journey_id` decide the
pair. The diff is by **exact `step_key` equality only** —
`added` | `removed` | `changed` | `reordered` | `unchanged`. No similarity,
no embeddings, no keyword scoring (Principle 6). `changed` compares the
Step's own `content_digest`; `reordered` is an unchanged digest at a
different `step_order`.

---

## §9. Functional Lineage View + Gap / Impact Overlay (#424)

`app/functional_lineage.py` → `GET /functional-lineage`. Read-only,
deterministic, no LLM.

### §9.1 The chain

```
Purpose / Vision → Capability → Value Exchange → Journey / Step
  → Requirement / Acceptance Criterion → Solution Design / adopted Option
  → static Flow / runtime Flow → Evolution Node / Component / Cell / Probe Point
  → Trace / Replay / Experiment → Outcome
```

Each hop keeps its own identity and its own `kind`. **Static Flow and
runtime Flow are never one entity**; Capability, Flow, and Node are never
folded together. Every hop is resolved by exact stable ref through the
canonical source that already owns it.

### §9.2 Gap codes

Finite, deterministic, every one reachable in the E2E fixture, each with a
fixed `LineageGapSeverity` its `kind` carries (never computed per instance —
that would be the importance score invariant 7 forbids).

`stakeholder_without_role` · `stakeholder_without_need` ·
`need_without_purpose` · `need_without_exchange` · `need_without_journey` ·
`exchange_without_journey` · `exchange_without_outcome` ·
`journey_step_without_requirement` ·
`requirement_without_acceptance_criterion` · `requirement_without_design` ·
`adopted_design_without_implementation_target` · `flow_without_node` ·
`node_without_flow` · `subject_without_evaluation_policy` ·
`confirmed_without_evidence` · `stale_upstream` · `stale_link` ·
`stale_evidence` · `conflicting_dependency` · `rejected_dependency` ·
`feedback_path_missing` · `unresolved_reference` · `unavailable_reference`

### §9.3 Analysis discipline

- Links resolve by **exact match on a stable ref**, never by similarity.
- **No weighted total score.** Gap counts may be shown; they never stand in
  for value or priority.
- Impact traversal is **downstream only**, through explicit links.
- **`unavailable` is never counted as `missing`.** An unreadable section is
  `unavailable_reference` and lands in `degraded_sections`.
- A runtime trace never makes a UX Outcome `confirmed`.
- **No LLM is used in any projection or gap determination.**

### §9.4 Dashboard integration

The three views (Value Network, Service Blueprint, Functional Lineage) share
one selected ref and one System scope, and navigate between each other
without losing either. Selecting a gap shows its evidence and **the single
next operation that would resolve it**, as a deep link into the existing
screen that owns that operation (Purpose Chain, UX Design Studio, Flow
Explorer, Evolution Nodes, Experiments) — the CTA **navigates, it never
executes** (#358 / #405's rule). Overlays are labelled, not colour-only.
Partial read failures render per section.

---

## §10. API surface

Prefix `/stakeholder-network` for the canonical entities (#420 / #421),
`/stakeholder-value-network`, `/journey-blueprint`, `/functional-lineage`
for the three projections (#422 / #423 / #424). All are System-scoped by the
existing `get_system_id` dependency and authenticated by `require_user`.

Write boundary rules, exactly #405's:

- Every write request model is `ConfigDict(extra="forbid")`.
- `created_by` / `decided_by` / `decision_method` / `authored_by_kind` are
  **never** accepted from a request body — they come from the route and the
  authenticated `Principal` (#337: an unverifiable body-supplied identity
  lets a caller fabricate an audit trail).
- **GET writes nothing.** A page view is never a decision (#382).

Reject codes (each 4xx, each with a test):
`stakeholder_key_required` · `stakeholder_key_conflict` ·
`stakeholder_not_found` · `stakeholder_ref_kind_invalid` ·
`stakeholder_ref_target_not_found` · `stakeholder_ref_wrong_target_kind` ·
`exchange_self_loop` · `exchange_value_statement_required` ·
`exchange_consideration_incomplete` · `exchange_validity_inverted` ·
`stakeholder_decision_stale_digest` · `stakeholder_not_decidable` ·
`stakeholder_authorship_not_settable` · `observation_impact_kind_invalid` ·
`journey_step_not_found` · `journey_blueprint_journey_not_found`

`stakeholder_authorship_not_settable` is the #336-shaped rule: a caller
cannot claim `authored_by_kind='developer'` for a machine-authored row, so
authorship comes from the route that wrote it.

---

## §11. Money is described, never processed

An Exchange of `exchange_kind='money'` records **that** a monetary flow
exists, its direction, its cadence, and a free-text `value_statement`.
probe-agent stores **no amount arithmetic, no currency conversion, no
invoice, no ledger, no settlement, and no payment execution**, and never
converts between monetary and UX value. This is a non-goal of the Epic and a
structural rule of this contract: there is no amount column to sum.

---

## §12. Saved views

`stakeholder_view_preference` may persist **display settings only** —
selected filters, collapsed refs, pinned refs, the active view. It carries no
coordinates, no computed layout, and **no projection reads it as a fact**
(invariant 10). Deleting every row in it changes what a developer sees on
their next visit and changes no answer the system gives.

---

## §13. Migration and backward compatibility

- Every table is **additive**. Nothing existing is dropped, renamed, or
  backfilled by inference.
- `system_profile.target_users` / `stakeholder_value`,
  `purpose` `beneficiary_problem`, and `ux_journey_revision.beneficiary`
  keep their current meaning and keep being displayed. The Stakeholder link
  is introduced **alongside** them as the new canonical answer, per #421.
- **No automatic migration of free text into Stakeholders.** A developer (or
  an explicitly-marked `proposed` reasoning-model suggestion they then
  confirm) creates them. A string match is never promoted to an identity.
- A System with no Stakeholder rows returns empty projections with
  `unknown`/`missing` states — never an error, never a zeroed score.

---

## §14. Retention, privacy, isolation

- Every table carries `system_id` with `ON DELETE CASCADE` and every query
  filters on it. Cross-System reads are 404, never a partial answer. Every
  new table gets an isolation test (CLAUDE.md's rule for System-scoped
  tables).
- Stakeholders are **roles and parties, not people**. Personal data,
  contact details, and account identifiers are out of scope; nothing in this
  layer is a CRM record. `display_name` is expected to be a role label
  ("購入責任者", "運用担当者"), and the Dashboard copy says so.
- Free-text fields are covered by the existing storage-boundary redaction
  rules (Principle 9) wherever they are ingested from a payload path.

---

## §15. Testing policy

Every sub-issue lands with tests. The Epic-level bar:

1. **Type parity** — every finite vocabulary in §2 appears in `models.py`
   as a `Literal`, in the domain module via `get_args`, and in the Dashboard
   union, enforced by `test_interview_type_parity.py`.
2. **System isolation** — every new table, read path, and decision refuses
   or 404s across Systems.
3. **Append-only** — a correction never mutates a prior revision; a
   decision never deletes a prior decision.
4. **Derived, never stored** — `design_status` and `validity_state` are
   recomputed from the ledger/clock in the test, and a hand-corrupted stored
   value cannot exist because there is no column to corrupt.
5. **Staleness** — for each row of §4's propagation table, a test that the
   named subject goes `stale` and the named non-subject does not.
6. **Human gate** — a reasoning-model-authored row is `proposed` and cannot
   become `confirmed` without a manual decision; `decision_method` is
   CHECKed to `'manual'` in the ledger.
7. **No LLM** — a regression test that no module in this Epic imports or
   calls `llm` / `create_llm_client`.
8. **No synthetic outcome, no weighted score** — a regression test that no
   projection returns a numeric score field and that traces alone never move
   an Outcome state.
9. **Partial failure** — each projection section degrades independently and
   records `degraded_sections`, substituting no guessed value.
10. **E2E fixture** (#424) — 利用者 / 購入責任者 / 運用担当者 / 提供者, with
    `service`+`experience`, `money`, `information`, and `obligation`
    exchanges, payer ≠ beneficiary, an as-is and a to-be Journey,
    Requirements, a Design Option, a Flow and a Node, human and runtime
    evidence, an Outcome, an upstream change producing `stale`, and at least
    one unconnected and one unmeasured gap — traversed end to end.

---

## §16. Sub-issue boundaries

| Issue | Owns | Must not |
| --- | --- | --- |
| #419 | this document | any code |
| #420 | `db.py` tables, `models.py` vocabularies + models, `app/stakeholder_network.py`, `app/routes/stakeholder_network.py` | projections, lineage refs, UI |
| #421 | `stakeholder_ref` resolution, staleness propagation, `GET /stakeholder-network/exchanges/{key}/lineage` | diagrams, gap codes |
| #422 | `app/stakeholder_value_network.py` + route + `/stakeholder-value-network` page | blueprint lanes, gap codes |
| #423 | `app/journey_blueprint.py` + route + `/journey-blueprint` page, §8.2's three link tables | value-network notices, gap codes |
| #424 | `app/functional_lineage.py` + route + overlay UI + cross-view navigation + E2E fixture | new canonical entities |

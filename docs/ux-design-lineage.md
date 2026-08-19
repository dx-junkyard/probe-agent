# UX Design Lineage(Epic #405 / #406-#409)

canonical design contract. 実装はこの文書に従う。

probe-agent は Purpose Chain(#387-#391)で「対象者と現在の課題 → 望ましい変化 →
システムの介入 → Capabilities」を保持できる。しかし **その Capability を
「誰がどの経路でどう使い、何が満たされていれば良いのか」を追跡可能な設計成果物として
保存する正本が無い**。UX の価値仮説は `purpose_experience_hypothesis` の一文に、
実行経路は Flow Explorer の都度計算に、処理単位の契約は `evolution_node_version` に
分かれており、どれも「この体験を実現するための要件と実現案」ではない。

この Epic はその一層だけを足す。

```text
Purpose / Capability                     ← 既存正本(#387-#391 / #312)。複製しない
        ↓ 参照 (ux_journey_upstream_ref)
UX Journey / Journey Step                ← #407 で新設
        ↓ 参照 (ux_requirement_step_link)
Requirement (+ Acceptance Criterion)     ← #407 で新設
        ↓ 参照 (solution_design_requirement_link)
Solution Design / Design Option          ← #408 で新設
        ↓ 参照 (solution_design_target_link)
Capability / Flow / Evolution Node /     ← 既存正本。identity を借りるだけ
Component / Probe Cell
```

---

## 0. 全 sub-issue に共通する不変条件

1. **新しい理解モデルを作らない。** Purpose / Vision / Capability / Flow /
   Evolution Node / Component / Probe Cell の正本は既存のまま。この層が持つのは
   **Journey / Step / Requirement / Solution Design という新しい設計成果物**と、
   その上下への **参照 (ref / link)** だけである。上流の内容を列へコピーしない
   (コピーした Capability 名は元の Capability が superseded された後も current
   として読めてしまう — #397 handoff が既に踏んだ轍)。
2. **UX の価値・利用経路・要件・実現案・評価は別モデル。**
   `purpose_experience_hypothesis`(価値仮説)と `ux_journey`(具体的な利用経路)は
   別物であり、片方をもう片方へ畳まない。Capability は価値の単位、Flow は実行経路、
   Evolution Node は進化・評価する処理単位、Probe Cell は実行役割 — この 4 つは
   `solution_design_target_link` の別 `target_kind` であって、同一 entity ではない。
3. **AI は案を出せるが、確認・採用・却下・廃止は常に人間の明示判断。**
   `decision_method: manual` の追記行としてのみ記録し、AI 生成物の
   `decision_method: reasoning_llm` を人間の承認として読ませない。
   **執筆者 (`authored_by_kind`)・決定経路 (`decision_method`)・承認 (決定台帳の行)
   は 3 つの独立した軸**であり、1 列に畳まない(#337 の
   `origin_role` / `producer_kind` / `actor_kind` と同じ規律)。
4. **訂正は append-only revision。** 削除・上書きで監査を失わない。旧 revision は
   `superseded_by_id` を張るだけで残し、「あの時点の内容に対して人がこう判断した」
   という事実を保存する(#388 `purpose_relation_decision` と同じ)。
5. **図・ワイヤーフレーム・ADR の本文を DB へ複製しない。** `content_hash` 付きの
   参照だけを持つ。**本文を入れる列は存在しない** — 規約ではなく構造で禁じる
   (#397 が score 列を作らないことで合成 score を禁じたのと同じ手法)。
6. **runtime trace だけから利用者の成功を推論しない。** UX / Outcome の証拠規律は
   Purpose Verification(#391)を継承する。Journey Step が宣言するのは
   「何が観測できれば成功と言えるか」という **期待**であって、成果そのものではない。
   成果の正本は `purpose_outcome_criterion` のままで、この層は作らない。
7. **合成 score を作らない。** Node 評価 / Flow-Capability 評価 / UX-Outcome 評価は
   `evolution_evaluation_policy` の 3 level のまま別々に読む(ADR-7)。設計の
   「完成度」「充足率」「confidence percentage」を返さない。件数は返してよい。
8. **`unknown` / `unavailable` / `not_applicable` を同じ空値へ丸めない。**
   - `unknown` — 読めたが記録がない(開発者がまだ決めていない)
   - `unavailable` — 読み取り自体が失敗した(この request の事実)
   - `not_applicable` — 構造上その概念が当てはまらない(新規システムに as-is
     Journey が無い、`out_of_scope` 要件に受入条件が無い)
   3 つは別の文言で表示する。1 つに畳むと、開発者が決めていないのか、
   システムが読めなかったのか、そもそも不要なのかが区別できない。
9. **判定は server canonical projection。** Dashboard は状態・staleness・
   差分・次の 1 操作を再導出しない(#351 / #380 / #387 と同じ)。有限語彙は
   `app/models.py` の `Literal` で一度だけ定義し、`test_interview_type_parity.py`
   の `FINITE_TYPE_NAMES` で TS union を拘束する。
10. **既存の human gate を一切緩めない。** 理解の確認 / Alignment 項目の確定 /
    提案の承認・編集・却下 / 差分の適用 / 観測の開始 / 採否の記録 / publish /
    Replay approval / 固定化承認 / reopen 承認 は不変。この層が追加する
    Journey・Requirement の確定と Design Option の採用も `decision_method: manual`。
    **設計案の採用は実装の適用ではない**(§3.6)。

---

## 1. なぜ Purpose Chain と違い「保存する」のか

Purpose Chain は行を保存しない projection である — 要素は
`interview_intent_item` と `current_understanding` という既存正本から毎回導出でき、
保存するのは「システムでは再導出できない人間の判断」の 2 テーブルだけだった。

UX Design Lineage は逆である。**Journey / Requirement / Solution Design は
どの既存行からも導出できない、新しく著述される内容**である。したがってこの層は
内容そのものを保存する。その代わり:

- **上流(Purpose / Capability)の内容は保存しない。** 参照 + 捕捉 digest だけ。
- **下流(Flow / Node / Component / Cell)の内容も保存しない。** 参照 + 捕捉 digest
  だけで、解決は読み取り時に各 kind の正本 1 つに対して行う
  (`node_design._LINK_KIND_TARGET_SOURCE` と同じ設計)。

この非対称が、この Epic が「Purpose Chain の複製」ではない理由である。

---

## 2. #407 — Journey / Step / Requirement / Artifact の永続化と API

### 2.1 モジュール

| file | 役割 |
| --- | --- |
| `apps/control-server/app/ux_design.py` | 決定的な domain service。LLM を呼ばない。 |
| `apps/control-server/app/routes/ux_design.py` | read/write API。`APIRouter(prefix="/ux-design", tags=["ux-design"])`。 |
| `apps/control-server/app/models.py` | `Literal` 語彙 + `*Out` / `*Request` モデル。 |
| `apps/control-server/app/db.py` | 下記 8 テーブル(`SCHEMA` 末尾へ追記)。 |
| `apps/control-server/tests/test_ux_design.py` | contract test。 |

### 2.2 identity

**identity は `(system_id, <kind>_key)`** — 開発者が与える安定 slug。
Evolution Node ADR-2 と同じ理由で、**上流の id からは決して導出しない**:

- Purpose 要素の id (`core_capability:<sha256(name)[:16]>`) は claim の **名前の
  hash** であり、名前を直せば別 id になる。Journey がそれを identity にすると、
  Capability を言い直しただけで Journey の履歴が切れる。
- 行 id からも導出しない。Understanding の再構築は `alignment_item` /
  `understanding_revision` を振り直す(#380)。

`journey_key` / `requirement_key` / `step_key` / `criterion_key` は
空文字を拒否する(422 `ux_design_key_required`)。

| entity | identity | 備考 |
| --- | --- | --- |
| UX Journey | `(system_id, journey_key)` UNIQUE | |
| Journey Step | `(journey_revision_id, step_key)` UNIQUE | `step_key` は revision を跨いで安定 |
| Requirement | `(system_id, requirement_key)` UNIQUE | |
| Acceptance Criterion | `(requirement_revision_id, criterion_key)` UNIQUE | |
| Artifact Reference | `(system_id, subject_kind, subject_key, uri)` の最新非 superseded 行 | `journey` / `requirement` / `solution_design` は stable key、親なしでは一意にならない `journey_step` / `design_option` は current row id の10進文字列 |

### 2.3 as-is / to-be は identity の属性であり、revision の属性ではない

```
UxJourneyPerspective = Literal["as_is", "to_be"]
```

`perspective` は `ux_journey`(identity 行)が持つ。revision に持たせると、
1 つの Journey が「現状の記述」から「目標の記述」へ**変わり得る**ことになり、
その revision 履歴は 2 つの別の主題の記録になってしまう。現状と目標は
**別の Journey** であり、`to_be` 側が `baseline_journey_id` で as-is を指す。

```
UxJourneyBaselineMode  = Literal["linked", "greenfield", "undecided"]
UxJourneyBaselineState = Literal["linked", "unresolved", "absent", "not_applicable"]
```

`baseline_state` は読み取り時の first match:

1. `perspective == "as_is"` → `not_applicable`(as-is に baseline は無い)
2. `baseline_mode == "greenfield"` → `not_applicable`(新規システムだと**開発者が
   明示的に宣言した**。宣言なき不在と区別する)
3. `baseline_mode == "undecided"` → `absent`
4. `baseline_journey_id` が同一 System の `as_is` Journey として解決する → `linked`
5. それ以外 → `unresolved`

`baseline_journey_id` は **同一 System の `as_is` Journey にのみ**設定できる
(他 System は 404、`to_be` を指すのは 422 `journey_baseline_not_as_is`)。

### 2.4 テーブル

すべて `system_id INTEGER NOT NULL` + `FOREIGN KEY (system_id) REFERENCES
systems (id) ON DELETE CASCADE`、timestamps は `REAL`、JSON は `TEXT`、
有限語彙は `CHECK (col IN (...))` で `Literal` を鏡写しにする。

```sql
CREATE TABLE IF NOT EXISTS ux_journey (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    journey_key         TEXT NOT NULL,
    perspective         TEXT NOT NULL CHECK (perspective IN ('as_is', 'to_be')),
    baseline_mode       TEXT NOT NULL DEFAULT 'undecided'
                            CHECK (baseline_mode IN ('linked', 'greenfield', 'undecided')),
    baseline_journey_id INTEGER,
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'ux-journey-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (baseline_journey_id) REFERENCES ux_journey (id) ON DELETE SET NULL,
    FOREIGN KEY (current_revision_id) REFERENCES ux_journey_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, journey_key)
);

CREATE TABLE IF NOT EXISTS ux_journey_revision (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_id        INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    revision_number   INTEGER NOT NULL,
    title             TEXT NOT NULL DEFAULT '',
    beneficiary       TEXT NOT NULL DEFAULT '',   -- 対象者
    usage_context     TEXT NOT NULL DEFAULT '',   -- 文脈
    entry_trigger     TEXT NOT NULL DEFAULT '',   -- トリガー
    value_arrival     TEXT NOT NULL DEFAULT '',   -- 価値到達
    summary           TEXT NOT NULL DEFAULT '',
    content_digest    TEXT NOT NULL,
    authored_by_kind  TEXT NOT NULL DEFAULT 'developer'
                          CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note       TEXT NOT NULL DEFAULT '',
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    schema_version    TEXT NOT NULL DEFAULT 'ux-journey-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_journey_revision (id) ON DELETE SET NULL,
    UNIQUE (journey_id, revision_number)
);

CREATE TABLE IF NOT EXISTS ux_journey_step (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    journey_revision_id   INTEGER NOT NULL,
    journey_id            INTEGER NOT NULL,
    system_id             INTEGER NOT NULL,
    step_key              TEXT NOT NULL,
    step_order            INTEGER NOT NULL,
    user_intent           TEXT NOT NULL DEFAULT '',
    system_response       TEXT NOT NULL DEFAULT '',
    success_criteria      TEXT NOT NULL DEFAULT '',
    failure_mode          TEXT NOT NULL DEFAULT '',
    recovery_path         TEXT NOT NULL DEFAULT '',
    evidence_expectation  TEXT NOT NULL DEFAULT '',
    evidence_source_kind  TEXT NOT NULL DEFAULT 'none'
                              CHECK (evidence_source_kind IN
                                  ('runtime_trace', 'human_report', 'external_analytics', 'none')),
    content_digest        TEXT NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_revision_id) REFERENCES ux_journey_revision (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    UNIQUE (journey_revision_id, step_key)
);
```

**Step は Journey revision の内容であり、独自の revision 鎖を持たない。**
順序付き Step 列こそが Journey の意味であり、Step を独立に版管理すると
「この Journey は当時どういう経路だったか」が 2 本の履歴の join になる。
`step_key` が revision を跨いで安定なので、差分は `step_key` の**完全一致**で
取れる(`understanding_diff` と同じ規則。文字列類似度・embedding は使わない)。

`evidence_source_kind` は §0-6 の受け皿である。`runtime_trace` を選んでも
それは「この Step の成功を確かめるには trace を見る」という**期待の宣言**であって、
trace が出たことを成功として表示してはならない。成果の判定は
`purpose_outcome_criterion` のままである。

```sql
CREATE TABLE IF NOT EXISTS ux_journey_upstream_ref (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id          INTEGER NOT NULL,
    journey_id         INTEGER NOT NULL,
    ref_kind           TEXT NOT NULL CHECK (ref_kind IN
                           ('purpose_element', 'purpose_relation', 'capability_entity')),
    target_ref         TEXT NOT NULL,
    target_row_id      INTEGER,
    captured_digest    TEXT NOT NULL DEFAULT '',
    captured_session_id INTEGER,
    note               TEXT NOT NULL DEFAULT '',
    decision_method    TEXT NOT NULL DEFAULT 'manual'
                           CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by         TEXT,
    created_at         REAL NOT NULL,
    superseded_by_id   INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_journey_upstream_ref (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ux_requirement (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    requirement_key     TEXT NOT NULL,
    requirement_kind    TEXT NOT NULL CHECK (requirement_kind IN
                            ('functional', 'non_functional', 'constraint', 'out_of_scope')),
    current_revision_id INTEGER,
    schema_version      TEXT NOT NULL DEFAULT 'ux-requirement-v1',
    created_by          TEXT,
    created_at          REAL NOT NULL,
    updated_at          REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (current_revision_id) REFERENCES ux_requirement_revision (id) ON DELETE SET NULL,
    UNIQUE (system_id, requirement_key)
);

CREATE TABLE IF NOT EXISTS ux_requirement_revision (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_id    INTEGER NOT NULL,
    system_id         INTEGER NOT NULL,
    revision_number   INTEGER NOT NULL,
    statement         TEXT NOT NULL DEFAULT '',
    rationale         TEXT NOT NULL DEFAULT '',
    constraint_text   TEXT NOT NULL DEFAULT '',
    out_of_scope_note TEXT NOT NULL DEFAULT '',
    content_digest    TEXT NOT NULL,
    authored_by_kind  TEXT NOT NULL DEFAULT 'developer'
                          CHECK (authored_by_kind IN ('developer', 'reasoning_model')),
    decision_method   TEXT NOT NULL DEFAULT 'manual'
                          CHECK (decision_method IN ('manual', 'reasoning_llm')),
    intelligence_run_id INTEGER,
    change_note       TEXT NOT NULL DEFAULT '',
    created_by        TEXT,
    created_at        REAL NOT NULL,
    superseded_by_id  INTEGER,
    schema_version    TEXT NOT NULL DEFAULT 'ux-requirement-revision-v1',
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_requirement_revision (id) ON DELETE SET NULL,
    UNIQUE (requirement_id, revision_number)
);

CREATE TABLE IF NOT EXISTS ux_requirement_acceptance_criterion (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    requirement_revision_id INTEGER NOT NULL,
    requirement_id          INTEGER NOT NULL,
    system_id               INTEGER NOT NULL,
    criterion_key           TEXT NOT NULL,
    criterion_order         INTEGER NOT NULL,
    statement               TEXT NOT NULL DEFAULT '',
    verification_method     TEXT NOT NULL DEFAULT 'manual_review'
                                CHECK (verification_method IN
                                    ('manual_review', 'replay', 'experiment',
                                     'runtime_observation', 'not_verifiable')),
    verification_note       TEXT NOT NULL DEFAULT '',
    content_digest          TEXT NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_revision_id) REFERENCES ux_requirement_revision (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    UNIQUE (requirement_revision_id, criterion_key)
);

CREATE TABLE IF NOT EXISTS ux_requirement_step_link (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id                   INTEGER NOT NULL,
    requirement_id              INTEGER NOT NULL,
    journey_id                  INTEGER NOT NULL,
    step_key                    TEXT NOT NULL,
    captured_journey_revision_id INTEGER,
    captured_step_digest        TEXT NOT NULL DEFAULT '',
    note                        TEXT NOT NULL DEFAULT '',
    decision_method             TEXT NOT NULL DEFAULT 'manual'
                                    CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by                  TEXT,
    created_at                  REAL NOT NULL,
    superseded_by_id            INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (requirement_id) REFERENCES ux_requirement (id) ON DELETE CASCADE,
    FOREIGN KEY (journey_id) REFERENCES ux_journey (id) ON DELETE CASCADE,
    FOREIGN KEY (captured_journey_revision_id) REFERENCES ux_journey_revision (id) ON DELETE SET NULL,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_requirement_step_link (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ux_design_artifact_reference (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id           INTEGER NOT NULL,
    subject_kind        TEXT NOT NULL CHECK (subject_kind IN
                            ('journey', 'journey_step', 'requirement',
                             'solution_design', 'design_option')),
    subject_key         TEXT NOT NULL,
    artifact_kind       TEXT NOT NULL CHECK (artifact_kind IN
                            ('wireframe', 'adr', 'spec', 'diagram', 'research_note', 'other')),
    title               TEXT NOT NULL DEFAULT '',
    uri                 TEXT NOT NULL,
    media_type          TEXT NOT NULL DEFAULT '',
    content_hash        TEXT NOT NULL,
    hash_algorithm      TEXT NOT NULL DEFAULT 'sha256' CHECK (hash_algorithm = 'sha256'),
    byte_size           INTEGER,
    verification_state  TEXT NOT NULL DEFAULT 'unverified'
                            CHECK (verification_state IN ('verified', 'unverified', 'unreachable')),
    verified_snapshot_id INTEGER,
    verified_commit_sha TEXT,
    verified_at         REAL,
    decision_method     TEXT NOT NULL DEFAULT 'manual'
                            CHECK (decision_method IN ('manual', 'reasoning_llm', 'deterministic')),
    created_by          TEXT,
    created_at          REAL NOT NULL,
    superseded_by_id    INTEGER,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_design_artifact_reference (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS ux_design_decision (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id         INTEGER NOT NULL,
    subject_kind      TEXT NOT NULL CHECK (subject_kind IN
                          ('journey', 'requirement', 'requirement_step_link',
                           'journey_upstream_ref', 'artifact_reference')),
    subject_key       TEXT NOT NULL,
    subject_row_id    INTEGER,
    decision          TEXT NOT NULL CHECK (decision IN
                          ('confirm', 'reject', 'retire', 'reinstate')),
    rationale         TEXT NOT NULL DEFAULT '',
    captured_digest   TEXT NOT NULL DEFAULT '',
    captured_revision_id INTEGER,
    decision_method   TEXT NOT NULL DEFAULT 'manual' CHECK (decision_method = 'manual'),
    decided_by        TEXT,
    superseded_by_id  INTEGER,
    created_at        REAL NOT NULL,
    FOREIGN KEY (system_id) REFERENCES systems (id) ON DELETE CASCADE,
    FOREIGN KEY (superseded_by_id) REFERENCES ux_design_decision (id) ON DELETE SET NULL
);
```

索引は各テーブルに最低 1 本、`(system_id, ..., id DESC)` 形式で置く。

### 2.5 状態は 3 つの独立した軸

1 つの表示語に 2 つの事実を持たせない(#366)。

```
UxDesignStatus      = Literal["proposed", "confirmed", "rejected", "retired"]
UxDesignRecheckState = Literal["current", "stale"]
UxRevisionState     = Literal["current", "superseded"]
```

* **`design_status`** — `ux_design_decision` の
  `(system_id, subject_kind, subject_key)` について最新の非 superseded 行から
  **導出**する。行が無ければ `proposed`。`confirm→confirmed`、`reject→rejected`、
  `retire→retired`、`reinstate→proposed`。
  列に保存しない理由: 保存した lifecycle 値はそれが記述する行から drift しうるが、
  導出した値はしえない(#337 / #338 / #349 と同じ規律)。
* **`recheck_state`** — 現在有効な `confirm` の `captured_digest` が現在の
  `content_digest` と食い違えば `stale`。**`design_status` は `confirmed` のまま**。
  確定を取り消すのではなく「あの内容に対して人が確定した」事実を残したまま
  再確認を促す(#388 と同じ)。
* **`revision_state`** — `superseded_by_id IS NULL` かどうか。内容の版であり、
  判断の状態ではない。

`authored_by_kind` は 4 つ目の軸(誰の声か)で、上の 3 つのどれとも独立。
`reasoning_model` が書いた revision が `confirmed` になることはあり得る —
それは「AI が書いた文を人が確認した」であって、`developer` 執筆に変わりはしない。

### 2.6 digest

```python
def content_digest(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
```

`purpose_chain.element_digest` / `understanding_brief.claim_digest` と同じ
canonicalization。**意味を持つ列だけ**を入れる:

| 対象 | digest 入力 |
| --- | --- |
| Journey revision | `title, beneficiary, usage_context, entry_trigger, value_arrival, summary` + 全 Step の `(step_key, step_order, content_digest)` |
| Journey Step | `step_key, user_intent, system_response, success_criteria, failure_mode, recovery_path, evidence_expectation, evidence_source_kind` |
| Requirement revision | `requirement_kind, statement, rationale, constraint_text, out_of_scope_note` + 全 criterion の `(criterion_key, content_digest)` |
| Acceptance criterion | `criterion_key, statement, verification_method, verification_note` |

`created_by` / `created_at` / `revision_number` / `change_note` は**入れない** —
再確認は**意味の変化**で促すのであって、記録の存在で促すのではない
(#308 が `confirmation_id` を除外し、#337 が Intent の `status` を除外したのと
同じ理由)。`step_order` は Journey revision 側の digest には入るが Step 自身の
digest には入らない: 並べ替えは Journey の意味を変えるが、その Step 自体の
意味は変えない。

### 2.7 上流参照の解決 — 4 つの独立した軸

`node_design.derive_node_lineage` の read-time 解決を踏襲し、
**kind ごとに正本を 1 つだけ**持つ:

| `ref_kind` | 正本 | `target_ref` |
| --- | --- | --- |
| `purpose_element` | `purpose_chain.derive_purpose_chain(...).elements` | 要素の stable id(例 `core_capability:ab12…`) |
| `purpose_relation` | 同 projection の `relations` | `f"{kind}:{src}->{tgt}"` |
| `capability_entity` | `understanding_capability_entity`(#312 の current head 構成) | `understanding_capability_entity.id` の 10 進文字列 |

**Capability は `understanding_capability_entity.id` を指す。** Purpose Chain の
`core_capability:<sha>` id は claim 名の hash であり、名前を言い換えれば別 id に
なる — Journey が数か月生きる前提の参照には使えない。`purpose_element` として
Capability を指すことも `ref_kind` 上は可能だが、その場合の弱さは
`target_resolution` に正直に出る(名前が動けば `unresolved`)。

報告する 4 軸(1 つに畳まない):

```
UxRefRelationStatus  = Literal["confirmed", "proposed", "derived"]
UxRefTargetResolution = Literal["resolved", "unresolved", "unavailable"]
UxRefRecheckState     = Literal["current", "stale", "not_captured"]
```

* `relation_status` — **その参照を誰が張ったか**。`decision_method` から
  `manual→confirmed` / `reasoning_llm→proposed` / `deterministic→derived`
  (`node_design._DECISION_METHOD_TO_RELATION_STATUS` と同じ表)。2 つ目の
  保存軸を作らない。
* `target_state` — **参照先自身の状態**。各正本の語彙をそのまま運ぶ(Purpose 要素なら
  `PurposeElementState`、relation なら `PurposeRelationStatus`、Capability なら
  `confirmed`/`superseded`)。翻訳しない(#380 superset 規則)。
* `target_resolution` — `resolved` / `unresolved`(正本は読めたが対象が無い) /
  `unavailable`(正本自体が読めなかった)。読めなかったことを既定値へ丸めない。
* `recheck_state` — `captured_digest` と現在の digest の比較。
  `not_captured` は `captured_digest` が空の legacy 行 —
  **`current` として扱わない**(#337 の `premise_not_captured` と同じ、
  fail-closed)。

`node_design` が digest を捕捉しないのに対しこの層が捕捉するのは、#407 の受入条件が
「source digest をテストする」「上流 digest が変わったとき stale として読める」を
要求しているからである。差分は意図的なものであり、`node_design` 側は変更しない。

### 2.8 Artifact Reference — 本文を持たず、勝手に取りに行かない

* **本文の列は存在しない。** 保存するのは `uri` / `media_type` / `content_hash` /
  `hash_algorithm='sha256'` / `byte_size` / `title` / `artifact_kind`。
* **probe-agent は任意の URI を fetch しない。** `http(s)` の取得は行わない
  (SSRF、および「対象リポジトリは Git から読む」Principle 5 の境界)。
  したがって:
  - `verification_state='verified'` に到達できるのは、`uri` が
    `repo:<path>` 形式で、pin された snapshot 上で `git show <sha>:<path>` として
    解決でき、その内容の sha256 が `content_hash` と一致した場合**だけ**。
    `verified_snapshot_id` / `verified_commit_sha` / `verified_at` を記録する。
  - それ以外(外部 URL、社内 Wiki、Figma など)は常に `unverified` —
    hash は**開発者が申告した値**であり、システムが確かめた値ではない。
    この 2 つを同じ表示にしない。
  - 一度 `verified` だった repo path が現在の snapshot で解決しなくなったら
    `unreachable`。`unverified` とは別の事実である。
* path traversal / repository 外 symlink は既存の `git_ops` の検証を再利用して
  拒否する(422 `artifact_uri_invalid`)。

### 2.9 変更伝播 — 下流方向のみ

| 変化 | 効果 |
| --- | --- |
| Purpose 要素 / relation の内容が動く | その `ux_journey_upstream_ref` が `stale`。Journey の内容は変えない |
| Capability entity が current head から外れる | 参照の `target_state='superseded'`。`target_resolution` は `resolved` のまま(実在する) |
| Journey revision(= Step 内容)が動く | その Step を指す `ux_requirement_step_link` が `stale`。Requirement 本文は変えない |
| Journey Step が消える | その link が `unresolved` |
| Requirement revision が動く | (#408)`solution_design_requirement_link` が `stale` |
| Solution Design Option の採用 | **下流に何も起こらない**(§3.6) |
| snapshot が動く | `verified` な artifact 参照と、snapshot 固定の `static_flow` link だけ |
| runtime trace | 何も確定しない(§0-6) |

**上流方向へは伝播しない。** Requirement を直しても Journey は `stale` に
ならない。ここを対称にすると、下流の作業が上流の確定を勝手に無効化する
(#388 が同じ理由で下流限定にしている)。

### 2.10 API

```
GET  /ux-design/journeys                              -> UxJourneyListOut
POST /ux-design/journeys                              -> UxJourneyOut
GET  /ux-design/journeys/{journey_key}                -> UxJourneyDetailOut
POST /ux-design/journeys/{journey_key}/revisions      -> UxJourneyDetailOut
GET  /ux-design/journeys/{journey_key}/revisions      -> UxJourneyRevisionListOut
GET  /ux-design/journeys/{journey_key}/diff
         ?from_revision=&to_revision=                 -> UxJourneyDiffOut
GET  /ux-design/journeys/{journey_key}/baseline-diff  -> UxJourneyDiffOut
POST /ux-design/journeys/{journey_key}/upstream-refs  -> UxUpstreamRefOut
GET  /ux-design/requirements                          -> UxRequirementListOut
POST /ux-design/requirements                          -> UxRequirementOut
GET  /ux-design/requirements/{requirement_key}        -> UxRequirementDetailOut
POST /ux-design/requirements/{requirement_key}/revisions -> UxRequirementDetailOut
GET  /ux-design/requirements/{requirement_key}/revisions -> UxRequirementRevisionListOut
GET  /ux-design/requirements/{requirement_key}/diff
         ?from_revision=&to_revision=                 -> UxRequirementDiffOut
POST /ux-design/requirements/{requirement_key}/step-links -> UxRequirementStepLinkOut
POST /ux-design/artifact-references                   -> UxArtifactReferenceOut
POST /ux-design/decisions                             -> UxDesignDecisionOut
```

* System scoping は `system_id: int = Depends(get_system_id)`、write は
  `principal: Principal = Depends(require_user)`。**`decided_by` / `created_by` /
  `decision_method` は request body から受けない** — `Principal` と route が
  決める(#337)。request model は `ConfigDict(extra="forbid")`。
* GET は何も書かない。page view を確認として保存しない。
* 有限拒否コード(`detail={"code": ..., "message": ...}`):

| code | status | 意味 |
| --- | --- | --- |
| `ux_design_key_required` | 422 | key が空 |
| `ux_design_key_conflict` | 409 | 同一 System に同じ key |
| `journey_baseline_not_as_is` | 422 | baseline に `to_be` を指定 |
| `journey_baseline_foreign_system` | 404 | 別 System の Journey |
| `journey_step_key_duplicated` | 422 | 1 revision 内で `step_key` 重複 |
| `journey_step_not_found` | 404 | link 先の `step_key` が現 revision に無い |
| `out_of_scope_requirement_not_verifiable` | 422 | `out_of_scope` に受入条件 |
| `artifact_uri_invalid` | 422 | traversal / 不正な `repo:` path |
| `artifact_hash_required` | 422 | `content_hash` 未指定 |
| `artifact_hash_invalid` | 422 | `content_hash` が64文字の16進SHA-256ではない |
| `ux_design_subject_not_found` | 404 | 決定対象が存在しない |
| `ux_design_decision_stale_digest` | 409 | 提示された digest と現在が不一致 |
| `ux_design_not_decidable` | 422 | `retired` を `confirm` しようとした 等 |

* 差分語彙: `UxDiffChangeKind = Literal["added", "removed", "changed", "unchanged"]`。
  照合は `step_key` / `criterion_key` の**完全一致のみ**。`baseline-diff` は
  baseline が無ければ `UxJourneyDiffOut.diff_state = "not_applicable"` を返し、
  空の差分を返さない。

### 2.11 #407 の受入条件(schema レベル)

1. revision / supersede / manual decision / `created_by` / source digest が
   永続化され、リロード後も同じ値が読める。
2. 別 System の `journey_key` / `requirement_key` / `baseline_journey_id` /
   `step_key` が漏れない・混入しない。
3. `unknown`(記録なし) / `unavailable`(読めない) / `not_applicable`
   (構造上不要)が別々の値として返る。
4. Journey / Step / Requirement は削除・上書きされず、過去 revision の内容と
   当時の判断が読める。
5. `design_status` が決定台帳から導出され、列に保存されていない。
6. 内容を変えると `recheck_state='stale'` になり、`design_status` は
   `confirmed` のまま、決定行も残る。
7. `authored_by_kind='reasoning_model'` の revision が、明示的な `confirm` なしに
   `confirmed` にならない。
8. `out_of_scope` 要件は受入条件を持てない。
9. artifact は本文を保存せず、外部 URI は `verified` に到達できない。
10. `content_digest` がリロードで再現する。

---

## 3. #408 — Requirement → Solution Design → Flow / Node / Cell

### 3.1 モジュール

`apps/control-server/app/solution_design.py` +
`apps/control-server/app/routes/solution_design.py`
(`APIRouter(prefix="/solution-designs", tags=["solution-design"])`) +
`apps/control-server/tests/test_solution_design.py`。

### 3.2 identity と複数案

```
SolutionDesignOptionDecision = Literal["adopt", "hold", "reject", "withdraw"]
SolutionDesignOptionStatus   = Literal["draft", "adopted", "held", "rejected", "withdrawn"]
```

* `solution_design`: `(system_id, design_key)` UNIQUE。
* `solution_design_option`: `(solution_design_id, option_key)` UNIQUE、
  append-only(訂正は `superseded_by_id`)。`title` / `approach` / `tradeoffs` /
  `risks` / `authored_by_kind` / `decision_method` / `content_digest`。
* `solution_design_requirement_link`: **多対多**。Requirement への FK を
  `solution_design` に置かない — 1 つの設計が複数要件を満たすのは実在する形であり、
  FK にすると要件ごとに設計行を複製することになって、案どうしの比較が割れる。
  `captured_requirement_revision_id` + `captured_digest` を持つ。
* `solution_design_decision`: `option_key` 単位、`decision_method='manual'` CHECK、
  append-only、`decided_by` は Principal 由来。

**採用の語彙を #407 の `ux_design_decision` と分けた理由**: 要件の
`confirm` は「この文が正しい」という非排他の判断であるのに対し、案の `adopt` は
**N 案の中から 1 つを選ぶ排他の判断**である。同じ表に入れると、排他性が
どこにも表現されない。

**排他性の実装**: 既に `adopted` の option があるとき別 option の `adopt` は
409 `solution_design_option_already_adopted` で**拒否する**。自動で前案を
`withdraw` しない — システムが人間の名前で「取り下げた」という決定を捏造する
ことになるからである。開発者が明示的に `withdraw` してから採用する。

### 3.3 実装対象への link

`solution_design_target_link` は `evolution_node_link` と同じ形にする:
`(target_kind CHECK 列挙, target_ref TEXT の安定文字列, target_row_id は
join の近道であって単独では信用しない, decision_method, note, 追記のみ)`。

```
SolutionTargetKind = Literal[
    "capability", "static_flow", "runtime_flow", "evolution_node",
    "component", "cell_definition", "cell_binding", "probe_point",
]
```

kind ごとの正本は 1 つだけ:

| `target_kind` | 正本 | `target_ref` | 追加の必須 |
| --- | --- | --- | --- |
| `capability` | `understanding_capability_entity`(current head) | entity id の 10 進文字列 | |
| `static_flow` | `code_entrypoints`(snapshot scoped) | `entrypoint_ref` | `captured_snapshot_id` 必須 |
| `runtime_flow` | `trace_spans.flow_id` | flow_id 文字列 | |
| `evolution_node` | `evolution_node` | `node_key` | |
| `component` | `components` | `component_id` | |
| `cell_definition` | `cell_definitions` | `cell_id` | |
| `cell_binding` | `cell_bindings` | binding id の 10 進文字列 | |
| `probe_point` | `probe_points`(`status='approved'` のみ) | probe point id | |

**`static_flow` と `runtime_flow` を 1 語にまとめない。** 前者は
`(system_id, snapshot_id, entrypoint_ref)` から毎回計算される静的経路、
後者は SDK が付けた実行時 correlation 文字列で、片方が current でも
もう片方は何も言っていない。1 つの表示語に 2 つの事実を持たせない(#366)。
**恒久的な Flow ID を捏造しない** — `flow_graph` の `flow-{i}` は 1 回の導出内
でしか安定せず、link の target にならない。`static_flow` の link は
`captured_snapshot_id` 無しでは作れない(422 `flow_target_requires_snapshot`)。

`probe_point` link は `evolution_node._require_approved_probe_point` と同じ
書き込み時検証を再利用する(未承認は 409、他 System は 404)。他の kind は
**読み取り時**に解決する(Phase 1 の `evolution_node_link` と同じ)。

`out_of_scope` 要件しか繋がっていない設計に target link を張ることは
422 `out_of_scope_requirement_not_implementable` で拒否する。
「やらないと決めたこと」に実装対象が付くのは、要件種別が意味を失った状態である。

### 3.4 link の読み取り状態

```
SolutionLinkState = Literal["current", "stale", "unresolved", "unavailable"]
SolutionLinkStaleReason = Literal[
    "requirement_changed", "design_changed", "target_changed",
    "snapshot_changed", "upstream_changed",
]
```

first match:

1. 対象の正本が読めなかった → `unavailable`
2. 対象が現在の正本に無い → `unresolved`
3. snapshot 固定 kind で `captured_snapshot_id` が現在の ready snapshot と違う
   → `stale` / `snapshot_changed`
4. `captured_digest` が現在の digest と違う → `stale` / `target_changed`
5. 上流(Requirement / Journey / Purpose)が `stale` → `stale` / `upstream_changed`
6. → `current`

`review_required` は 5 つ目の値ではなく、**`link_state != "current"` の
決定的な帰結**として owner 側に立つ有限の `review_reason` である。
状態語彙を増やさずに #408 の受入条件 2 を満たす。

### 3.5 変更起点の diff projection

```
UxChangeOrigin = Literal[
    "purpose", "capability", "journey", "requirement",
    "solution_design", "implementation_target", "snapshot",
]
```

`GET /solution-designs/{design_key}/change-origins` は、現在 `current` でない
参照を**どこで起きた変化か**で分類して返す。「何かが古い」ではなく
「Capability が変わった」「snapshot が動いただけ」を区別できることが、
既存システム改善で最初に必要な情報である。分類は各 link の
`stale_reason` + `ref_kind` / `target_kind` からの**決定的な写像**であり、
推測も score も使わない。

### 3.6 採用は実装ではない

**Solution Design Option の採用は、次のいずれも起こさない**(#408 受入条件 3):

- `evolution_node.maturity` の遷移
- Cell Improvement の状態変化
- `components.mode`(SDK policy `off`/`trace`/`shadow`)の変更
- patch の生成・適用、worktree への書き込み、publish job の作成
- Probe Plan / Probe Pattern の承認

採用が作るのは `solution_design_decision` の 1 行と、それに紐づく target link が
「採用済み案のもの」として読めるようになることだけである。これは
Evolution Node ADR-9(自動 maturity 遷移は存在しない)と同じ境界を、設計層の
側から守るものである。テストでこの 5 項目を明示的に assert する。

### 3.7 read-only handoff

```
GET /solution-designs/{design_key}/handoff -> SolutionDesignHandoffOut
```

`node_design.get_handoff` の規律をそのまま踏襲する:

* **参照だけを返し、内容をコピーしない。** コピーした評価 criterion は、元の
  `evolution_evaluation_policy` が superseded された後も current として読めてしまう。
* **解決できない参照は名前付きで残し、bundle を `incomplete` にする。**
  黙って落とすと「参照を失った handoff」と「最初から持っていない handoff」が
  区別できない(#408 受入条件 4)。
* **読めなかった section があるなら `handoff_state` は `unavailable`。**
  first match は `degraded_sections` → `unavailable`、`unresolved_references`
  → `incomplete`、それ以外 → `complete`。読み取りの失敗(`unavailable`)と参照の
  未解決(`incomplete`)は別の事実であり、どちらも「完成した bundle」として報告
  しない(§0 invariant 8)。`requirements` の読み取りが失敗したうえで
  `requirements: []` を `complete` として返すと、Requirement link を 1 つも
  持たない設計と見分けがつかなくなる。
* **参照の同定は完全一致のみ。** 例えば Node decomposition 参照は
  `node_decomposition_candidate.adopted_node_ids_json` を **parse した id 集合
  への所属**で決める。JSON 配列に対する部分文字列一致では node 1 が node 11 /
  21 / 100 の候補を継承してしまい、handoff が別 Node の decomposition を渡す
  (Principle 6)。target は保存済みの `target_row_id` ではなく `target_ref`
  (`node_key`)から読み取り時に解決する(§3.3 の「単独では信用しない」)。
* 返すのは: 採用 option、その target link 群(各 `link_state` 付き)、
  紐づく Requirement とその受入条件、Node decomposition 提案への参照、
  Probe Plan への参照、`evolution_evaluation_policy` への参照
  (**level ごとにグループ化して**返す。ADR-7 の分離を構造で保つ)。
* **合成 score を作らない。** 件数は返してよい。

### 3.8 #408 の受入条件

1. 1 つの Requirement を複数の Node / Flow で満たせ、1 つの Node が複数の
   Journey Step を支えられる(多対多が両方向で成立する)。
2. snapshot または上流 digest が変わったとき、該当 link が `stale` /
   `review_required` として読める。
3. 採用だけでは §3.6 の 5 項目が一切変化しない。
4. handoff で参照解決不能を黙って落とさない。
5. `static_flow` / `runtime_flow` が別の事実として読める。恒久 Flow ID を作らない。
6. `out_of_scope` 要件に実装対象を繋げない。
7. 既に採用済みの案があるとき、別案の採用は拒否される(自動 withdraw をしない)。

---

## 4. #409 — Design Studio UX と既存改善フローの統合

Dashboard のみ。**新しい endpoint を追加せず、server の判定を再導出しない。**

> 用語注意: 既存の `docs/project-intelligence.md` 「Issue #397 — Phase 2:
> Design Studio」は Evolution Node の設計層を指す。#409 の画面は
> **UX Design Studio**(`/ux-design-studio`)と呼び、#397 の概念と混同しない。

### 4.1 画面と導線

`apps/dashboard/src/pages/ux-design-studio.tsx` +
`apps/dashboard/src/components/ux-design/`。
`App.tsx` に route、`components/layout/sidebar.tsx` の既存 phase group に nav item
を 1 つ追加する(4 つ目の孤立ページを作らない)。

導線(すべて既存画面から **navigate であって execute ではない**、#358):

| from | to |
| --- | --- |
| Overview の Purpose Frame | その Capability を参照する Journey |
| Interview | 確定した Capability から Journey 作成 |
| Capability Map | Capability → 参照している Journey / Requirement |
| Flow Explorer | Flow → その Flow を target にしている Solution Design |
| Evolution Nodes | Node → その Node を target にしている Solution Design |
| UX Design Studio | 各 target へ戻る deep link |

### 4.2 段階的開示

* **「今決めるべきこと」は 1 つだけ**。first-match の決定表で server 側の
  状態から選ぶ(#380 `decide_next_action` と同じ形)。実装は
  `components/ux-design/model.ts` の `decideNextDesignAction`: 入力は
  `design_status` / `recheck_state` / `adopted_option_key` / `option_count`
  という **server が既に決めた値だけ**で、因果順(Journey → Requirement →
  Solution Design)に走る 11 行の first-match 表。同一条件に複数該当したときの
  tie-break は key の昇順で、score も recency ranking も使わない。
  `rejected` / `retired` は下流設計の起点となる確定済み成果物には数えず、
  それらだけが残る場合は新しい Journey / Requirement の作成へ戻す。
  **CTA は移動であって実行ではない**(§4.1 / #358) — tab と対象を選ぶだけで、
  操作そのものは移動先の panel が持つ primary action のままである。
  一覧が読めなかったとき(`unavailable`)と、決めることが無いとき(`settled`)は
  **どちらも CTA を持たない**。disabled のボタンではなく文で答える
  (#342 原則 P3 / #380)。
* **未到達の操作を disabled で露出しない**(#342 原則 P3)。前提が満たされて
  いない操作は表示しない。ただし「修正するには」のような**案内**は #356 の
  例外に従い、理由付きで表示してよい。
* 4 階層: 一覧 → Journey の Step 列 → Step に紐づく Requirement → 採用案と実装対象。

### 4.3 既存改善の比較表示

`as_is` / `to_be` / `changed` / `stale` / `unavailable` / `not_applicable` を
別々の表示にする。色だけで区別せずテキストマーカーを併記する。
`baseline_diff.diff_state == "not_applicable"` のときは「差分なし」ではなく
「比較対象の現状 Journey がありません(新規として宣言済み / 未設定)」を
`baseline_mode` に応じて出し分ける。

### 4.4 評価の提示

Journey Step の受入条件 / Flow-Capability 評価 / UX-Outcome criterion /
Node 評価を**関係付きで**並べる。**合成 score を作らない。**
`evolution_evaluation_policy` の 3 level は level ごとに分けて表示し、
1 つのリストへ混ぜない。`purpose_outcome_criterion` の
`not_observed` / `not_computed` はその文言のまま表示する。

### 4.5 #409 の受入条件

1. 既存システムで「現状 Journey → 変更要件 → 採用 Design → 影響 Flow/Node →
   評価証拠」を辿れる。
2. 新規システムで、実装・観測点が 1 つも無くても Journey / Requirement /
   Solution Design を確定できる。
3. runtime trace のみを UX 成功として表示しない。
4. 人間承認前に設計案が実装適用・policy 変更・publish を行わない。
5. 既存 Purpose Chain / System Interview / Capability Map / Flow Explorer /
   Evolution Node の回帰テストを含める。
6. accessibility: 見出し順 = 因果順、色単独で状態を示さない、キーボードで
   確定・訂正・採用へ到達できる、loading / empty / unknown / unavailable /
   not_applicable を区別する。

---

## 5. Migration と rollback

* **すべて新規テーブルであり、既存テーブルを 1 つも変更しない。** `db.py` の
  `SCHEMA` 末尾へ `CREATE TABLE IF NOT EXISTS` を追記するだけで、既存テーブルに
  対する `ALTER TABLE` も backfill も不要(既存行に対応物が無いので、legacy 行と
  いう概念自体が無い)。
* ただし **この Epic 自身のテーブルに対する migration は 1 つ存在する**:
  `db._migrate_solution_design_option_unique`。`solution_design_option` は最初の
  形で table-level の `UNIQUE (solution_design_id, option_key)` を持って出荷され、
  それが append-only 規律と矛盾していた — 訂正は新しい行を insert して旧行を
  supersede するので、置き換えようとしている当の行と衝突し、訂正を 1 度も記録
  できなかった。SQLite は table 制約をその場で落とせず `CREATE TABLE IF NOT
  EXISTS` も既存 DB を直せないので、テーブルを 1 度だけ再構築して全行を保存する。
  検出は table-level UNIQUE が作る暗黙 index(`origin == 'u'`)で、置き換え後の
  partial index は `origin == 'c'` を報告するため 2 度目以降は no-op になる。
  **これは #405 が自分で作ったテーブルの修復であって、既存正本への変更ではない。**
* 例外は `captured_digest` が空文字の行で、これは将来この層の内部で
  digest 捕捉より前に作られた行にだけ現れうる。`recheck_state='not_captured'`
  として fail-closed に扱い、`current` へ昇格させない(#337)。
* **rollback**: この Epic のテーブルを読む既存 consumer は存在しないので、
  route の登録を外せば機能全体が無効になる。データは残るが誰も読まない。
  テーブルの DROP は不要かつ非推奨(監査記録が消える)。
* **既存正本への書き込みは一切行わない。** `interview_*` / `purpose_*` /
  `understanding_*` / `evolution_node*` / `cell_*` / `components` /
  `probe_points` のどの行も、この Epic のコードは UPDATE / INSERT しない。
  これは #329 が origin 行へ書かないと決めたのと同じ境界で、テストで守る。

---

## 6. 非目標(Epic 全体)

* Purpose / Vision / Capability / Flow / Node / Cell の新しい正本を作る
* UX 要件を Purpose / Capability / Node へ二重に保存する
* 設計の完成度・充足率・confidence percentage を出す
* Node 評価 / Flow-Capability 評価 / UX-Outcome 評価を 1 つの score へ合成する
* AI に Journey / Requirement / 採用を自動確定させる
* runtime trace から利用者の成功・満足・継続意思を推論する
* 設計案の採用を Node maturity / Cell Improvement / SDK policy / patch 適用 /
  publish へ自動的に波及させる
* 恒久的な Flow ID を捏造する
* artifact の本文を DB へ複製する、または任意の URI を fetch する
* 初回に事業計画・ペルソナ一式・全 Journey の入力を強制する

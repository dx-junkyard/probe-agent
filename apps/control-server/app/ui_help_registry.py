"""UI 機能解説モード (Issue #440, Epic #436) — 唯一の正本レジストリ.

Overview / Interview / UX Design Studio / Journey Blueprint の主要な画面要素
について、「これは何か / どう使うか / どのドキュメントに書いてあるか」を
静的にコード管理する。LLM は一切呼ばない — 説明文はここに著述された固定
テキストであり、`decision_method` は常に `"deterministic"` である
(`docs/assistant-discussion.md` §0 / §3、CLAUDE.md Core Design Principle 6/7)。

このモジュールが持つのは「解説」という新しい成果物と、既存 Dashboard 画面の
要素への参照 (`screen_id` + `help_id`) だけである。新しい理解モデルは作らず、
上流データを一切保持・変換しない。

`help_id` は `<screen_id>.<area>[.<element>]` の安定な開発者管理キーで、行 id
からは決して導出しない (Epic #405 / #418 / #427 と同じ規律)。

probe-agent:
  role: Static UI help-mode registry (screen/section/element explanations)
  capability: ui-help-mode
  element_type: core
  consumers: [control-server, dashboard]
  operation_kind: read
  state_effects: []
  probe_value: Verify every help entry cites a real repository doc, every screen_id is a registered assistant screen, and no LLM is ever invoked to produce or select an explanation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Tuple

UI_HELP_REGISTRY_VERSION = "ui-help-v1"

# Finite vocabularies (Principle 6). Kept deliberately small — a help entry
# describes a screen, a section of a screen, or one decidable element on it.
UI_HELP_SCOPES: Tuple[str, ...] = ("screen", "section", "element")
UI_HELP_ACTION_KINDS: Tuple[str, ...] = ("navigate", "configure", "operate")


@dataclass(frozen=True)
class UiHelpDocRef:
    """A pointer into a real repository doc. No body text is copied here —
    the doc file is the source of truth and this is a reference to it."""

    doc_path: str
    title: str
    anchor: str = ""


@dataclass(frozen=True)
class UiHelpAction:
    """A suggested next step shown alongside an explanation. `kind` is one
    of `UI_HELP_ACTION_KINDS`; `target` is a route (navigate), an env var
    name (configure), or a short description of the in-place operation
    (operate). This never executes anything itself — it is a pointer."""

    label: str
    kind: str
    target: str = ""


@dataclass(frozen=True)
class UiHelpEntry:
    help_id: str
    screen_id: str
    scope: str
    title: str
    summary: str
    usage: str
    doc_refs: Tuple[UiHelpDocRef, ...] = field(default_factory=tuple)
    related_actions: Tuple[UiHelpAction, ...] = field(default_factory=tuple)
    related_help_ids: Tuple[str, ...] = field(default_factory=tuple)


# --- doc shorthands ----------------------------------------------------------

_NAV = "docs/system-understanding-navigation.md"
_WORKFLOW = "docs/system-interview-workflow-ux.md"
_PURPOSE = "docs/purpose-chain.md"
_UX_LINEAGE = "docs/ux-design-lineage.md"
_STAKEHOLDER = "docs/stakeholder-value-network.md"
_OBJECTIVE = "docs/product-objective-lineage.md"
_GLOSSARY = "docs/ui-glossary.md"
_INTEL = "docs/project-intelligence.md"


# --- Overview -----------------------------------------------------------------

_OVERVIEW_ENTRIES: Tuple[UiHelpEntry, ...] = (
    UiHelpEntry(
        help_id="overview",
        screen_id="overview",
        scope="screen",
        title="Overview",
        summary=(
            "Overview は「接続できたか」を確認する稼働メトリクス画面ではなく、"
            "開いた瞬間にこのシステムについて前回より賢くなり、根拠を理解した"
            "うえで次の一手を実行できる意思決定コックピットです。"
        ),
        usage=(
            "見出しの並び順がそのまま読む順です: System Brief → 今わかったこと "
            "→ 次にすること → 改善ループの現在地 → Runtime health。各領域は "
            "GET /overview がすでに決定した内容をそのまま表示するだけで、この "
            "画面では優先度や readiness を計算し直しません。"
        ),
        doc_refs=(
            UiHelpDocRef(
                _NAV,
                "Overview: System Intelligence Brief / 意思決定コックピット",
                "Overview: System Intelligence Brief / 意思決定コックピット（Issue #380-#384）",
            ),
        ),
        related_help_ids=(
            "overview.header",
            "overview.purpose_frame",
            "overview.brief",
            "overview.findings",
            "overview.next_action",
            "overview.loop_rail",
            "overview.objective",
            "overview.runtime_health",
        ),
    ),
    UiHelpEntry(
        help_id="overview.header",
        screen_id="overview",
        scope="section",
        title="Snapshot / 理解リビジョン / 最後の確認",
        summary=(
            "ページ見出し直下に、いま何を根拠にこの画面が語っているかを示す文脈"
            "情報を表示します: 固定した Snapshot とその新しさ、理解リビジョン、"
            "開発者が最後に理解を確認した日時です。"
        ),
        usage=(
            "この画面のすべての主張・発見・CTA はここで示された Snapshot と "
            "理解リビジョンを前提にしています。「最新ではない断面」と出ている"
            "ときは、下の発見や次の一手が古いコードに基づいている可能性があり"
            "ます。snapshot の新しさはサーバーの判定をそのまま表示するだけで、"
            "この画面で commit を比較し直すことはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_NAV, "ファーストビューと見出し"),
        ),
    ),
    UiHelpEntry(
        help_id="overview.purpose_frame",
        screen_id="overview",
        scope="section",
        title="Purpose Frame",
        summary=(
            "「誰のどんな現状を変えるか」「どの状態へ変えたいか」「システムが"
            "どう介入するか」の 3 要素を、System Brief より前に表示します。"
            "Epic の問い『何のためのシステムか』を最初に答えるための領域です。"
        ),
        usage=(
            "3 要素は既存の Intent Brief / Understanding Brief の claim を"
            "そのまま並べたもので、ここで新しく生成される主張はありません。"
            "最重要 unknown があるときだけ、文脈付きの質問が 1 件だけ添えられ"
            "ます。"
        ),
        doc_refs=(
            UiHelpDocRef(_PURPOSE, "Overview (Level 0)", "3.1 Overview (Level 0)"),
        ),
        related_help_ids=("overview.purpose_frame.question", "overview.brief"),
    ),
    UiHelpEntry(
        help_id="overview.purpose_frame.question",
        screen_id="overview",
        scope="element",
        title="Purpose Frame の質問",
        summary=(
            "現在の理解のなかで、判断を止めている最重要の unknown を 1 件だけ"
            "示す質問カードです。0 件または 1 件しか出ません — 複数の疑問を"
            "並べて優先度を選ばせることはしません。"
        ),
        usage=(
            "質問の『なぜ今この質問か』『答えるとどう先へ進めるか』を必ず読ん"
            "でから回答してください。このカードの CTA は Interview 画面の該当"
            "箇所へ移動するだけで、この場では何も確定しません。"
        ),
        doc_refs=(
            UiHelpDocRef(_PURPOSE, "質問選択", "2.4 質問選択"),
        ),
        related_actions=(
            UiHelpAction(label="Interview で質問に答える", kind="navigate", target="/interview"),
        ),
        related_help_ids=("overview.purpose_frame", "interview"),
    ),
    UiHelpEntry(
        help_id="overview.brief",
        screen_id="overview",
        scope="section",
        title="System Brief",
        summary=(
            "AI がこのシステムをどう理解しているかを、Vision / System Purpose "
            "/ Core Capabilities の 3 主張として要約します。工程上の現在地では"
            "なく、意味としての理解状態を示す領域です。"
        ),
        usage=(
            "各主張には確認状態 (確定済み / AI仮説 / 矛盾 / 情報不足 / 再確認"
            "が必要) と出所 (開発者の意図 / 実装事実 / AI仮説 など) が別々に"
            "付きます。この 2 軸は独立していて、どちらかを見て他方を推測しな"
            "いでください。"
        ),
        doc_refs=(
            UiHelpDocRef(
                _INTEL,
                "Understanding Brief と Decision Readiness",
                "Understanding Brief と Decision Readiness(Epic #351 / #352 / #353 / #354)",
            ),
        ),
        related_help_ids=("overview.brief.vision", "overview.brief.system_purpose", "overview.brief.capabilities"),
    ),
    UiHelpEntry(
        help_id="overview.brief.vision",
        screen_id="overview",
        scope="element",
        title="Vision",
        summary=(
            "誰のどんな状態をどう変えたいか、という Purpose とは別の主張です。"
            "根拠が無い Vision は自動的に『不確実』へ倒され、AI 仮説または"
            "不明としてしか表示されません。"
        ),
        usage=(
            "確定済みの Intent Brief の goal があれば、それが常にモデルの"
            "Vision より上位に表示されます。開発者自身が『何を変えたいか』を"
            "確定しているなら、そちらが優先されるということです。"
        ),
        doc_refs=(
            UiHelpDocRef(
                _INTEL,
                "Vision は Purpose と別の主張として持つ",
                "Understanding Brief と Decision Readiness(Epic #351 / #352 / #353 / #354)",
            ),
        ),
    ),
    UiHelpEntry(
        help_id="overview.brief.system_purpose",
        screen_id="overview",
        scope="element",
        title="System Purpose",
        summary=(
            "この Vision に対してこのシステムが担う役割を示す主張です。Vision"
            "（誰のどんな状態を変えたいか）と同じセクションに畳まず、別の主張"
            "として保持しています。"
        ),
        usage=(
            "確認状態は claim ごとに first-match で決まります (矛盾 > 確認後の"
            "変更 > 確認済み > 情報不足 > 仮説)。『確認済み』のまま古い内容が"
            "表示され続けることはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(
                _INTEL,
                "確認状態と出所は独立した 2 軸",
                "Understanding Brief と Decision Readiness(Epic #351 / #352 / #353 / #354)",
            ),
        ),
    ),
    UiHelpEntry(
        help_id="overview.brief.capabilities",
        screen_id="overview",
        scope="element",
        title="Core Capabilities",
        summary=(
            "AI が識別したこのシステムの中核機能の一覧です。0 件のときは "
            "Purpose だけの理解が『完成』とは読めないよう、要確認として扱われ"
            "ます。"
        ),
        usage=(
            "各 Capability も Vision / System Purpose と同じ確認状態・出所の"
            "2 軸で表示されます。個別の Capability を確認・訂正するには"
            "Interview 画面へ移動してください。"
        ),
        doc_refs=(
            UiHelpDocRef(
                _INTEL,
                "Understanding Brief と Decision Readiness",
                "Understanding Brief と Decision Readiness(Epic #351 / #352 / #353 / #354)",
            ),
        ),
        related_actions=(
            UiHelpAction(label="Interview で Capability を確認する", kind="navigate", target="/interview"),
        ),
    ),
    UiHelpEntry(
        help_id="overview.findings",
        screen_id="overview",
        scope="section",
        title="今わかったこと",
        summary=(
            "前回の理解確認から何が変わり、何が新しく分かったかを最大 3 件"
            "示します。11 種の有限 kind・4 段の有限 severity・3 値の有限 "
            "status を持ち、選定と重複排除はすべてサーバー側の決定的な規則で"
            "決まります。"
        ),
        usage=(
            "『前回』の基準は開発者自身が理解を確認した時刻です。まだ一度も"
            "確認していない場合は『比較の基準がまだ無い』と表示され、比較した"
            "結果として発見が無い場合や取得に失敗した場合とは別の文言で区別"
            "されます。"
        ),
        doc_refs=(
            UiHelpDocRef(_NAV, "今わかったこと（#382）"),
            UiHelpDocRef(_GLOSSARY, "状態語の規則", "3. 状態語の規則"),
        ),
    ),
    UiHelpEntry(
        help_id="overview.next_action",
        screen_id="overview",
        scope="section",
        title="次にすること",
        summary=(
            "次に行うべき 1 操作だけを、選定理由・完了条件・完了後に得られる"
            "価値とともに示します。14 行の first-match ルール表で決まり、必ず"
            "0 件または 1 件です。"
        ),
        usage=(
            "実行できない操作は disabled ボタンとして常設せず、『処理中です』"
            "『判定できませんでした』という文章で示します。この CTA は既存の"
            "人間確認ゲート (理解の確認・採否の記録・publish など) を一切"
            "迂回しません。"
        ),
        doc_refs=(
            UiHelpDocRef(_NAV, "次にすること（#383）"),
            UiHelpDocRef(_GLOSSARY, "操作ラベルの規則", "2. 操作ラベルの規則"),
        ),
    ),
    UiHelpEntry(
        help_id="overview.loop_rail",
        screen_id="overview",
        scope="section",
        title="改善ループの現在地",
        summary=(
            "システム理解の構築から候補生成・比較・採否記録までの改善ループの"
            "どこまで来ているかを示します。既存の `derive_user_phase` の判定を"
            "そのまま表示するだけです。"
        ),
        usage=(
            "このレールは移動のための案内であり、ここから直接何かを実行する"
            "ことはありません。次に進むべき画面へのリンクとして使ってくださ"
            "い。"
        ),
        doc_refs=(
            UiHelpDocRef(_NAV, "正本は `GET /overview` ひとつ"),
        ),
    ),
    UiHelpEntry(
        help_id="overview.objective",
        screen_id="overview",
        scope="section",
        title="目標(Objective)",
        summary=(
            "Vision・注力中の Product Objective・次に確認する Milestone・"
            "最重要 Gap・次に決めることを 1 つのカードにまとめます。合成した"
            "スコアや進捗率は表示しません。"
        ),
        usage=(
            "Milestone の『定義が確定したか』(design_status) と『達成した"
            "か』(achievement) は別々の軸です。ヘッダーの『Objective Map を"
            "見る』は常に使えるリンクで、次の 1 操作の有無に左右されません。"
        ),
        doc_refs=(
            UiHelpDocRef(_NAV, "Overview の `objective` セクション"),
            UiHelpDocRef(_OBJECTIVE, "Gap の 6 軸", "5.1 Gap の 6 軸"),
        ),
        related_actions=(
            UiHelpAction(label="Objective Map を見る", kind="navigate", target="/objective-map"),
        ),
    ),
    UiHelpEntry(
        help_id="overview.runtime_health",
        screen_id="overview",
        scope="section",
        title="Runtime health",
        summary=(
            "いま観測できているかを示す二次領域です。見出しは累積の `state` "
            "ではなく、いま生きている値である `freshness` です。"
        ),
        usage=(
            "error / 不一致 / replay の件数は直近 24 時間の有界ウィンドウで"
            "計測されます。累積の総数は details の中に折りたたまれており、"
            "『現在の稼働状態ではありません』と明記されています。"
        ),
        doc_refs=(
            UiHelpDocRef(_NAV, "Runtime health は二次領域（#384）"),
            UiHelpDocRef(_GLOSSARY, "状態語の規則", "3. 状態語の規則"),
        ),
        related_actions=(
            UiHelpAction(label="Component 一覧を見る", kind="navigate", target="/components"),
        ),
    ),
)


# --- Interview -----------------------------------------------------------------

_INTERVIEW_ENTRIES: Tuple[UiHelpEntry, ...] = (
    UiHelpEntry(
        help_id="interview",
        screen_id="interview",
        scope="screen",
        title="Interview",
        summary=(
            "AI と一緒にシステム理解を構築・確認するコックピット画面です。"
            "内部のステージ名は開発者に見せず、W0〜W7 の 8 状態のうち現在の"
            "1 つだけを常に表示します。"
        ),
        usage=(
            "画面は毎回 1 状態・1 主操作です。表示される状態はサーバーが"
            "永続化された事実だけから決定的に判定したもので、この画面で"
            "状態を再計算することはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "正準な作業状態", "2.1 正準な作業状態"),
        ),
        related_help_ids=(
            "interview.session_selector",
            "interview.workflow_state",
            "interview.status_summary",
            "interview.exceptions",
            "interview.work_surface",
            "interview.brief",
            "interview.unresolved_items",
            "interview.qa_progress",
            "interview.understanding_map",
            "interview.detail_pane",
            "interview.auxiliary_panel",
        ),
    ),
    UiHelpEntry(
        help_id="interview.session_selector",
        screen_id="interview",
        scope="section",
        title="セッション選択",
        summary=(
            "対象の Interview セッションを切り替えるドロップダウンと、現在の"
            "セッション番号・Snapshot 番号・ステータスの常設表示です。"
        ),
        usage=(
            "『セッション未選択』を明示的に選ぶと、自動選択はそれを上書きし"
            "ません。参加者・最終更新・根拠件数などの詳細は『セッション情報』"
            "ボタンから開く補助情報にあります。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "重複表示の解消", "10.4 重複表示の解消"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.workflow_state",
        screen_id="interview",
        scope="section",
        title="現在地カード",
        summary=(
            "W0〜W7 のうち現在の 1 状態と、そこで開発者が達成すべきことを"
            "1 文で示します。2 段階評価 (first-match のルール表 + 完了済み"
            "状態への後退の抑止) で決まり、`W1` (システムが調べている) だけが"
            "システム処理を待つ状態です。"
        ),
        usage=(
            "完了済みの状態へ戻るときは明示確認が必要です。回答修正などで"
            "自動的に手前へ戻ることはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "状態決定（2 段階評価、決定的）", "2.2 状態決定（2 段階評価、決定的）"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.status_summary",
        screen_id="interview",
        scope="section",
        title="Interview status サマリー",
        summary=(
            "完成度・理解カテゴリ数・要確認・未設定・質問合計・次にやること"
            "を示す補助サマリーです。現在地カードを置き換えるものではありませ"
            "ん。"
        ),
        usage=(
            "『次にやること』の主 CTA は移動であって実行ではありません。"
            "クリックすると対応する作業面や質問行へスクロールしてフォーカス"
            "するだけで、状態を完了させる操作はその作業面のボタンだけが行い"
            "ます。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "サマリーの主 CTA は「移動」であって「実行」ではない", "10.2 サマリーの主 CTA は「移動」であって「実行」ではない"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.exceptions",
        screen_id="interview",
        scope="section",
        title="戻り要求 / 例外",
        summary=(
            "未承諾の戻り要求や、ブロッキング・劣化中の例外をサマリーより上に"
            "常設表示します。復旧操作は主操作より先に出します。"
        ),
        usage=(
            "ブロッキング失敗が解消していない間は、対応する状態が復旧カード"
            "として表示され続けます。中断・再開はどの状態からも安全に行え、"
            "未解決の失敗は再開時に再表示されます。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "例外の分類", "5.1 例外の分類"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.work_surface",
        screen_id="interview",
        scope="section",
        title="作業面(現在の状態の主作業)",
        summary=(
            "現在の状態 (W0-A〜W7) に応じて内容と唯一の主操作が切り替わる"
            "作業カードです。1 状態につき主操作は 1 つだけです。"
        ),
        usage=(
            "たとえば W6 (差分を確認する) の主操作は『差分を確認した』という"
            "記録そのものであり、差分のダウンロードや表示だけではこの状態は"
            "完了しません。各状態の完了条件は現在地カードの説明と一致します。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "状態ごとの目的・開始条件・完了条件・遷移", "2.3 状態ごとの目的・開始条件・完了条件・遷移"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.brief",
        screen_id="interview",
        scope="section",
        title="Understanding Brief (Interview 内)",
        summary=(
            "Overview と同じ Understanding Brief を、Interview 側の詳細度で"
            "表示します。`W1` では構築中の表示そのものになり、`W2` ではこの"
            "Brief 自体が判断対象になって『この理解で進む』という主操作を"
            "内包します。"
        ),
        usage=(
            "Brief は 1 つの値から描かれ、作業面と全体像の 2 箇所に同時には"
            "出ません。W2 以外の状態では全体像として画面下部に表示されます。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "並び順（`R1`〜`R6` の配置規則）", "10.1 並び順（`R1`〜`R6` の配置規則）"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.unresolved_items",
        screen_id="interview",
        scope="element",
        title="未解決の確認事項",
        summary=(
            "意味的に近い質問を有限のサーバー決定済みグループにまとめ、上位"
            "3 グループを初期表示します。テキスト類似度や埋め込みでグルーピン"
            "グすることはありません。"
        ),
        usage=(
            "『残り N 件を表示』は隠れている質問の件数です。代表以外の質問も"
            "それぞれ個別に開くボタンを持ちます。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "並び順（`R1`〜`R6` の配置規則）", "10.1 並び順（`R1`〜`R6` の配置規則）"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.qa_progress",
        screen_id="interview",
        scope="element",
        title="Q&A の進捗",
        summary=(
            "回答済み・確認待ち・未回答の件数を示します。3 つの合計は常に"
            "質問の総数と一致し、`skipped` (後で回答) は未回答として数えられ"
            "ます。"
        ),
        usage=(
            "Q&A の取得自体に失敗しているときは、0 件ではなく『取得できてい"
            "ない』ことが別の表示で示されます。0 件と取得失敗を同じ表示にする"
            "ことはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_GLOSSARY, "状態語の規則", "3. 状態語の規則"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.understanding_map",
        screen_id="interview",
        scope="section",
        title="理解の全体マップ",
        summary=(
            "Vision / System purpose / Capabilities / API boundaries / "
            "Probe flow の 5 カテゴリの確認状態を一覧表示します。カード選択は"
            "クライアント限定の表示状態で、ワークフロー状態には影響しません。"
        ),
        usage=(
            "各カテゴリの状態は confirmed / review / missing の 3 値だけで、"
            "第 4 の値を持ちません。カテゴリを選ぶと右側の詳細ペインに切り替"
            "わります。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "コックピット表示層（Issue #356、後追い追記）", "9. コックピット表示層（Issue #356、後追い追記）"),
        ),
        related_help_ids=("interview.detail_pane",),
    ),
    UiHelpEntry(
        help_id="interview.detail_pane",
        screen_id="interview",
        scope="section",
        title="詳細・修正ペイン",
        summary=(
            "選択したカテゴリの根拠・理由・修正手段を表示します。修正できない"
            "項目は理由付きの disabled として残し、消してしまうと直せない理由"
            "が読めなくなるためです。"
        ),
        usage=(
            "『修正するには』ボタンは既存のパネルへスクロールしてフォーカス"
            "するだけで、新しい回答・編集・承認の経路をここで作ることはあり"
            "ません。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "コックピット表示層（Issue #356、後追い追記）", "9. コックピット表示層（Issue #356、後追い追記）"),
        ),
    ),
    UiHelpEntry(
        help_id="interview.auxiliary_panel",
        screen_id="interview",
        scope="section",
        title="補助情報 (セッション情報 / Intent Brief / 引き継ぎ など)",
        summary=(
            "セッション情報・Intent Brief・引き継ぎ・観測提案・まとめて修正・"
            "Q&A 全一覧・履歴と監査を 1 つの折りたたみ領域にまとめています。"
        ),
        usage=(
            "即時対応が必要なもの (未処理の引き継ぎ、失敗中の補助処理) は"
            "折りたたまず常設表示になります。0 件になった時点でこの補助領域"
            "へ降ります。"
        ),
        doc_refs=(
            UiHelpDocRef(_WORKFLOW, "段階的開示の境界", "10.3 段階的開示の境界"),
        ),
    ),
)


# --- UX Design Studio -----------------------------------------------------------

_UX_DESIGN_STUDIO_ENTRIES: Tuple[UiHelpEntry, ...] = (
    UiHelpEntry(
        help_id="ux-design-studio",
        screen_id="ux-design-studio",
        scope="screen",
        title="UX Design Studio",
        summary=(
            "UX Journey / Requirement / Solution Design の設計成果物を確認・"
            "記述する画面です。状態・差分・staleness はすべてサーバーの判定"
            "をそのまま表示し、この画面では再計算しません。"
        ),
        usage=(
            "4 階層の段階的開示です: Journey 一覧 → Journey の Step 列 → "
            "Step に紐づく Requirement → 採用された Solution Design と実装"
            "対象。設計案の採用は実装への適用・policy 変更・publish を一切"
            "行いません。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "Design Studio UX と既存改善フローの統合", "4. #409 — Design Studio UX と既存改善フローの統合"),
        ),
        related_help_ids=(
            "ux-design-studio.next_decision",
            "ux-design-studio.journey.list",
            "ux-design-studio.requirement.list",
            "ux-design-studio.solution_design.list",
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.next_decision",
        screen_id="ux-design-studio",
        scope="section",
        title="次に決めること",
        summary=(
            "Journey → Requirement → Solution Design の因果順に走る 11 行の"
            "first-match 表で、いま決めるべきことを 1 件だけ示します。件数や"
            "recency で並べ替えることはありません。"
        ),
        usage=(
            "CTA は該当タブと対象を選ぶだけの移動です。実行そのものは移動先"
            "のパネルが持つ主操作が行います。決めることが無いときや一覧が"
            "読めないときはどちらも CTA を持たず、文章で状態を説明します。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "段階的開示", "4.2 段階的開示"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.journey.list",
        screen_id="ux-design-studio",
        scope="section",
        title="UX Journey 一覧",
        summary=(
            "System 内の UX Journey を一覧します。identity は開発者が与える"
            "安定 slug (`journey_key`) で、Purpose 要素の id や行 id からは"
            "導出されません。"
        ),
        usage=(
            "一覧から選ぶと右側 (または下) の詳細に切り替わります。as-is / "
            "to-be はそれぞれ別の Journey として存在し、1 つの Journey が"
            "両方を兼ねることはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "identity", "2.2 identity"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.journey.detail",
        screen_id="ux-design-studio",
        scope="element",
        title="Journey 詳細",
        summary=(
            "選択した Journey の現行 revision、Step 列、as-is/to-be の"
            "baseline 状態を表示します。"
        ),
        usage=(
            "`design_status` (確定したか) / `recheck_state` (内容が動いたか) "
            "/ `revision_state` (最新版か) は独立した軸です。stale でも"
            "confirmed のままなことがあります — 確定を取り消さず再確認を"
            "促すだけだからです。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "状態は 3 つの独立した軸", "2.5 状態は 3 つの独立した軸"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.journey.baseline",
        screen_id="ux-design-studio",
        scope="element",
        title="as-is / to-be 差分",
        summary=(
            "to-be Journey が参照する as-is Journey との差分です。"
            "`baseline_mode` が『新規(greenfield)』と宣言されている場合と、"
            "まだ決めていない場合を区別して表示します。"
        ),
        usage=(
            "差分は Step の `step_key` の完全一致だけで判定され、類似度や"
            "埋め込みは使いません。追加・削除・変更・並び替え・変更なしの"
            "5 値です。"
        ),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "as-is / to-be diff", "§8.3 as-is / to-be diff"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.requirement.list",
        screen_id="ux-design-studio",
        scope="section",
        title="Requirement 一覧",
        summary=(
            "System 内の UX Requirement を一覧します。1 つの Requirement は"
            "複数の Journey Step から参照されえます。"
        ),
        usage=(
            "一覧から選ぶと詳細ペインに切り替わり、紐づく Solution Design や"
            "Feature を確認できます。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "identity", "2.2 identity"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.requirement.detail",
        screen_id="ux-design-studio",
        scope="element",
        title="Requirement 詳細",
        summary=(
            "選択した Requirement の現行 revision (statement / rationale / "
            "制約 / 対象外) と、紐づく Solution Design・Feature の一覧を"
            "表示します。"
        ),
        usage=(
            "対象外(out of scope)の記述は独立した項目として表示され、"
            "statement の一部として埋め込まれません。Feature への link は"
            "この画面から追加できますが、Requirement の revision 本文が"
            "コピーされることはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "テーブル", "2.4 テーブル"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.solution_design.list",
        screen_id="ux-design-studio",
        scope="section",
        title="Solution Design 一覧",
        summary=(
            "System 内の Solution Design を一覧します。1 つの Requirement に"
            "対して複数の設計案(option)が並立できます。"
        ),
        usage=(
            "一覧から選ぶと詳細ペインへ切り替わり、案ごとの採否・実装対象・"
            "評価証拠を確認できます。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "identity と複数案", "3.2 identity と複数案"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.solution_design.detail",
        screen_id="ux-design-studio",
        scope="element",
        title="Solution Design 詳細",
        summary=(
            "選択した Solution Design の option 一覧と、それぞれの採否"
            "(draft / adopted / held / rejected / withdrawn) を表示します。"
        ),
        usage=(
            "既に採用済みの option がある間、別の option を採用しようとする"
            "と拒否されます。システムが自動で前案を取り下げることはなく、"
            "取り下げは開発者が明示的に行う必要があります。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "identity と複数案", "3.2 identity と複数案"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.solution_design.handoff",
        screen_id="ux-design-studio",
        scope="element",
        title="採用案の実装ハンドオフ",
        summary=(
            "採用された option とその実装対象 link (Capability / Flow / "
            "Evolution Node / Component / Cell) を、読み取り専用で表示しま"
            "す。内容は一切コピーせず、参照を読み取り時に解決します。"
        ),
        usage=(
            "採用(adopt)は Node の maturity・Cell Improvement の状態・SDK "
            "policy mode・patch の適用・publish のいずれも変更しません。"
            "解決できない参照があるときは『不完全』と正直に表示されます。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "採用は実装ではない", "3.6 採用は実装ではない"),
            UiHelpDocRef(_UX_LINEAGE, "read-only handoff", "3.7 read-only handoff"),
        ),
    ),
    UiHelpEntry(
        help_id="ux-design-studio.solution_design.evaluation",
        screen_id="ux-design-studio",
        scope="element",
        title="評価証拠",
        summary=(
            "Journey Step の受入条件 / Flow-Capability 評価 / UX-Outcome "
            "criterion / Node 評価を、関係付けたまま別々のレベルとして表示"
            "します。合成した 1 つのスコアは作りません。"
        ),
        usage=(
            "`not_observed` / `not_computed` はそのままの文言で表示され、"
            "0 点や未達成として丸められることはありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_UX_LINEAGE, "評価の提示", "4.4 評価の提示"),
        ),
    ),
)


# --- Journey Blueprint -----------------------------------------------------------

_LANE_DOC_TITLES: Dict[str, str] = {
    "stakeholder_action": "利用者の行動",
    "touchpoint": "接点(チャネル)",
    "frontstage": "フロントステージ",
    "backstage": "バックステージ",
    "support": "サポート業務",
    "external": "外部連携",
    "requirement": "要件",
    "evidence": "エビデンス",
    "failure_recovery": "失敗と復旧",
}

_LANE_SUMMARIES: Dict[str, str] = {
    "stakeholder_action": (
        "この Step で利用者(Stakeholder)が何を行うかを、Step の "
        "`user_intent` と Step→Stakeholder の役割 link から表示します。"
    ),
    "touchpoint": (
        "この Step で使われる Value Exchange のチャネルを、Step→touchpoint "
        "link から表示します。"
    ),
    "frontstage": (
        "利用者から見える系を、Step の `system_response` と "
        "`frontstage` 種別の delivery link から表示します。"
    ),
    "backstage": (
        "裏側の処理を、`backstage` 種別の delivery link 経由で "
        "Requirement → Solution Design → Flow/Node のつながりとして表示"
        "します。"
    ),
    "support": (
        "サポート業務による関与を、`support` 種別の delivery link から"
        "表示します。"
    ),
    "external": (
        "外部システム・外部組織との連携を、`external` 種別の delivery "
        "link から表示します。"
    ),
    "requirement": (
        "この Step に紐づく Requirement と、その受入条件を表示します。"
    ),
    "evidence": (
        "期待されるエビデンス (`evidence_expectation` / "
        "`evidence_source_kind`) と、観測済みのエビデンス参照を表示します。"
    ),
    "failure_recovery": (
        "この Step の失敗モードと復旧経路 (`failure_mode` / "
        "`recovery_path`) を表示します。"
    ),
}

_SENTINEL_LANES = ("frontstage", "backstage", "support", "external")


def _lane_usage(lane_kind: str) -> str:
    if lane_kind in _SENTINEL_LANES:
        return (
            "`unknown` (まだ記録がない) と `not_applicable` (この Step には"
            "構造的に存在しない) は別の意味です。`not_applicable` は開発者が"
            "明示的に記録した場合だけに表示され、記録が無いことから自動で"
            "推測されることはありません。"
        )
    return (
        "`unknown` (まだ記録がない) と `unavailable` (取得できなかった) は"
        "別の意味です。このレーンには `not_applicable` はありません — この"
        "5 レーンは Step が構造的に持ち得ないという性質のものではないため"
        "です。"
    )


_JOURNEY_BLUEPRINT_LANE_ENTRIES: Tuple[UiHelpEntry, ...] = tuple(
    UiHelpEntry(
        help_id=f"journey-blueprint.lane.{lane_kind}",
        screen_id="journey-blueprint",
        scope="element",
        title=f"レーン: {_LANE_DOC_TITLES[lane_kind]}",
        summary=_LANE_SUMMARIES[lane_kind],
        usage=_lane_usage(lane_kind),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "The nine lanes", "§8.1 The nine lanes"),
        ),
        related_help_ids=("journey-blueprint.detail_pane",),
    )
    for lane_kind in (
        "stakeholder_action",
        "touchpoint",
        "frontstage",
        "backstage",
        "support",
        "external",
        "requirement",
        "evidence",
        "failure_recovery",
    )
)

_JOURNEY_BLUEPRINT_ENTRIES: Tuple[UiHelpEntry, ...] = (
    UiHelpEntry(
        help_id="journey-blueprint",
        screen_id="journey-blueprint",
        scope="screen",
        title="Journey Service Blueprint",
        summary=(
            "Journey の各 Step を横軸に、9 つのレーン(利用者の行動・接点・"
            "フロントステージ・バックステージ・サポート業務・外部連携・要件・"
            "エビデンス・失敗と復旧)を縦軸に表示する画面です。"
        ),
        usage=(
            "状態はすべてサーバーの判定をそのまま表示し、この画面では再計算"
            "しません。セルを選ぶと右側の詳細ペインに根拠が表示されます。"
        ),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "Journey Service Blueprint projection", "§8. Journey Service Blueprint projection (#423)"),
        ),
        related_help_ids=(
            "journey-blueprint.journey_select",
            "journey-blueprint.view_toggle",
            "journey-blueprint.detail_pane",
            "journey-blueprint.diff",
        ),
    ),
    UiHelpEntry(
        help_id="journey-blueprint.journey_select",
        screen_id="journey-blueprint",
        scope="section",
        title="Journey を選択",
        summary=(
            "表示対象の UX Journey を選ぶドロップダウンです。選択は URL に"
            "反映されるので、再読み込みや共有リンクでも同じ表示を再現できま"
            "す。"
        ),
        usage=(
            "一覧には as-is / to-be の別が併記されます。未選択のままだと"
            "『Journey を選択してください』と表示され、レーンは表示されませ"
            "ん。"
        ),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "as-is / to-be diff", "§8.3 as-is / to-be diff"),
        ),
    ),
    UiHelpEntry(
        help_id="journey-blueprint.view_toggle",
        screen_id="journey-blueprint",
        scope="section",
        title="Blueprint / as-is-to-be 差分の切り替え",
        summary=(
            "同じ Journey について、レーンごとの現況(Blueprint)と、as-is / "
            "to-be の差分表示を切り替えます。"
        ),
        usage=(
            "差分は Step の `step_key` の完全一致だけで判定されます。"
            "追加・削除・変更・並び替え・変更なしの 5 値で表示され、類似度"
            "や埋め込みでの判定はありません。"
        ),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "as-is / to-be diff", "§8.3 as-is / to-be diff"),
        ),
    ),
    *_JOURNEY_BLUEPRINT_LANE_ENTRIES,
    UiHelpEntry(
        help_id="journey-blueprint.detail_pane",
        screen_id="journey-blueprint",
        scope="section",
        title="セル詳細",
        summary=(
            "選択したレーンセルの根拠と、紐づく Requirement への移動導線を"
            "表示します。"
        ),
        usage=(
            "『Requirement を開く』は UX Design Studio の該当 Requirement へ"
            "移動するだけで、この画面から Requirement を編集することはでき"
            "ません。"
        ),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "Added links (owned here, content never copied)", "§8.2 Added links (owned here, content never copied)"),
        ),
        related_actions=(
            UiHelpAction(label="Requirement を開く", kind="navigate", target="/ux-design-studio?tab=requirements"),
        ),
    ),
    UiHelpEntry(
        help_id="journey-blueprint.diff",
        screen_id="journey-blueprint",
        scope="section",
        title="as-is / to-be 差分パネル",
        summary=(
            "as-is Journey と to-be Journey の Step の差分一覧です。"
            "baseline が『新規宣言』か『未決定』かによって、比較対象が無い"
            "理由の文言が変わります。"
        ),
        usage=(
            "`baseline_mode` が greenfield (新規) のときは『比較対象の現状"
            "Journey がありません(新規として宣言済み)』と表示され、"
            "undecided (未決定) のときとは異なる文言になります。"
        ),
        doc_refs=(
            UiHelpDocRef(_STAKEHOLDER, "as-is / to-be diff", "§8.3 as-is / to-be diff"),
            UiHelpDocRef(_UX_LINEAGE, "既存改善の比較表示", "4.3 既存改善の比較表示"),
        ),
    ),
)


UI_HELP_ENTRIES: Tuple[UiHelpEntry, ...] = (
    _OVERVIEW_ENTRIES
    + _INTERVIEW_ENTRIES
    + _UX_DESIGN_STUDIO_ENTRIES
    + _JOURNEY_BLUEPRINT_ENTRIES
)

HELP_BY_ID: Dict[str, UiHelpEntry] = {entry.help_id: entry for entry in UI_HELP_ENTRIES}


def entries_for_screen(screen_id: str) -> Tuple[UiHelpEntry, ...]:
    return tuple(entry for entry in UI_HELP_ENTRIES if entry.screen_id == screen_id)

# probe-agent 開発エージェントの入口

通常の改善は対象の仕様・実装・テストから直接進める。知識体系の一律参照や専用計画は不要。
製品の方針・現行仕様は[docs目次](docs/README.md)、実装・テストの詳細は[CLAUDE.md](CLAUDE.md)の該当節を参照する。

## 自律的な改善が難しい・行き詰まったと判断したとき

原因を絞れない、修正しても同型の失敗を繰り返す、検証結果が矛盾する、判断・調査コストが
膨らむ場合に以下を使う。試行回数の閾値を待つ必要はない。ユーザーが知識調査を依頼した場合も対象。

1. [改善サイクル](docs/04-operations/improvement-cycle/README.md)と
   [ローカル設定](improvement/local/project.md)を読み、対象版・目的・状況・保持条件を確認する。
   配布手順の入口は[Evolution Controller](vendor/evolution-controller-0.1.0-preview.1/AGENTS.md)。
2. 下記の課題・解決策の参照を行い、適合する知識と必要な追加観測を選ぶ。
3. [計画テンプレート](docs/04-operations/improvement-cycle/PLAN_TEMPLATE.md)の必要欄を使い、選択した候補・版・
   派生形または補完対策・選択理由・検証を記録してから、依頼範囲の作業を進める。
   小さい試行は既存の作業記録へ参照理由・期待・検証を短く書けばよく、専用IP/G/Sは作らなくてよい。
4. [結果テンプレート](docs/04-operations/improvement-cycle/RESULT_TEMPLATE.md)へ実測・未確認・負担を残し、
   新しい原因・解決はIKへ、一般化候補の利用結果は候補の適用実績へ結果記録のリンクで返す。

## 課題・解決策を参照するとき

- [課題ナレッジ](docs/02-challenges-and-decisions/issue-knowledge/README.md)から
  [索引](docs/02-challenges-and-decisions/issue-knowledge/index.md)と
  [原因の型の辞書](docs/02-challenges-and-decisions/issue-knowledge/dictionary.md)を調べ、関連するIKの対象版・原因・解決・証拠を読む。
- 必要な範囲で[状況観点との対応](improvement/local/perspective-map.md)や
  [課題・解決原理の一般化候補](improvement/local/patterns/README.md)を確認する。
  状況と保持条件に合う候補の適用・非適用・未確認条件を照合し、詳細を読む候補を選ぶ。
  全層の読破を開始条件にしない。
- 座標や解決タグの一致だけで処方しない。状況別の派生形と、併用が必要な補完対策を区別する。
  該当しなければ理由を短く残して既存の型・事例・Recipeから検討する。
- 参照・調査だけの依頼では、適合理由と根拠・未確認事項を回答または調査記録へ残せばよい。
  そのためだけに新規の改善計画や適用実績を作らない。

分類・成熟・改訂の正本は[taxonomy](docs/02-challenges-and-decisions/issue-knowledge/taxonomy.md)。
新しい知見は人の確認を待たずcandidateとして登録する。実案件で使い、効果なし・逆効果も
結果へ残して、担当エージェントが分類・適用条件・手順を自律的に修正する。
人の介入は、顕在化した問題を自律改善できていないと人が発見した場合に限る。
通常の登録・分類確定・効果評価の承認ゲートを設けない。これは製品の承認契約や作業権限を変更しない。
既存の4軸と個別事例を維持し、過去の解決済み記録・候補の導入・実際の効果確認を区別する。
配布物は変更せず、ローカルな変更と結果は導入先の文書へ記録する。

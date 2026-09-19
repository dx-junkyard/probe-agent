#!/usr/bin/env python3
"""Validate issue knowledge and deterministically render its Markdown index.

Uses the JSON-compatible subset of YAML front matter; standard library only.
"""
import argparse
from collections import Counter, defaultdict
from datetime import date
import itertools
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
REL = Path('docs/02-challenges-and-decisions/issue-knowledge')
CONFIDENCE = {'high', 'medium', 'low'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def iso(value):
    require(isinstance(value, str), '日付は文字列が必要')
    require(date.fromisoformat(value).isoformat() == value, '日付はYYYY-MM-DDが必要')


def strings(value):
    return isinstance(value, list) and all(nonempty(v) for v in value) and len(set(value)) == len(value)


def existing(root, value, docs_only=False):
    require(nonempty(value), '参照パスが空')
    p = Path(value)
    require(not p.is_absolute() and '..' not in p.parts, '参照はリポジトリ相対パスが必要')
    resolved = (root / p).resolve()
    require(root.resolve() in resolved.parents and resolved.is_file(), f'参照先がない: {value}')
    if docs_only:
        require(value.startswith('docs/') and p.suffix == '.md', f'docsのMarkdownが必要: {value}')


def metadata(path):
    parts = path.read_text().split('---', 2)
    require(len(parts) == 3 and not parts[0].strip(), f'{path.name}: front matterが必要')
    def no_duplicates(pairs):
        result = {}
        for k, v in pairs:
            require(k not in result, f'重複キー: {k}')
            result[k] = v
        return result
    return json.loads(parts[1], object_pairs_hook=no_duplicates), parts[2]


def validate_entry(root, path, e, body, vocabulary, patterns):
    require(re.fullmatch(r'IK-\d{4}', e['id']) and e['id'] != 'IK-0000', 'IDが不正')
    require(re.fullmatch(re.escape(e['id']) + r'-[a-z]+(?:-[a-z]+)*\.md', path.name), 'IDとファイル名が不一致')
    for key in ('title', 'target_revision'):
        require(nonempty(e[key]), f'{key}が必要')
    for key in ('recorded_at', 'observed_at'):
        iso(e[key])
    require(e['status'] in {'open', 'resolved', 'deferred', 'rejected'}, '不正な状態')
    require(strings(e['sources']) and e['sources'], 'sourcesが必要')
    for source in e['sources']:
        existing(root, source, docs_only=True)
    f = e['feature_context']
    require(nonempty(f['realizing']), '機能の目的が必要')
    require(strings(f['layers']) and f['layers'] and set(f['layers']) <= set(vocabulary['layers']), '不正な層')
    c = e['classification']
    require(set(c['axes']) == set(vocabulary['axes']) == set(c['axis_confidence']), '全軸の座標・確信度が必要')
    for axis, allowed in vocabulary['axes'].items():
        values = c['axes'][axis]
        require(strings(values) and 1 <= len(values) <= 2, f'{axis}: 1〜2値が必要')
        require(set(values) <= set(allowed) | {'none', 'unknown'}, f'{axis}: 不正な値')
        require(not (set(values) & {'none', 'unknown'}) or len(values) == 1, f'{axis}: 空値の混在')
        confidence = c['axis_confidence'][axis]
        require(confidence in CONFIDENCE, '不正な確信度')
        require(values != ['unknown'] or confidence != 'high', 'unknownにhighは不可')
    require(any(v != ['none'] for v in c['axes'].values()), '全軸noneは再検討が必要')
    require(c['cause_status'] in {'confirmed', 'hypothesis'}, '不正な原因確度')
    require(nonempty(c['basis']), '原因の根拠が必要')
    if any(v == ['unknown'] for v in c['axes'].values()):
        require(c['cause_status'] == 'hypothesis', 'unknownは仮説が必要')
    if c['cause_status'] == 'hypothesis':
        require(c['basis'].startswith('仮説:'), '仮説の根拠を明示する')
    require(c['review'] in {'candidate', 'confirmed'}, '不正な分類レビュー状態')
    if c['review'] == 'confirmed':
        require(nonempty(c['reviewed_by']), '分類確定者が必要')
        iso(c['reviewed_at'])
    else:
        require(c['reviewed_by'] is None and c['reviewed_at'] is None, '候補に確定者を付けない')
    all_values = {a + '.' + v for a, values in vocabulary['axes'].items() for v in values}
    require(isinstance(c['proposals'], list), 'proposalsは配列')
    for p in c['proposals']:
        require(p['kind'] in {'axis', 'value'}, '不正な提案種別')
        require(p['target_axis'] in vocabulary['axes'] if p['kind'] == 'value' else p['target_axis'] is None, '不正な提案先')
        require(strings(p['neighbor_of']) and p['neighbor_of'] and set(p['neighbor_of']) <= all_values, '不正な提案の相手')
        require(nonempty(p['statement']) and p['confidence'] in CONFIDENCE, '提案の説明・確信度が必要')
    require(e['generalization']['level'] in {'instance', 'repo_pattern', 'general'}, '不正な一般化')
    require(nonempty(e['generalization']['general_form']), '一般形が必要')
    require(e['pattern'] in patterns, '辞書に存在しない型')
    for field in ('discovery', 'resolution'):
        p = e[field]['perspective']
        require(strings(p) and 1 <= len(p) <= 2 and set(p) <= set(vocabulary[field]), f'{field}: 不正な観点')
        require(nonempty(e[field]['note']), f'{field}: 説明が必要')
    r = e['resolution']
    require(r['perspective'][0] != 'guardrail_fix', 'guardrail_fixは主観点にできない')
    require(not set(r['perspective']) & {'pending', 'deferred_decision'} or len(r['perspective']) == 1, '未解決・保留観点の混在')
    require(strings(r['landed_in']) and strings(r['verification']), '着地先・検証はパスの配列')
    for ref in r['landed_in'] + r['verification']:
        existing(root, ref)
    if e['status'] == 'resolved':
        iso(e['resolved_at'])
        require(e['observed_at'] <= e['resolved_at'], '解決日が観測日より前')
        require(r['landed_in'] and r['verification'], '解決には着地先と検証証拠が必要')
        require(not set(r['perspective']) & {'pending', 'deferred_decision'}, '解決済みに未解決観点は不可')
    else:
        require(e['resolved_at'] is None, '未解決に解決日は不可')
    if e['status'] in {'open', 'deferred'}:
        require(r['perspective'] == (['pending'] if e['status'] == 'open' else ['deferred_decision']), '課題状態と解決観点が不一致')
    for field in ('related', 'view_of'):
        require(strings(e[field]) and e['id'] not in e[field], '関連IDが不正')
    require(isinstance(e['history'], list), 'historyは配列')
    for h in e['history']:
        iso(h['date'])
        require(nonempty(h['field']) and nonempty(h['reason']) and 'from' in h and 'to' in h, '変更履歴が不完全')
    for heading in ('課題', '発見の観点', '解決の観点', '一般化'):
        require(f'## {heading}\n' in body, f'本文の{heading}が必要')


def load(root=ROOT):
    base = root / REL
    match = re.search(r'```json\n(.*?)\n```', (base / 'taxonomy.md').read_text(), re.S)
    require(match is not None, 'taxonomyの機械用語彙がない')
    vocabulary = json.loads(match[1])
    all_values = {a + '.' + v for a, vs in vocabulary['axes'].items() for v in vs}
    for pair in vocabulary['neighbors']:
        require(len(pair) == 2 and len(set(pair)) == 2 and set(pair) <= all_values, '不正な隣接値')
    for p in vocabulary['provisional_values']:
        require(p['value'] in all_values and strings(p['neighbor_of']) and p['neighbor_of'] and set(p['neighbor_of']) <= all_values, '不正な暫定値')
        iso(p['introduced_at'])
    patterns, family = {}, None
    for line in (base / 'dictionary.md').read_text().splitlines():
        if line.startswith('### '):
            family = line[4:]
        if line.startswith('#### '):
            require(family and line[5:] not in patterns, '族なし・重複の型')
            patterns[line[5:]] = family
    entries = []
    for path in sorted((base / 'entries').glob('*.md')):
        try:
            e, body = metadata(path)
            validate_entry(root, path, e, body, vocabulary, patterns)
        except (ValueError, KeyError, TypeError) as exc:
            raise ValueError(f'{path.name}: {exc}') from exc
        e['_path'] = path.name
        entries.append(e)
    ids = {e['id'] for e in entries}
    require(len(ids) == len(entries), '重複ID')
    require(set(patterns) == {e['pattern'] for e in entries}, '参照されていない辞書の型')
    for e in entries:
        require(set(e['related'] + e['view_of']) <= ids, f"{e['id']}: 存在しない関連ID")
    return entries, vocabulary, patterns


def groups(entries):
    parent = {e['id']: e['id'] for e in entries}
    def find(k):
        while parent[k] != k:
            k = parent[k]
        return k
    for e in entries:
        for other in e['view_of']:
            a, b = sorted([find(e['id']), find(other)])
            parent[b] = a
    return {k: find(k) for k in parent}


def group(e):
    axes = e['classification']['axes']
    if any(v == ['unknown'] for v in axes.values()):
        return '未判定'
    return '構造・接続・統制' if any(v != ['none'] for a, v in axes.items() if a != 'processing') else '局所'


def render(entries, vocabulary, patterns):
    roots = groups(entries)
    def link(e):
        return f"[{e['id']}](entries/{e['_path']})"
    def links(es):
        return '、'.join(link(e) for e in es) or 'なし'
    def coord(e):
        return ' / '.join(a + '=' + '+'.join(v) for a, v in e['classification']['axes'].items())
    def clean(s):
        return s.replace('|', '\\|').replace('\n', ' ')
    lines = ['# 課題ナレッジ索引', '', '[入口](README.md) · [辞書](dictionary.md) · [分類体系](taxonomy.md)', '',
             '> 機械生成。編集はentriesへ。`python3 docs/tools/issue_knowledge.py` で更新。',
             '> 状態は各エントリの観測時点・対象版の記録。現行製品の健康状態や全課題の網羅率ではない。', '', '## 概況', '']
    lines += [f'- エントリ: {len(entries)} / 独立原因: {len(set(roots.values()))}']
    for label, values in [('状態', [e['status'] for e in entries]), ('分類レビュー', [e['classification']['review'] for e in entries]), ('群', [group(e) for e in entries])]:
        lines.append(f'- {label}: ' + '、'.join(f'{k} {n}' for k, n in sorted(Counter(values).items())))
    lines += ['', '## 全エントリ', '', '| ID | 課題 | 状態 / 原因 / 分類 | 群 | 座標 | 観測日 |', '| --- | --- | --- | --- | --- | --- |']
    for e in entries:
        c = e['classification']
        lines.append(f"| {link(e)} | {clean(e['title'])} | {e['status']} / {c['cause_status']} / {c['review']} | {group(e)} | {coord(e)} | {e['observed_at']} |")
    lines += ['', '## 型・族と実際の座標', '']
    for p, family in patterns.items():
        es = [e for e in entries if e['pattern'] == p]
        confirmed = {roots[e['id']] for e in es if e['classification']['review'] == 'confirmed'}
        lines += [f"### {p}", '', f"族: {family} / {'成立' if len(confirmed) >= 2 else '暫定'} / 分類確定の独立原因: {len(confirmed)}", '', links(es)]
        lines += ['- ' + c for c in sorted({coord(e) for e in es})] + ['']
    for title, getter in [('軸・値', lambda e: [a+'.'+v for a, vs in e['classification']['axes'].items() for v in vs]), ('発見観点', lambda e: e['discovery']['perspective']), ('解決観点', lambda e: e['resolution']['perspective']), ('層', lambda e: e['feature_context']['layers'])]:
        lines += [f'## {title}', '']
        buckets = defaultdict(list)
        for e in entries:
            for v in getter(e):
                buckets[v].append(e)
        lines += [f'- `{v}`: {links(es)}' for v, es in sorted(buckets.items())] + ['']
    lines += ['## 軸の組合せ', '']
    pairs = Counter()
    for e in entries:
        active = [a for a, v in e['classification']['axes'].items() if v not in (['none'], ['unknown'])]
        pairs.update(itertools.combinations(sorted(active), 2))
    lines += [f'- {a} + {b}: {n}' for (a, b), n in sorted(pairs.items())] or ['なし']
    lines += ['', '## レビュー待ち・仮説・未解決', '']
    for label, predicate in [('分類候補', lambda e: e['classification']['review'] == 'candidate'), ('原因仮説', lambda e: e['classification']['cause_status'] == 'hypothesis'), ('unknownあり', lambda e: ['unknown'] in e['classification']['axes'].values()), ('未解決・保留', lambda e: e['status'] in {'open','deferred'})]:
        lines += [f'- {label}: {links([e for e in entries if predicate(e)])}']
    for e in entries:
        low = [a for a, c in e['classification']['axis_confidence'].items() if c != 'high']
        if low:
            lines.append(f"- {link(e)} 確信度low/medium: {', '.join(low)}")
    lines += ['', '## 同一原因の束', '']
    lines += [f'- {r}: {links([e for e in entries if roots[e["id"]] == r])}' for r in sorted(set(roots.values()))]
    lines += ['', '## 収録出典と範囲', '', '以下は収録済みの出典のみ。未収録文書や課題数を分母にした被覆率ではない。', '']
    for source in sorted({s for e in entries for s in e['sources']}):
        rel = '../../' + source.removeprefix('docs/')
        lines.append(f'- [{source}]({rel}): {links([e for e in entries if source in e["sources"]])}')
    lines += ['', '## 軸・値の提案', '']
    proposals = defaultdict(list)
    for e in entries:
        for p in e['classification']['proposals']:
            proposals[(p['kind'], p['target_axis'] or '', tuple(sorted(p['neighbor_of'])))].append((e,p))
    if not proposals:
        lines.append('提案なし。語彙不足と調査不足を分けてレビューする。')
    for key, ps in sorted(proposals.items()):
        count = len({roots[e['id']] for e,p in ps if p['confidence'] == 'high'})
        lines += [f"- {key}: highの独立原因 {count} / {'設定候補' if count >= 3 else '蓄積中'}"]
        lines += [f"  - {link(e)}: {clean(p['statement'])} ({p['confidence']})" for e,p in ps]
    lines += ['', '## 暫定値の再評価', '']
    if not vocabulary['provisional_values']:
        lines.append('暫定の追加値なし。')
    for p in vocabulary['provisional_values']:
        axis, value = p['value'].split('.')
        used = [e for e in entries if value in e['classification']['axes'][axis]]
        count = len({roots[e['id']] for e in used if e['classification']['review'] == 'confirmed'})
        queue = [e for e in entries if any(v in e['classification']['axes'][a] and e['classification']['axis_confidence'][a] != 'high' for a,v in (n.split('.') for n in p['neighbor_of']))]
        lines += [f"- {p['value']}: 使用 {links(used)} / 確定独立原因 {count} / {'成立レビュー可' if count >= 2 else '暫定'}", f'  - 再評価: {links(queue)}']
    lines += ['', '## 改善サイクルへの還流', '', '層と発見月の分布は点検の入口。型の複数出現だけで「解決後の再発」と断定しない。', '']
    cycle = [e for e in entries if any(l.startswith('cycle_') for l in e['feature_context']['layers'])]
    lines += ['- サイクル層: ' + links(cycle)]
    monthly = Counter((e['observed_at'][:7], e['discovery']['perspective'][0]) for e in entries)
    lines += [f'- {month} / {perspective}: {count}' for (month,perspective),count in sorted(monthly.items())]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='検査のみ。索引の差分も失敗にする')
    args = parser.parse_args()
    try:
        entries, vocabulary, patterns = load()
        output = render(entries, vocabulary, patterns)
        path = ROOT / REL / 'index.md'
        if args.check:
            require(path.exists() and path.read_text() == output, 'index.mdが未同期。生成コマンドを実行してください')
        else:
            path.write_text(output)
        print(f'OK: {len(entries)} entries; index ' + ('synchronized' if args.check else 'generated'))
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

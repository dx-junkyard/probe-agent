#!/usr/bin/env python3
"""Check local knowledge links and explicit versions in linked cycle records.

No network access or writes. Anchors, reference-style Markdown and semantic
effectiveness are outside this small check; no new record schema is required.
"""
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[2]
DIRECTORIES = (
    'docs/02-challenges-and-decisions/issue-knowledge',
    'docs/02-challenges-and-decisions/improvement-plans',
    'docs/03-validation/improvement-cycle',
    'docs/04-operations/improvement-cycle',
    'improvement/local',
    'improvement/records',
)
ENTRANCES = ('AGENTS.md', 'CLAUDE.md', 'docs/README.md')


def local_targets(path, text):
    text = re.sub(r'^```[^\n]*\n.*?^```\s*$', '', text, flags=re.M | re.S)
    for match in re.finditer(r'\[[^\]\n]*\]\(([^)\n]+)\)', text):
        target = match[1].strip().strip('<>')
        url = urlsplit(target)
        if url.scheme or url.netloc or not url.path:
            continue
        yield (path.parent / unquote(url.path)).resolve()


def version(text):
    # Read the declaration, not versions mentioned in dated correction notes.
    for line in text.splitlines():
        if re.search(r'\*\*(?:検索・)?接続方式:|^## 観点の選択|^ローカル接続 ', line):
            match = re.search(r'`(probe-agent-ec/[^`]+)`', line)
            if match:
                return match[1]
    return None


def record_id(text):
    match = re.match(r'# (IP|IR|G)-(\d{8}-\d+)\b', text)
    return match.groups() if match else None


def check(root=ROOT):
    root = root.resolve()
    paths = {p for directory in DIRECTORIES for p in (root / directory).rglob('*.md')}
    paths.update(root / name for name in ENTRANCES)
    texts = {p: p.read_text() for p in paths if p.is_file()}
    errors = [f'{p.relative_to(root)}: missing entrance' for p in paths if not p.is_file()]
    count = 0
    for path, text in sorted(texts.items()):
        for target in local_targets(path, text):
            count += 1
            if not target.exists():
                errors.append(f'{path.relative_to(root)}: missing link {target}')
                continue
            other = texts.get(target, '')
            source_id, target_id = record_id(text), record_id(other)
            if not source_id or not target_id or target_id[0] not in {'IP', 'IR'}:
                continue
            # Guidance owns its linked plan/result. IP/IR comparisons must be
            # for the same cycle, not a reference to an earlier cycle.
            if source_id[0] != 'G' and source_id[1] != target_id[1]:
                continue
            left, right = version(text), version(other)
            if left and right and left != right:
                errors.append(f'{path.relative_to(root)} -> {target.relative_to(root)}: version mismatch {left} != {right}')
    return len(texts), count, errors


def main():
    files, links, errors = check()
    if errors:
        print('\n'.join(errors), file=sys.stderr)
        return 1
    print(f'OK: {files} documents; {links} local links; declared cycle versions consistent')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

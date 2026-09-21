#!/usr/bin/env python3
"""Build an allowlisted, immutable preview; verify without repository access."""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

VERSION = '0.1.0-preview.1'
PACKAGE = 'evolution-controller-' + VERSION
FILES = (
    'README.md', 'AGENTS.md', 'situation-model.md', 'workflow.md',
    'self-improvement.md', 'COMPATIBILITY.md', 'CHANGELOG.md',
    'PROVENANCE.md', 'NOTICE.md', 'knowledge/perspectives.md',
    'templates/project.md', 'templates/pattern.md', 'templates/cycle.md',
    'templates/situation.md', 'templates/guidance-feedback.md',
    'examples/pipeline.md',
)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def check_docs(payload):
    for name, data in payload.items():
        if not name.endswith('.md'):
            continue
        text = data.decode('utf-8')
        if '/Users/' in text or '/private/' in text:
            raise ValueError(f'local path in {name}')
        if sum(line.startswith('```') for line in text.splitlines()) % 2:
            raise ValueError(f'unclosed fence: {name}')
        for target in re.findall(r'\]\(([^)]+)\)', text):
            if '://' in target or target.startswith('#'):
                continue
            target = target.split('#')[0]
            parts = list(PurePosixPath(name).parent.parts)
            for part in PurePosixPath(target).parts:
                if part == '..':
                    if not parts:
                        raise ValueError(f'escaping link: {name}: {target}')
                    parts.pop()
                elif part != '.':
                    parts.append(part)
            if '/'.join(parts) not in payload:
                raise ValueError(f'broken link: {name}: {target}')


def make_archive(source):
    payload = {}
    for name in FILES:
        path = source / name
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(source.resolve()):
            raise ValueError(f'invalid source: {name}')
        payload[name] = path.read_bytes()
    check_docs(payload)
    hashes = {name: sha(data) for name, data in sorted(payload.items())}
    snapshot = sha(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode())
    manifest = {
        'release_id': PACKAGE, 'version': VERSION, 'status': 'preview',
        'created_date': '2026-09-20', 'source_snapshot_sha256': snapshot,
        'source_identity': 'allowlisted working-tree content; not identified by git HEAD',
        'components': {'situation': '0.1', 'roles': '0.1', 'perspectives': '0.1', 'workflow': '0.1'},
        'compatibility': 'new side-by-side installations; no automatic migration',
        'evidence_status': 'candidate; operational effectiveness not established',
        'files': hashes,
    }
    payload['manifest.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in sorted(payload.items()):
            info = zipfile.ZipInfo(PACKAGE + '/' + name, (2026, 9, 20, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data)
    return buf.getvalue()


def verify(path):
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [i.filename for i in infos]
        expected = {PACKAGE + '/' + n for n in (*FILES, 'manifest.json')}
        if len(names) != len(set(names)) or set(names) != expected:
            raise ValueError('unexpected, missing, or duplicate archive members')
        if any(i.file_size > 2_000_000 or (i.external_attr >> 16) & 0o170000 == 0o120000 for i in infos):
            raise ValueError('oversized member or symlink')
        manifest = json.loads(archive.read(PACKAGE + '/manifest.json'))
        if manifest['release_id'] != PACKAGE or manifest['version'] != VERSION or manifest['status'] != 'preview':
            raise ValueError('release identity mismatch')
        if set(manifest['files']) != set(FILES):
            raise ValueError('manifest allowlist mismatch')
        payload = {name: archive.read(PACKAGE + '/' + name) for name in FILES}
        hashes = {name: sha(data) for name, data in sorted(payload.items())}
        if hashes != manifest['files']:
            raise ValueError('content hash mismatch')
        snapshot = sha(json.dumps(hashes, sort_keys=True, separators=(',', ':')).encode())
        if snapshot != manifest['source_snapshot_sha256']:
            raise ValueError('snapshot mismatch')
        check_docs(payload)
    return manifest


def build(source, output):
    data = make_archive(source)
    output.mkdir(parents=True, exist_ok=True)
    path = output / (PACKAGE + '.zip')
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('release already exists with different content; use a new version')
    else:
        with path.open('xb') as handle:
            handle.write(data)
    verify(path)
    checksum = output / (PACKAGE + '.zip.sha256')
    content = f'{sha(data)}  {path.name}\n'
    if checksum.exists() and checksum.read_text() != content:
        raise ValueError('checksum already exists with different content')
    if not checksum.exists():
        with checksum.open('x') as handle:
            handle.write(content)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    b = sub.add_parser('build')
    b.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1] / 'distribution/starter')
    b.add_argument('--output', type=Path, required=True)
    v = sub.add_parser('verify')
    v.add_argument('archive', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'build':
            print(build(args.source, args.output))
        else:
            m = verify(args.archive)
            print(f"Verified {m['release_id']}: {len(m['files'])} files")
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(1, f'error: {error}\n')


if __name__ == '__main__':
    main()

"""Documentation indexer for committed snapshot files (Issue #77).

Reads documentation files (README.md, docs/**/*.md) from a pinned
repository snapshot and produces a deterministic chunk index using
the Markdown chunker. Respects the committed-snapshot-only constraint:
no working tree reads.

Custom documentation path patterns are supported via doc_patterns
parameter for future extensibility.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Tuple

from .documentation_chunker import MarkdownChunk, chunk_markdown


DEFAULT_DOC_PATTERNS = ["README.md", "docs/"]


@dataclass
class DocFileInfo:
    path: str
    content_hash: str
    path_depth: int
    doc_role_hint: str
    line_count: int
    included: bool = True


@dataclass
class DocumentationIndex:
    snapshot_id: int
    system_id: int
    files: List[DocFileInfo]
    chunks: List[MarkdownChunk]
    total_files: int = 0
    total_chunks: int = 0


def _is_doc_file(path: str, patterns: List[str]) -> bool:
    """Check if a file path matches documentation patterns."""
    lower = path.lower()
    if not lower.endswith(".md"):
        return False
    for pattern in patterns:
        if pattern.endswith("/"):
            if lower.startswith(pattern.lower()) or path.startswith(pattern):
                return True
        else:
            if lower == pattern.lower() or path == pattern:
                return True
            base = path.rsplit("/", 1)[-1] if "/" in path else path
            if base.lower() == pattern.lower():
                return True
    return False


def _path_depth(path: str) -> int:
    return path.replace("\\", "/").count("/")


def _content_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def build_documentation_index(
    conn: sqlite3.Connection,
    system_id: int,
    snapshot_id: int,
    doc_patterns: Optional[List[str]] = None,
) -> DocumentationIndex:
    """Build a deterministic documentation chunk index from a pinned snapshot.

    Reads only from snapshot_files (committed content). Files are selected
    by matching against doc_patterns. Each file is chunked by Markdown
    heading structure.
    """
    patterns = doc_patterns or DEFAULT_DOC_PATTERNS

    rows = conn.execute(
        """SELECT path, content, content_hash, inclusion_status
           FROM snapshot_files
           WHERE snapshot_id = ?
           ORDER BY path""",
        (snapshot_id,),
    ).fetchall()

    files: List[DocFileInfo] = []
    all_chunks: List[MarkdownChunk] = []

    for row in rows:
        path = row["path"]
        if not _is_doc_file(path, patterns):
            continue

        inclusion = row["inclusion_status"]
        content_bytes = row["content"] if row["content"] else b""
        c_hash = row["content_hash"] or _content_hash(content_bytes)

        if isinstance(content_bytes, bytes):
            try:
                text = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                text = ""
        else:
            text = content_bytes

        line_count = text.count("\n") + 1 if text else 0
        included = inclusion == "indexed" and bool(text.strip())
        depth = _path_depth(path)

        from .documentation_chunker import _doc_role_hint
        doc_role = _doc_role_hint(path)

        files.append(DocFileInfo(
            path=path,
            content_hash=c_hash,
            path_depth=depth,
            doc_role_hint=doc_role,
            line_count=line_count,
            included=included,
        ))

        if included:
            chunks = chunk_markdown(path, text)
            all_chunks.extend(chunks)

    return DocumentationIndex(
        snapshot_id=snapshot_id,
        system_id=system_id,
        files=files,
        chunks=all_chunks,
        total_files=len(files),
        total_chunks=len(all_chunks),
    )


def get_unchanged_chunk_hashes(
    existing_chunks: List[MarkdownChunk],
    new_chunks: List[MarkdownChunk],
) -> Set[str]:
    """Return content hashes that appear in both existing and new chunk lists.

    These chunks do not need reprocessing (e.g., claim scanning).
    """
    existing_hashes = {c.content_hash for c in existing_chunks}
    new_hashes = {c.content_hash for c in new_chunks}
    return existing_hashes & new_hashes

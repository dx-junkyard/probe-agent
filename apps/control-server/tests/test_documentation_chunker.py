"""Tests for documentation chunking and indexing (Issue #77)."""

import hashlib
import sqlite3
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from app.documentation_chunker import (
    MAX_CHUNK_LINES,
    MarkdownChunk,
    chunk_markdown,
    _content_hash,
    _doc_role_hint,
    _heading_level,
    _heading_text,
    _path_depth,
)
from app.documentation_indexer import (
    DocFileInfo,
    DocumentationIndex,
    build_documentation_index,
    get_unchanged_chunk_hashes,
    _is_doc_file,
)
from app.db import SCHEMA


def _make_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _insert_snapshot(conn, system_id=1, commit_sha="abc123"):
    import time
    now = time.time()
    conn.execute(
        "INSERT INTO systems (id, name, created_at, updated_at) VALUES (?, 'test', ?, ?)",
        (system_id, now, now),
    )
    cur = conn.execute(
        """INSERT INTO repository_snapshots
            (system_id, repo_path, commit_sha, status, file_count, created_at)
        VALUES (?, '/tmp/repo', ?, 'completed', 0, ?)""",
        (system_id, commit_sha, now),
    )
    return cur.lastrowid


def _insert_doc_file(conn, snapshot_id, path, content):
    content_bytes = content.encode("utf-8") if isinstance(content, str) else content
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    conn.execute(
        """INSERT INTO snapshot_files
            (snapshot_id, path, source_type, size_bytes, content_hash, content, inclusion_status)
        VALUES (?, ?, 'committed', ?, ?, ?, 'indexed')""",
        (snapshot_id, path, len(content_bytes), content_hash, content_bytes),
    )


# --- chunk_markdown tests ---

class TestChunkMarkdown:
    def test_empty_file(self):
        assert chunk_markdown("README.md", "") == []
        assert chunk_markdown("README.md", "   \n\n  ") == []

    def test_single_heading(self):
        text = "# Title\n\nSome content here."
        chunks = chunk_markdown("README.md", text)
        assert len(chunks) == 1
        assert chunks[0].heading_path == ["Title"]
        assert chunks[0].start_line == 1
        assert chunks[0].end_line == 3
        assert "Some content here." in chunks[0].content

    def test_multiple_headings(self):
        text = "# Top\n\nIntro\n\n## Section A\n\nContent A\n\n## Section B\n\nContent B"
        chunks = chunk_markdown("README.md", text)
        assert len(chunks) == 3
        assert chunks[0].heading_path == ["Top"]
        assert chunks[1].heading_path == ["Top", "Section A"]
        assert chunks[2].heading_path == ["Top", "Section B"]

    def test_nested_headings(self):
        text = "# Root\n\n## Child\n\n### Grandchild\n\nDeep content\n\n## Sibling\n\nSibling content"
        chunks = chunk_markdown("docs/guide.md", text)
        assert len(chunks) == 4
        assert chunks[0].heading_path == ["Root"]
        assert chunks[1].heading_path == ["Root", "Child"]
        assert chunks[2].heading_path == ["Root", "Child", "Grandchild"]
        assert chunks[3].heading_path == ["Root", "Sibling"]

    def test_heading_path_resets_on_higher_level(self):
        text = "# A\n\n### Deep\n\ncontent\n\n# B\n\n## Under B\n\ncontent"
        chunks = chunk_markdown("README.md", text)
        assert chunks[2].heading_path == ["B"]
        assert chunks[3].heading_path == ["B", "Under B"]

    def test_no_heading_preamble(self):
        text = "Some text before any heading.\n\nMore text.\n\n# First Heading\n\nContent"
        chunks = chunk_markdown("README.md", text)
        assert len(chunks) == 2
        assert chunks[0].heading_path == []
        assert chunks[0].start_line == 1
        assert chunks[1].heading_path == ["First Heading"]

    def test_deterministic_output(self):
        text = "# Title\n\n## A\n\nContent A\n\n## B\n\nContent B"
        chunks1 = chunk_markdown("README.md", text)
        chunks2 = chunk_markdown("README.md", text)
        assert len(chunks1) == len(chunks2)
        for c1, c2 in zip(chunks1, chunks2):
            assert c1.chunk_id == c2.chunk_id
            assert c1.content_hash == c2.content_hash
            assert c1.heading_path == c2.heading_path
            assert c1.start_line == c2.start_line
            assert c1.end_line == c2.end_line

    def test_content_hash_changes_with_content(self):
        text1 = "# Title\n\nVersion 1"
        text2 = "# Title\n\nVersion 2"
        chunks1 = chunk_markdown("README.md", text1)
        chunks2 = chunk_markdown("README.md", text2)
        assert chunks1[0].content_hash != chunks2[0].content_hash

    def test_long_section_split(self):
        lines = ["# Long Section"] + [f"Line {i}" for i in range(MAX_CHUNK_LINES + 50)]
        text = "\n".join(lines)
        chunks = chunk_markdown("README.md", text)
        assert len(chunks) >= 2
        for chunk in chunks:
            chunk_lines = chunk.content.split("\n")
            assert len(chunk_lines) <= MAX_CHUNK_LINES + 5

    def test_chunk_order_stable(self):
        text = "# A\n\na\n\n## B\n\nb\n\n### C\n\nc\n\n## D\n\nd"
        chunks = chunk_markdown("README.md", text)
        starts = [c.start_line for c in chunks]
        assert starts == sorted(starts)

    def test_doc_role_hints(self):
        assert _doc_role_hint("README.md") == "overview"
        assert _doc_role_hint("docs/api/reference.md") == "api_reference"
        assert _doc_role_hint("CHANGELOG.md") == "changelog"
        assert _doc_role_hint("CONTRIBUTING.md") == "contributing"
        assert _doc_role_hint("docs/architecture.md") == "architecture"
        assert _doc_role_hint("docs/setup.md") == "setup"
        assert _doc_role_hint("docs/random.md") == "documentation"

    def test_path_depth(self):
        assert _path_depth("README.md") == 0
        assert _path_depth("docs/guide.md") == 1
        assert _path_depth("docs/api/reference.md") == 2

    def test_abstraction_hint(self):
        text = "# High\n\nContent\n\n## Medium\n\nContent\n\n### Detail\n\nContent"
        chunks = chunk_markdown("README.md", text)
        assert chunks[0].abstraction_hint == "high"
        assert chunks[1].abstraction_hint == "medium"
        assert chunks[2].abstraction_hint == "detail"


# --- Documentation indexer tests ---

class TestDocumentationIndexer:
    def test_builds_from_snapshot(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "README.md", "# Project\n\nOverview text")
        _insert_doc_file(conn, snap_id, "docs/guide.md", "# Guide\n\n## Setup\n\nSteps")

        index = build_documentation_index(conn, 1, snap_id)
        assert index.total_files == 2
        assert index.total_chunks >= 2
        assert any(f.path == "README.md" for f in index.files)
        assert any(f.path == "docs/guide.md" for f in index.files)

    def test_excludes_non_doc_files(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "README.md", "# Readme\n\nContent")
        _insert_doc_file(conn, snap_id, "src/main.py", "# not a doc")
        _insert_doc_file(conn, snap_id, "app/models.py", "# also not")

        index = build_documentation_index(conn, 1, snap_id)
        assert index.total_files == 1
        assert index.files[0].path == "README.md"

    def test_nested_docs_directory(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "docs/guide.md", "# Guide\n\nText")
        _insert_doc_file(conn, snap_id, "docs/api/reference.md", "# API\n\n## Endpoints\n\nGET /foo")
        _insert_doc_file(conn, snap_id, "docs/deep/nested/file.md", "# Deep\n\nContent")

        index = build_documentation_index(conn, 1, snap_id)
        assert index.total_files == 3
        paths = {f.path for f in index.files}
        assert "docs/guide.md" in paths
        assert "docs/api/reference.md" in paths
        assert "docs/deep/nested/file.md" in paths

    def test_custom_patterns(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "wiki/page.md", "# Wiki\n\nPage content")
        _insert_doc_file(conn, snap_id, "README.md", "# Readme")

        index = build_documentation_index(conn, 1, snap_id, doc_patterns=["wiki/"])
        assert index.total_files == 1
        assert index.files[0].path == "wiki/page.md"

    def test_deterministic_chunks(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "README.md", "# A\n\n## B\n\nContent B\n\n## C\n\nContent C")

        idx1 = build_documentation_index(conn, 1, snap_id)
        idx2 = build_documentation_index(conn, 1, snap_id)
        assert len(idx1.chunks) == len(idx2.chunks)
        for c1, c2 in zip(idx1.chunks, idx2.chunks):
            assert c1.chunk_id == c2.chunk_id
            assert c1.content_hash == c2.content_hash

    def test_unchanged_chunk_detection(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "README.md", "# Title\n\nOriginal content")
        idx = build_documentation_index(conn, 1, snap_id)

        unchanged = get_unchanged_chunk_hashes(idx.chunks, idx.chunks)
        assert len(unchanged) == len(idx.chunks)

    def test_changed_chunk_detection(self):
        text1 = "# Title\n\nVersion 1"
        text2 = "# Title\n\nVersion 2"
        chunks1 = chunk_markdown("README.md", text1)
        chunks2 = chunk_markdown("README.md", text2)
        unchanged = get_unchanged_chunk_hashes(chunks1, chunks2)
        assert len(unchanged) == 0

    def test_is_doc_file(self):
        patterns = ["README.md", "docs/"]
        assert _is_doc_file("README.md", patterns)
        assert _is_doc_file("docs/guide.md", patterns)
        assert _is_doc_file("docs/api/ref.md", patterns)
        assert not _is_doc_file("src/main.py", patterns)
        assert not _is_doc_file("src/readme.txt", patterns)
        assert not _is_doc_file("random.md", patterns)

    def test_empty_content_excluded(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "README.md", "")

        index = build_documentation_index(conn, 1, snap_id)
        assert index.total_files == 1
        assert not index.files[0].included
        assert index.total_chunks == 0

    def test_snapshot_only_no_working_tree(self):
        """Verify index reads from snapshot_files table only."""
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "README.md", "# Test\n\nContent")

        index = build_documentation_index(conn, 1, snap_id)
        assert index.snapshot_id == snap_id
        assert index.total_chunks > 0


class TestFencedCodeBlockHeadings:
    """Headings inside fenced code blocks must not create new chunks."""

    def test_heading_inside_fence_ignored(self):
        text = "# Real Heading\n\nSome text.\n\n```python\n# Not a heading\nprint('hello')\n```\n\nMore text."
        chunks = chunk_markdown("test.md", text)
        assert len(chunks) == 1
        assert chunks[0].heading_path == ["Real Heading"]

    def test_multiple_fences(self):
        text = (
            "# Top\n\nIntro\n\n"
            "```\n# fake1\n```\n\n"
            "## Real Sub\n\nBody\n\n"
            "```bash\n# fake2\n# fake3\n```\n"
        )
        chunks = chunk_markdown("test.md", text)
        headings = [c.heading_path for c in chunks]
        assert ["Top"] in headings
        assert ["Top", "Real Sub"] in headings
        assert not any("fake1" in h for c in chunks for h in c.heading_path)

    def test_unclosed_fence_treats_rest_as_code(self):
        text = "# Title\n\nText\n\n```\n# Inside\n## Also inside\n"
        chunks = chunk_markdown("test.md", text)
        assert len(chunks) == 1
        assert chunks[0].heading_path == ["Title"]


class TestGlobPatternMatching:
    """_is_doc_file must support glob patterns like docs/**/*.md."""

    def test_doublestar_glob(self):
        assert _is_doc_file("docs/guide.md", ["docs/**/*.md"])
        assert _is_doc_file("docs/api/reference.md", ["docs/**/*.md"])

    def test_single_star_glob(self):
        assert _is_doc_file("docs/guide.md", ["docs/*.md"])
        assert not _is_doc_file("docs/api/reference.md", ["docs/*.md"])

    def test_question_mark_glob(self):
        assert _is_doc_file("docs/v1.md", ["docs/v?.md"])
        assert not _is_doc_file("docs/v10.md", ["docs/v?.md"])

    def test_glob_mixed_with_plain(self):
        patterns = ["README.md", "docs/**/*.md"]
        assert _is_doc_file("README.md", patterns)
        assert _is_doc_file("docs/deep/nested/file.md", patterns)
        assert not _is_doc_file("src/main.md", patterns)

    def test_glob_with_indexer(self):
        conn = _make_db()
        snap_id = _insert_snapshot(conn)
        _insert_doc_file(conn, snap_id, "docs/api/ref.md", "# API\n\nContent")
        _insert_doc_file(conn, snap_id, "src/notes.md", "# Notes\n\nBody")

        index = build_documentation_index(conn, 1, snap_id, doc_patterns=["docs/**/*.md"])
        paths = [f.path for f in index.files]
        assert "docs/api/ref.md" in paths
        assert "src/notes.md" not in paths

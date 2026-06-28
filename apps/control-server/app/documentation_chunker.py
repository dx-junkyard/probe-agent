"""Deterministic Markdown chunker for documentation understanding (Issue #77).

Splits Markdown files by heading structure, preserving heading paths,
line ranges, and content hashes. Only splits by size when a heading section
exceeds MAX_CHUNK_LINES. Output is deterministic and stable for identical
input.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

MAX_CHUNK_LINES = 120
MIN_CHUNK_LINES = 5


@dataclass
class MarkdownChunk:
    chunk_id: str
    path: str
    heading_path: List[str]
    start_line: int
    end_line: int
    content_hash: str
    content: str
    path_depth: int = 0
    doc_role_hint: str = ""
    abstraction_hint: str = ""


def _content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _chunk_id(path: str, start_line: int, heading_path: List[str]) -> str:
    key = f"{path}:{start_line}:{'/'.join(heading_path)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def _heading_level(line: str) -> Optional[int]:
    m = re.match(r"^(#{1,6})\s+", line)
    return len(m.group(1)) if m else None


def _heading_text(line: str) -> str:
    m = re.match(r"^#{1,6}\s+(.*)", line)
    return m.group(1).strip() if m else ""


def _doc_role_hint(path: str) -> str:
    lower = path.lower()
    if lower.endswith("readme.md") or lower == "readme.md":
        return "overview"
    if "/api" in lower or "openapi" in lower or "swagger" in lower:
        return "api_reference"
    if "changelog" in lower or "changes" in lower:
        return "changelog"
    if "contributing" in lower:
        return "contributing"
    if "architecture" in lower or "design" in lower:
        return "architecture"
    if "install" in lower or "setup" in lower or "getting-started" in lower:
        return "setup"
    return "documentation"


def _abstraction_hint(heading_path: List[str], level: int) -> str:
    if level <= 1:
        return "high"
    if level == 2:
        return "medium"
    return "detail"


def _path_depth(path: str) -> int:
    parts = path.replace("\\", "/").split("/")
    return len(parts) - 1


def _split_large_section(
    lines: List[str],
    path: str,
    heading_path: List[str],
    start_line: int,
    doc_role: str,
    p_depth: int,
    abs_hint: str,
) -> List[MarkdownChunk]:
    """Split a section that exceeds MAX_CHUNK_LINES into sub-chunks."""
    chunks: List[MarkdownChunk] = []
    total = len(lines)
    offset = 0
    part = 0

    while offset < total:
        end = min(offset + MAX_CHUNK_LINES, total)
        if end < total and (end - offset) > MIN_CHUNK_LINES:
            for i in range(end - 1, offset + MIN_CHUNK_LINES - 1, -1):
                if lines[i].strip() == "":
                    end = i + 1
                    break

        chunk_lines = lines[offset:end]
        content = "\n".join(chunk_lines)
        sub_heading = heading_path + [f"part_{part}"] if part > 0 else heading_path
        actual_start = start_line + offset

        chunks.append(MarkdownChunk(
            chunk_id=_chunk_id(path, actual_start, sub_heading),
            path=path,
            heading_path=sub_heading,
            start_line=actual_start,
            end_line=actual_start + len(chunk_lines) - 1,
            content_hash=_content_hash(content),
            content=content,
            path_depth=p_depth,
            doc_role_hint=doc_role,
            abstraction_hint=abs_hint,
        ))
        offset = end
        part += 1

    return chunks


def chunk_markdown(path: str, text: str) -> List[MarkdownChunk]:
    """Split a Markdown file into deterministic chunks by heading structure.

    Returns chunks in document order. Chunk IDs are stable for the same
    path + start_line + heading_path combination.
    """
    if not text.strip():
        return []

    raw_lines = text.split("\n")
    doc_role = _doc_role_hint(path)
    p_depth = _path_depth(path)

    sections: List[Tuple[List[str], int, List[str], int]] = []
    heading_stack: List[Tuple[int, str]] = []

    current_lines: List[str] = []
    current_start = 1
    current_heading_path: List[str] = []
    current_level = 0

    for i, line in enumerate(raw_lines):
        line_num = i + 1
        level = _heading_level(line)

        if level is not None and current_lines:
            sections.append((
                current_lines,
                current_start,
                list(current_heading_path),
                current_level,
            ))
            current_lines = []

        if level is not None:
            heading = _heading_text(line)
            while heading_stack and heading_stack[-1][0] >= level:
                heading_stack.pop()
            heading_stack.append((level, heading))
            current_heading_path = [h for _, h in heading_stack]
            current_start = line_num
            current_level = level

        current_lines.append(line)

    if current_lines:
        sections.append((
            current_lines,
            current_start,
            list(current_heading_path),
            current_level,
        ))

    chunks: List[MarkdownChunk] = []
    for section_lines, start, heading_path, level in sections:
        abs_hint = _abstraction_hint(heading_path, level)

        if len(section_lines) > MAX_CHUNK_LINES:
            chunks.extend(_split_large_section(
                section_lines, path, heading_path, start,
                doc_role, p_depth, abs_hint,
            ))
        else:
            content = "\n".join(section_lines)
            chunks.append(MarkdownChunk(
                chunk_id=_chunk_id(path, start, heading_path),
                path=path,
                heading_path=heading_path,
                start_line=start,
                end_line=start + len(section_lines) - 1,
                content_hash=_content_hash(content),
                content=content,
                path_depth=p_depth,
                doc_role_hint=doc_role,
                abstraction_hint=abs_hint,
            ))

    return chunks

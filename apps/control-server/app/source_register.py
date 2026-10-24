"""External Source Register (vision-to-feature-research.md §3.3, VF-G3 / VF-G8).

probe-agent never fetches external material itself (VF-G1). A project commits
each external source (a plan, a strategy, a survey report, ...) as a Markdown
file whose front matter carries a ``probe_source`` mapping. This module reads
those files from a pinned snapshot -- committed content only, no git, no
network, no LLM -- and returns:

- one register entry per ``source_id`` (a source may be split into several
  ``part`` files when it exceeds the snapshot's per-file size limit),
- the source attributes evidence needs but cannot carry on its own (layer,
  publisher, issued date, plan period, edition, status, ...),
- a finite set of structural checks (§3.3's register check). A check is a
  fact about the register, never a judgement about the source's content.

Front matter shape (all keys under ``probe_source``)::

    ---
    probe_source:
      source_id: SRC-plan-2017          # required, stable, never derived from the URL
      layer: SL-2                       # required, SL-1 .. SL-5
      title: ...                        # required
      publisher: ...                    # required
      url: https://...                  # required
      issued: 2017-02                   # YYYY | YYYY-MM | YYYY-MM-DD
      period_start: 2017-04-01          # optional, same formats
      period_end: 2027-03-31            # optional, same formats (write the exact
                                        # date for fiscal years; a bare year is
                                        # read as the end of that calendar year)
      edition: final                    # final | draft
      retrieved: 2026-10-24             # required
      content_digest: sha256:<hex>      # sha256 of the body after the front matter
      coverage: 全文                     # what part of the source is in the body
      status: current                   # current | superseded | draft | unavailable
      unavailable_reason: ...           # required when status = unavailable
      superseded_by: SRC-plan-2027      # required when status = superseded
      license_note: ...
      part: 1                           # optional, for sources split into files
    ---

An ``unavailable`` source still gets a file (empty body allowed): a source the
project tried and failed to obtain must be distinguishable from one nobody
looked for (VF-D5).

probe-agent:
  role: Deterministic external source register and structural check
  capability: documentation-understanding
  element_type: element
  consumers: [system-understanding, stakeholder-value-network, dashboard]
  operation_kind: analysis
  state_effects: [database-read]
  probe_value: Verify that identical snapshot content yields an identical register and that unreadable or malformed sources are reported, never dropped.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

import yaml

FRONT_MATTER_KEY = "probe_source"

SOURCE_LAYERS: Tuple[str, ...] = ("SL-1", "SL-2", "SL-3", "SL-4", "SL-5")
SOURCE_STATUSES: Tuple[str, ...] = ("current", "superseded", "draft", "unavailable")
SOURCE_EDITIONS: Tuple[str, ...] = ("final", "draft")
PERIOD_STATES: Tuple[str, ...] = ("not_started", "active", "ended", "unbounded", "unknown")

REQUIRED_FIELDS: Tuple[str, ...] = ("source_id", "layer", "title", "publisher", "url", "retrieved", "status")

#: Attributes that describe the SOURCE, so every part of one source must agree
#: on them. `part`, `coverage` and `content_digest` describe one FILE.
SOURCE_LEVEL_FIELDS: Tuple[str, ...] = (
    "layer", "title", "publisher", "url", "issued", "period_start", "period_end",
    "edition", "retrieved", "status", "unavailable_reason", "superseded_by", "license_note",
)

#: Finite check codes. `error` means the register entry cannot be trusted as
#: written; `notice` is a fact a reader should see before relying on it.
CHECK_SEVERITY: Dict[str, str] = {
    "front_matter_invalid": "error",
    "field_missing": "error",
    "field_invalid": "error",
    "duplicate_source_part": "error",
    "part_attributes_mismatch": "error",
    "unavailable_without_reason": "error",
    "superseded_without_successor": "error",
    "successor_unresolved": "error",
    "digest_mismatch": "error",
    "body_missing": "error",
    "digest_not_declared": "notice",
    "coverage_unstated": "notice",
    "edition_draft": "notice",
    "period_ended": "notice",
}

_DATE_RE = re.compile(r"^(\d{4})(?:-(\d{2}))?(?:-(\d{2}))?$")
_FRONT_MATTER_RE = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


@dataclass
class SourceCheck:
    code: str
    path: str
    field: str = ""
    message: str = ""

    @property
    def severity(self) -> str:
        return CHECK_SEVERITY[self.code]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code, "severity": self.severity, "path": self.path,
            "field": self.field, "message": self.message,
        }


@dataclass
class SourcePart:
    path: str
    part: Optional[int]
    coverage: str
    declared_digest: str
    actual_digest: str
    body_line_offset: int
    body_line_count: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path, "part": self.part, "coverage": self.coverage,
            "declared_digest": self.declared_digest, "actual_digest": self.actual_digest,
            "body_line_offset": self.body_line_offset, "body_line_count": self.body_line_count,
        }


@dataclass
class SourceEntry:
    source_id: str
    attributes: Dict[str, str]
    parts: List[SourcePart] = field(default_factory=list)
    checks: List[SourceCheck] = field(default_factory=list)
    period_state: str = "unknown"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "attributes": dict(self.attributes),
            "period_state": self.period_state,
            "parts": [p.to_dict() for p in self.parts],
            "checks": [c.to_dict() for c in self.checks],
            "error_count": sum(1 for c in self.checks if c.severity == "error"),
            "notice_count": sum(1 for c in self.checks if c.severity == "notice"),
        }


@dataclass
class SourceRegister:
    snapshot_id: Optional[int]
    state: str  # available | none | unavailable
    entries: Dict[str, SourceEntry] = field(default_factory=dict)
    #: Files that declare `probe_source` but could not be turned into an entry
    #: (unparseable front matter, missing source_id). Reported, never dropped.
    orphan_checks: List[SourceCheck] = field(default_factory=list)
    path_to_source: Dict[str, str] = field(default_factory=dict)
    detail: str = ""

    def source_for_path(self, path: str) -> Optional[SourceEntry]:
        sid = self.path_to_source.get(path)
        return self.entries.get(sid) if sid else None

    def to_dict(self) -> Dict[str, Any]:
        entries = [self.entries[k].to_dict() for k in sorted(self.entries)]
        orphans = [c.to_dict() for c in self.orphan_checks]
        all_checks = [c for e in entries for c in e["checks"]] + orphans
        return {
            "snapshot_id": self.snapshot_id,
            "state": self.state,
            "detail": self.detail,
            "source_count": len(entries),
            "error_count": sum(1 for c in all_checks if c["severity"] == "error"),
            "notice_count": sum(1 for c in all_checks if c["severity"] == "notice"),
            "sources": entries,
            "unregistered_checks": orphans,
        }


def split_front_matter(text: str) -> Tuple[Optional[str], str, int]:
    """Return (front matter text or None, body, number of lines before the body)."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return None, text, 0
    body = text[m.end():]
    return m.group(1), body, text[: m.end()].count("\n")


def body_digest(body: str) -> str:
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _normalize_digest(value: str) -> str:
    v = value.strip().lower()
    return v if v.startswith("sha256:") else ("sha256:" + v if v else "")


def _parse_date(value: str, *, end: bool) -> Optional[_dt.date]:
    m = _DATE_RE.match(value)
    if not m:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else None
    day = int(m.group(3)) if m.group(3) else None
    try:
        if month is None:
            return _dt.date(year, 12, 31) if end else _dt.date(year, 1, 1)
        if day is None:
            if not end:
                return _dt.date(year, month, 1)
            nxt = _dt.date(year + (month // 12), month % 12 + 1, 1)
            return nxt - _dt.timedelta(days=1)
        return _dt.date(year, month, day)
    except ValueError:
        return None


def period_state(attributes: Dict[str, str], today: _dt.date) -> str:
    start_raw = attributes.get("period_start", "")
    end_raw = attributes.get("period_end", "")
    if not start_raw and not end_raw:
        return "unbounded"
    start = _parse_date(start_raw, end=False) if start_raw else None
    end = _parse_date(end_raw, end=True) if end_raw else None
    if (start_raw and start is None) or (end_raw and end is None):
        return "unknown"
    if start is not None and today < start:
        return "not_started"
    if end is not None and today > end:
        return "ended"
    return "active"


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (_dt.date, _dt.datetime)):
        return value.isoformat()[:10]
    return str(value).strip()


def _parse_file(path: str, text: str) -> Tuple[Optional[Dict[str, Any]], str, int, List[SourceCheck]]:
    """Return (probe_source mapping or None, body, body line offset, checks).

    A file without front matter or without the `probe_source` key is not a
    source and returns (None, ...) with no checks.
    """
    fm, body, offset = split_front_matter(text)
    if fm is None or FRONT_MATTER_KEY not in fm:
        return None, body, offset, []
    try:
        data = yaml.safe_load(fm)
    except yaml.YAMLError as exc:
        return None, body, offset, [SourceCheck("front_matter_invalid", path, "", f"YAML could not be parsed: {exc}")]
    if not isinstance(data, dict) or not isinstance(data.get(FRONT_MATTER_KEY), dict):
        return None, body, offset, [
            SourceCheck("front_matter_invalid", path, FRONT_MATTER_KEY, f"{FRONT_MATTER_KEY} must be a mapping"),
        ]
    return data[FRONT_MATTER_KEY], body, offset, []


def build_register(
    files: Iterable[Tuple[str, str]],
    *,
    snapshot_id: Optional[int] = None,
    today: Optional[_dt.date] = None,
) -> SourceRegister:
    """Build the register from (path, text) pairs. Deterministic for the same
    input and `today`."""
    today = today or _dt.date.today()
    register = SourceRegister(snapshot_id=snapshot_id, state="none")
    seen_parts: Dict[Tuple[str, Optional[int]], str] = {}

    for path, text in sorted(files, key=lambda f: f[0]):
        raw, body, offset, parse_checks = _parse_file(path, text)
        if parse_checks:
            register.orphan_checks.extend(parse_checks)
            continue
        if raw is None:
            continue
        attrs = {k: _as_text(v) for k, v in raw.items()}
        source_id = attrs.get("source_id", "")
        if not source_id:
            register.orphan_checks.append(SourceCheck("field_missing", path, "source_id", "source_id is required"))
            continue

        checks: List[SourceCheck] = []
        for f in REQUIRED_FIELDS:
            if not attrs.get(f):
                checks.append(SourceCheck("field_missing", path, f, f"{f} is required"))
        if attrs.get("layer") and attrs["layer"] not in SOURCE_LAYERS:
            checks.append(SourceCheck("field_invalid", path, "layer", f"layer must be one of {', '.join(SOURCE_LAYERS)}"))
        if attrs.get("status") and attrs["status"] not in SOURCE_STATUSES:
            checks.append(SourceCheck("field_invalid", path, "status", f"status must be one of {', '.join(SOURCE_STATUSES)}"))
        if attrs.get("edition") and attrs["edition"] not in SOURCE_EDITIONS:
            checks.append(SourceCheck("field_invalid", path, "edition", f"edition must be one of {', '.join(SOURCE_EDITIONS)}"))
        for f in ("issued", "retrieved", "period_start", "period_end"):
            if attrs.get(f) and _parse_date(attrs[f], end=False) is None:
                checks.append(SourceCheck("field_invalid", path, f, f"{f} must be YYYY, YYYY-MM or YYYY-MM-DD"))
        part: Optional[int] = None
        if attrs.get("part"):
            try:
                part = int(attrs["part"])
                if part < 1:
                    raise ValueError
            except ValueError:
                checks.append(SourceCheck("field_invalid", path, "part", "part must be a positive integer"))
                part = None

        status = attrs.get("status", "")
        if status == "unavailable" and not attrs.get("unavailable_reason"):
            checks.append(SourceCheck("unavailable_without_reason", path, "unavailable_reason",
                                      "an unavailable source must say why it could not be obtained"))
        if status == "superseded" and not attrs.get("superseded_by"):
            checks.append(SourceCheck("superseded_without_successor", path, "superseded_by",
                                      "a superseded source must name its successor source_id"))
        if attrs.get("edition") == "draft" or status == "draft":
            checks.append(SourceCheck("edition_draft", path, "edition", "this source is a draft, not a final edition"))

        actual = body_digest(body)
        declared = _normalize_digest(attrs.get("content_digest", ""))
        has_body = bool(body.strip())
        if status != "unavailable" and not has_body:
            checks.append(SourceCheck("body_missing", path, "", "the source has no body text"))
        if has_body:
            if not declared:
                checks.append(SourceCheck("digest_not_declared", path, "content_digest", f"actual digest is {actual}"))
            elif declared != actual:
                checks.append(SourceCheck("digest_mismatch", path, "content_digest",
                                          f"declared {declared}, actual {actual}"))
            if not attrs.get("coverage"):
                checks.append(SourceCheck("coverage_unstated", path, "coverage",
                                          "say which part of the source this file contains"))

        key = (source_id, part)
        if key in seen_parts:
            checks.append(SourceCheck("duplicate_source_part", path, "source_id",
                                      f"{source_id} part {part} is also declared in {seen_parts[key]}"))
        else:
            seen_parts[key] = path

        source_parts = SourcePart(
            path=path, part=part, coverage=attrs.get("coverage", ""), declared_digest=declared,
            actual_digest=actual, body_line_offset=offset, body_line_count=body.count("\n") + (1 if body else 0),
        )
        level_attrs = {f: attrs.get(f, "") for f in SOURCE_LEVEL_FIELDS}

        entry = register.entries.get(source_id)
        if entry is None:
            entry = SourceEntry(source_id=source_id, attributes=level_attrs)
            register.entries[source_id] = entry
        else:
            diff = [f for f in SOURCE_LEVEL_FIELDS if entry.attributes.get(f, "") != level_attrs[f]]
            if diff:
                checks.append(SourceCheck("part_attributes_mismatch", path, ",".join(diff),
                                          "every part of one source must carry the same source attributes"))
        entry.parts.append(source_parts)
        entry.checks.extend(checks)
        register.path_to_source[path] = source_id

    for entry in register.entries.values():
        entry.parts.sort(key=lambda p: (p.part or 0, p.path))
        entry.period_state = period_state(entry.attributes, today)
        if entry.period_state == "ended" and entry.attributes.get("status") == "current":
            first = entry.parts[0].path if entry.parts else ""
            entry.checks.append(SourceCheck("period_ended", first, "period_end",
                                            "the plan period has ended; check whether a successor exists"))
        successor = entry.attributes.get("superseded_by", "")
        if successor and successor not in register.entries:
            first = entry.parts[0].path if entry.parts else ""
            entry.checks.append(SourceCheck("successor_unresolved", first, "superseded_by",
                                            f"{successor} is not in the register"))

    if register.entries or register.orphan_checks:
        register.state = "available"
    return register


def _decode(content: Any) -> Optional[str]:
    if content is None:
        return ""
    if isinstance(content, bytes):
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return str(content)


def load_register(
    conn: sqlite3.Connection,
    snapshot_id: int,
    *,
    today: Optional[_dt.date] = None,
) -> SourceRegister:
    """Read every Markdown file of the snapshot and build the register.

    A file whose content was not stored (size limit, binary) but which is a
    Markdown file is reported only if its stored prefix declares a source --
    we cannot know otherwise, so it is NOT reported as missing.
    """
    try:
        rows = conn.execute(
            "SELECT path, content FROM snapshot_files WHERE snapshot_id = ? AND lower(path) LIKE '%.md'",
            (snapshot_id,),
        ).fetchall()
    except sqlite3.Error as exc:
        return SourceRegister(snapshot_id=snapshot_id, state="unavailable", detail=str(exc))
    files: List[Tuple[str, str]] = []
    for row in rows:
        text = _decode(row["content"])
        if text is None:
            continue
        files.append((row["path"], text))
    return build_register(files, snapshot_id=snapshot_id, today=today)


def latest_snapshot_id(conn: sqlite3.Connection, system_id: int) -> Optional[int]:
    row = conn.execute(
        "SELECT id FROM repository_snapshots WHERE system_id = ? AND status = 'ready' ORDER BY id DESC LIMIT 1",
        (system_id,),
    ).fetchone()
    return int(row["id"]) if row is not None else None


def resolve_source_ref(register: SourceRegister, ref: str) -> Dict[str, Any]:
    """Resolve an evidence reference of the form ``source:<source_id>`` or
    ``source:<source_id>#<locator>`` against the register.

    Returns ``{"resolution": not_a_source | resolved | unresolved | unavailable,
    "source_id", "locator", "source"}``. ``source`` carries the attributes and
    period_state of a resolved source; it is a read-time join, never stored.
    """
    if not ref.startswith("source:"):
        return {"resolution": "not_a_source", "source_id": None, "locator": "", "source": None}
    body = ref[len("source:"):]
    source_id, _, locator = body.partition("#")
    if register.state == "unavailable":
        return {"resolution": "unavailable", "source_id": source_id, "locator": locator, "source": None}
    entry = register.entries.get(source_id)
    if entry is None:
        return {"resolution": "unresolved", "source_id": source_id, "locator": locator, "source": None}
    return {
        "resolution": "resolved", "source_id": source_id, "locator": locator,
        "source": {
            "attributes": dict(entry.attributes),
            "period_state": entry.period_state,
            "error_count": sum(1 for c in entry.checks if c.severity == "error"),
        },
    }

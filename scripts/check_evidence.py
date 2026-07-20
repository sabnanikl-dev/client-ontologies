#!/usr/bin/env python3
"""Deterministic evidence-health check for portable citation anchors.

Evidence is the repo's core defense ("evidence beats memory", AGENTS.md rule 3),
but most citations point at an absolute local path on one machine with a bare
line range. This tool makes citations *verifiable*: where a citation carries a
`content_hash` anchor (added on `evidenceRef` by schemas/evidence.schema.json), it
re-hashes the cited span and reports whether the source still matches, drifted, or
cannot be resolved in the current environment. It also reports, independently, the
existence health of every registry source that declares a `path` — including
sources no citation references.

Design goals (issue #23):

  * Stdlib only. Reuses scripts/ontology_loader.py for YAML parsing and file
    enumeration, so it inspects exactly the canonical file set the validator
    gates on (Ruby must be on PATH — see CLAUDE.md).
  * Portable vs environment-local. Only repo-relative sources (relative paths, or
    absolute paths that resolve *inside* the repo root) are verifiable *portably*
    (any checkout/CI). An external absolute path (`/Users/creator/...`) is only ever
    verified *environment-locally*: if it is available here it is hashed and
    reported with ``scope: environment_local`` (never claimed as a portable/CI
    guarantee); if it is unavailable it is ``unresolvable_in_environment`` and stays
    advisory. Either way an unreadable external source is never a false pass.
  * Two levels, never conflated. Source existence (per path-bearing registry
    source) and citation anchor health (per evidence ref) are reported and summed
    separately, so an uncited-but-declared source is still visible and citation
    counts are never inflated by source-existence rows.
  * Deterministic. Human and JSON output are stable (results sorted); no clocks,
    no randomness.

## The utf8-lf-v1 content-hash contract

``content_hash`` is a versioned value, not a bare digest::

    sha256:utf8-lf-v1:<64 lowercase hex characters>

For ``utf8-lf-v1`` the cited bytes are computed by:

  1. decode the source as UTF-8;
  2. normalize CRLF and CR line endings to LF;
  3. interpret ``lines`` as 1-based inclusive range(s) over logical lines
     (the repo's existing ``"a-b,c-d,e"`` grammar — union in written order);
  4. join the selected logical lines with ``\n``;
  5. do NOT append a synthetic trailing newline;
  6. SHA-256 the resulting UTF-8 bytes.

A line-ending-only change therefore does not create false drift. Future
normalization behavior must use a *new* version tag rather than silently changing
existing hashes; an unrecognized algorithm/version is ``unsupported_hash_version``.

## Result levels and categories (issue #23 contract)

The report has two independent levels so source health and citation health are
never conflated.

*Source level* — one row per registry source that declares a ``path`` (whether or
not any citation references it), so an uncited-but-declared source is still visible:

  * ``present``  — the path resolves to a file. Repo-relative paths are present
                   *portably* (any checkout/CI); an external absolute path that
                   happens to exist here is present *environment-locally* only
                   (``scope: environment_local``, advisory for portability).
  * ``missing``  — a repo-relative path does not exist (a genuine, portable defect).
  * ``unavailable_in_environment`` — an external absolute path (or a path escaping
                   the repo) that is not available here; advisory, never a failure.

*Citation level* — one row per ``evidence`` ref, classified into exactly one:

  * ``verified_match``  — anchor present, source resolved, hash matches. Carries a
                          ``scope``: ``portable`` (repo-relative) or
                          ``environment_local`` (an available external absolute
                          path — verified *here* only, not portably/in CI).
  * ``content_drift``   — anchor present, source resolved, hash differs (same
                          ``scope`` distinction).
  * ``source_missing``  — a repo-relative source file does not exist.
  * ``anchor_missing``  — source resolvable but the citation has no ``content_hash``.
  * ``invalid_range``   — ``lines`` missing/malformed/out of bounds for an anchored
                          citation.
  * ``unsupported_hash_version`` — ``content_hash`` is not ``sha256:utf8-lf-v1:`` +
                          64 hex (schema also rejects it; this is a backstop).
  * ``unresolvable_in_environment`` — an external absolute path (or path escaping the
                          repo) unavailable here; advisory.

Verification scope is explicit on every resolved source/citation row: only
repo-relative sources are verified *portably*. An available external absolute path
is verified *environment-locally* (``scope: environment_local``) and that
distinction is preserved in the human report, the JSON, and the docs — the tool
never claims a portable/CI guarantee it cannot make.

## Exit behavior (precise)

  * exit 0 — a report ran and either ``--strict`` was not given, or ``--strict``
             found no genuine-failure category.
  * exit 1 — ``--strict`` and at least one genuine failure. Citation failures:
             ``content_drift``, ``source_missing``, ``invalid_range``,
             ``unsupported_hash_version``. Source failures: ``missing``. Advisory
             categories (citation ``anchor_missing`` / ``unresolvable_in_environment``
             and source ``unavailable_in_environment``) never trip a non-zero exit,
             so the CI step is non-blocking for owner-only external paths.
  * exit 2 — usage error (unknown ``--client``, unreadable root, bad arguments).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Optional

# The shared loader lives next to this file; make it importable whether this
# script is run directly (its dir is already on sys.path) or imported by a test
# that inserted scripts/ onto sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ontology_loader import iter_yaml, parse_yaml  # noqa: E402

HASH_ALGO = "sha256"
HASH_VERSION = "utf8-lf-v1"
# Full committed-anchor shape (kept in lockstep with schemas/evidence.schema.json).
CONTENT_HASH_RE = re.compile(r"^sha256:utf8-lf-v1:[0-9a-f]{64}$")
# Generic shape used to *classify* an anchor as unsupported vs malformed.
GENERIC_HASH_RE = re.compile(r"^(?P<algo>[a-z0-9]+):(?P<version>[a-z0-9._-]+):(?P<hex>[0-9a-fA-F]+)$")

# --- Citation level ---------------------------------------------------------- #
# Categories whose presence makes --strict exit non-zero. Advisory categories
# (anchor_missing, unresolvable_in_environment) and passes (verified_match) do not.
CITATION_STRICT_FAILURES = {
    "content_drift",
    "source_missing",
    "invalid_range",
    "unsupported_hash_version",
}
# Stable ordering for the human summary and JSON summary maps.
ALL_CITATION_CATEGORIES = [
    "verified_match",
    "content_drift",
    "source_missing",
    "anchor_missing",
    "invalid_range",
    "unsupported_hash_version",
    "unresolvable_in_environment",
]

# --- Source level ------------------------------------------------------------ #
# Only a genuinely missing repo-relative source gates; an unavailable external
# path stays advisory (owner-only paths must not fail CI).
SOURCE_STRICT_FAILURES = {"missing"}
ALL_SOURCE_CATEGORIES = [
    "present",
    "missing",
    "unavailable_in_environment",
]


class CheckError(Exception):
    """A user/usage error (unknown client, unreadable root, bad text source)."""


# --------------------------------------------------------------------------- #
# Pure helpers: line-range parsing and utf8-lf-v1 hashing.
# --------------------------------------------------------------------------- #
def parse_line_spec(spec: str) -> list[int]:
    """Expand the repo's ``"a-b,c-d,e"`` line grammar into 1-based line numbers.

    Ranges are inclusive and preserved in written order (no sorting/dedup) so the
    hashed span is exactly what the citation names. Raises ``ValueError`` on any
    malformed token, non-positive line, or descending range (``b < a``).
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("empty or non-string line spec")
    numbers: list[int] = []
    for raw_token in spec.split(","):
        token = raw_token.strip()
        if not token:
            raise ValueError(f"empty range token in {spec!r}")
        if "-" in token:
            start_s, sep, end_s = token.partition("-")
            start_s, end_s = start_s.strip(), end_s.strip()
            if not sep or not start_s or not end_s:
                raise ValueError(f"malformed range token {token!r}")
            if not (start_s.isdigit() and end_s.isdigit()):
                raise ValueError(f"non-numeric range token {token!r}")
            start, end = int(start_s), int(end_s)
            if start < 1 or end < start:
                raise ValueError(f"invalid range {token!r} (need 1 <= start <= end)")
            numbers.extend(range(start, end + 1))
        else:
            if not token.isdigit():
                raise ValueError(f"non-numeric line token {token!r}")
            n = int(token)
            if n < 1:
                raise ValueError(f"line numbers are 1-based, got {n}")
            numbers.append(n)
    if not numbers:
        raise ValueError(f"no lines selected by {spec!r}")
    return numbers


def to_logical_lines(raw: str) -> list[str]:
    """Normalize CRLF/CR to LF and split into logical lines (utf8-lf-v1 step 1-2)."""
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n")


def select_span(logical_lines: list[str], line_numbers: list[int]) -> str:
    """Join the selected 1-based logical lines with ``\\n`` (no trailing newline).

    Raises ``IndexError`` if any requested line is out of range for the source.
    """
    selected: list[str] = []
    for n in line_numbers:
        if n < 1 or n > len(logical_lines):
            raise IndexError(f"line {n} out of range (source has {len(logical_lines)} lines)")
        selected.append(logical_lines[n - 1])
    return "\n".join(selected)


def compute_span_hash(raw_text: str, spec: str) -> str:
    """Return the full ``sha256:utf8-lf-v1:<hex>`` anchor for ``spec`` over ``raw_text``.

    This is the single canonical implementation both the checker and the anchoring
    workflow use, so authored anchors and verified anchors can never diverge.
    """
    line_numbers = parse_line_spec(spec)
    span = select_span(to_logical_lines(raw_text), line_numbers)
    digest = hashlib.sha256(span.encode("utf-8")).hexdigest()
    return f"{HASH_ALGO}:{HASH_VERSION}:{digest}"


def parse_content_hash(value: Any) -> Optional[tuple[str, str, str]]:
    """Split a ``content_hash`` into ``(algo, version, hex)``.

    Returns ``None`` if the value is not even shaped like ``algo:version:hex`` (so
    the caller reports ``unsupported_hash_version``). A well-shaped value whose
    algo/version is not ``sha256:utf8-lf-v1`` or whose hex is not 64 chars is
    returned as-is so the caller can reject it explicitly.
    """
    if not isinstance(value, str):
        return None
    m = GENERIC_HASH_RE.match(value)
    if not m:
        return None
    return m.group("algo").lower(), m.group("version").lower(), m.group("hex").lower()


def is_supported_hash(value: Any) -> bool:
    """True iff ``value`` is a fully-supported ``sha256:utf8-lf-v1:<64hex>`` anchor."""
    return isinstance(value, str) and bool(CONTENT_HASH_RE.match(value))


# --------------------------------------------------------------------------- #
# Path classification.
# --------------------------------------------------------------------------- #
def classify_path(src_path: str, repo_root: Path) -> tuple[str, Path]:
    """Classify a source ``path`` as ``"repo"`` or ``"external"`` and resolve it.

    * A relative path resolves against ``repo_root``; if it stays inside the repo
      it is ``"repo"`` (verifiable portably across machines), otherwise ``"external"``.
    * An absolute path that resolves inside ``repo_root`` is ``"repo"``; one that
      resolves outside (``/Users/creator/...`` etc.) is ``"external"`` and only
      verifiable environment-locally here.
    """
    repo_root = repo_root.resolve()
    p = Path(src_path)
    if p.is_absolute():
        candidate = p
    else:
        candidate = repo_root / p
    try:
        inside = candidate.resolve().is_relative_to(repo_root)
    except (OSError, ValueError):
        inside = False
    return ("repo" if inside else "external", candidate)


def _scope_for(kind: str) -> str:
    """Verification scope for a resolved source: portable vs environment-local."""
    return "portable" if kind == "repo" else "environment_local"


# --------------------------------------------------------------------------- #
# Source-level classification (existence health of each path-bearing source).
# --------------------------------------------------------------------------- #
def classify_source(source: dict[str, Any], repo_root: Path) -> dict[str, Any]:
    """Classify the existence of one registry source that declares a ``path``.

    Independent of any citation: a repo-relative path present anywhere in a
    checkout is portably present; an available external absolute path is present
    environment-locally only; a missing repo-relative path is a genuine defect; an
    unavailable external path stays advisory. Callers must only pass path-bearing
    sources (non-file sources have nothing to check at this level).
    """
    path = str(source.get("path"))
    kind, resolved = classify_path(path, repo_root)
    # Existence — not file-ness — is the source-level contract: a directory used as
    # a provenance pointer (e.g. a projection citing its client dir) exists and is
    # therefore ``present``. Hash-verification of a specific span is a citation-level
    # concern handled by check_citation.
    if resolved.exists():
        scope = _scope_for(kind)
        what = "directory" if resolved.is_dir() else "file"
        if kind == "repo":
            detail = f"repo-relative source {what} present (portably verifiable): {path}"
        else:
            detail = (f"external source {what} present in this environment only "
                      f"(environment-local, advisory for portability): {path}")
        return {"category": "present", "scope": scope, "detail": detail}
    if kind == "external":
        return {"category": "unavailable_in_environment", "scope": "environment_local",
                "detail": f"external source not available in this environment (advisory): {path}"}
    return {"category": "missing", "scope": "portable",
            "detail": f"repo-relative source path does not exist: {path}"}


# --------------------------------------------------------------------------- #
# Core per-citation classification.
# --------------------------------------------------------------------------- #
def check_citation(ref: dict[str, Any], source: Optional[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    """Classify one evidence ref against its resolved source registry entry.

    Returns a result dict with at least ``category`` and ``detail`` (and ``scope``
    on resolved verify/drift outcomes). Precedence (first match wins) is documented
    in the module docstring; the ordering keeps static data defects (unsupported
    hash) visible and external-unreachable sources advisory rather than falsely
    verified.
    """
    content_hash = ref.get("content_hash")
    lines = ref.get("lines")

    # A. Static anchor defect: a present-but-unsupported content_hash. Reported
    #    regardless of environment (schema also forbids it; this is a backstop).
    if content_hash is not None and not is_supported_hash(content_hash):
        return {"category": "unsupported_hash_version",
                "detail": f"content_hash is not {HASH_ALGO}:{HASH_VERSION}:<64hex>: {content_hash!r}"}

    # The source_id must resolve to a local registry entry (the validator already
    # enforces this; treat a miss defensively without inventing a new category).
    if source is None:
        return {"category": "source_missing",
                "detail": f"source_id {ref.get('source_id')!r} not found in the file's source registry"}

    path = source.get("path")
    if not path:
        # Non-file sources (github_issue, human_approval_record, user_preference,
        # public_url without a local path) cannot be hash-verified offline.
        return {"category": "unresolvable_in_environment",
                "detail": f"source {source.get('id')!r} (type {source.get('type')!r}) has no local path to hash"}

    kind, resolved = classify_path(str(path), repo_root)
    exists = resolved.is_file()
    scope = _scope_for(kind)
    env_note = "" if kind == "repo" else " (environment-local: external path verified here only, not portably/in CI)"

    # B. Path resolution.
    if kind == "external" and not exists:
        return {"category": "unresolvable_in_environment",
                "detail": f"external path not available in this environment: {path}"}
    if not exists:
        # A repo-relative path that genuinely does not exist is a real defect.
        return {"category": "source_missing",
                "detail": f"repo-relative source path does not exist: {path}"}

    # C. Nothing to verify.
    if content_hash is None:
        return {"category": "anchor_missing",
                "detail": "citation has no content_hash anchor to verify"}

    # D. Verify the anchor against the resolved source.
    try:
        raw = resolved.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        # Unreadable/undecodable source: advisory for external, genuine for repo.
        category = "unresolvable_in_environment" if kind == "external" else "source_missing"
        return {"category": category, "detail": f"could not read source ({exc})"}

    if lines is None:
        return {"category": "invalid_range",
                "detail": "content_hash requires a `lines` span to verify"}
    try:
        actual = compute_span_hash(raw, str(lines))
    except (ValueError, IndexError) as exc:
        return {"category": "invalid_range", "detail": f"cannot select lines {lines!r}: {exc}"}

    if actual == content_hash:
        return {"category": "verified_match", "scope": scope,
                "detail": f"hash matches for lines {lines}{env_note}"}
    return {"category": "content_drift", "scope": scope,
            "detail": f"hash mismatch for lines {lines}: expected {content_hash}, got {actual}{env_note}"}


# --------------------------------------------------------------------------- #
# Document walking.
# --------------------------------------------------------------------------- #
def iter_citations(doc: dict[str, Any]):
    """Yield ``(object_id, evidence_ref)`` for every evidence ref in ``doc``.

    Walks recursively, tracking the nearest enclosing object ``id`` so a citation
    is attributed to the entity/relationship/rule/claim that carries it.
    """
    def walk(node: Any, current_id: Optional[str]):
        if isinstance(node, dict):
            nid = node["id"] if isinstance(node.get("id"), str) else current_id
            for key, value in node.items():
                if key == "evidence" and isinstance(value, list):
                    for ref in value:
                        if isinstance(ref, dict) and "source_id" in ref:
                            yield (nid, ref)
                else:
                    yield from walk(value, nid)
        elif isinstance(node, list):
            for item in node:
                yield from walk(item, current_id)

    yield from walk(doc, doc.get("id") if isinstance(doc.get("id"), str) else None)


def source_registry(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the file-local source registry keyed by ``source_id``.

    Clients carry ``source_registry``; modules/projections carry
    ``evidence_sources`` (mirrors validate_ontology.py's ``sources_key``).
    """
    key = "source_registry" if doc.get("kind") == "client" else "evidence_sources"
    registry: dict[str, dict[str, Any]] = {}
    for src in doc.get(key, []) or []:
        if isinstance(src, dict) and isinstance(src.get("id"), str):
            registry[src["id"]] = src
    return registry


def collect_results(root: Path, client_id: Optional[str] = None) -> dict[str, list[dict[str, Any]]]:
    """Classify sources and citations under ``root`` (optionally one client).

    Returns ``{"sources": [...], "citations": [...]}`` — two independent,
    separately-sorted streams so source-existence health and citation-anchor
    health are never conflated or double-counted. ``root`` is both the
    file-enumeration root and the repo root used to resolve repo-relative source
    paths, so the check is portable across checkouts.

    Raises ``CheckError`` when ``client_id`` is given but no ontology file declares
    it (the documented exit-2 usage error).
    """
    root = root.resolve()
    citations: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []
    known_clients: set[str] = set()
    for path in iter_yaml(root):
        try:
            doc = parse_yaml(path)
        except Exception as exc:  # noqa: BLE001 - surface a parse error as a result row
            citations.append({
                "level": "citation", "file": _rel(path, root), "object_id": None,
                "source_id": None, "lines": None, "category": "source_missing",
                "scope": None, "detail": f"YAML parse failed: {exc}",
            })
            continue
        client_of_doc = doc.get("client_id", doc.get("id"))
        if isinstance(client_of_doc, str):
            known_clients.add(client_of_doc)
        if client_id is not None and client_of_doc != client_id:
            continue
        registry = source_registry(doc)

        # Source-level rows: every registry source that declares a `path`, whether
        # or not any citation references it (uncited sources stay visible).
        for source_id, source in sorted(registry.items()):
            if not source.get("path"):
                continue
            outcome = classify_source(source, root)
            sources.append({
                "level": "source",
                "file": _rel(path, root),
                "source_id": source_id,
                "source_type": source.get("type"),
                "source_path": source.get("path"),
                "category": outcome["category"],
                "scope": outcome.get("scope"),
                "detail": outcome["detail"],
            })

        # Citation-level rows: one per evidence ref, anchor health only.
        for object_id, ref in iter_citations(doc):
            source_id = ref.get("source_id")
            source = registry.get(source_id)
            outcome = check_citation(ref, source, root)
            citations.append({
                "level": "citation",
                "file": _rel(path, root),
                "object_id": object_id,
                "source_id": source_id,
                "source_type": (source or {}).get("type"),
                "source_path": (source or {}).get("path"),
                "lines": ref.get("lines"),
                "content_hash": ref.get("content_hash"),
                "snapshot_date": ref.get("snapshot_date"),
                "category": outcome["category"],
                "scope": outcome.get("scope"),
                "detail": outcome["detail"],
            })

    if client_id is not None and client_id not in known_clients:
        raise CheckError(
            f"unknown client {client_id!r}: no ontology file under {root} declares it"
        )

    citations.sort(key=lambda r: (
        r["file"] or "", r["object_id"] or "", r["source_id"] or "", str(r["lines"] or "")
    ))
    sources.sort(key=lambda r: (r["file"] or "", r["source_id"] or ""))
    return {"sources": sources, "citations": citations}


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def summarize(rows: list[dict[str, Any]], categories: list[str]) -> dict[str, int]:
    """Deterministic per-category counts covering every category (zeros included)."""
    counts = {category: 0 for category in categories}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
    return counts


def strict_failures(report: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    """Genuine-failure rows across both levels (advisory categories excluded)."""
    citation_fails = [r for r in report["citations"] if r["category"] in CITATION_STRICT_FAILURES]
    source_fails = [r for r in report["sources"] if r["category"] in SOURCE_STRICT_FAILURES]
    return source_fails + citation_fails


def compute_exit(report: dict[str, list[dict[str, Any]]], strict: bool) -> int:
    """Exit 1 only under ``--strict`` with a genuine-failure category present."""
    if strict and strict_failures(report):
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Output rendering.
# --------------------------------------------------------------------------- #
def render_json(report: dict[str, list[dict[str, Any]]], strict: bool) -> str:
    payload = {
        "strict": strict,
        "exit_code": compute_exit(report, strict),
        "source_summary": summarize(report["sources"], ALL_SOURCE_CATEGORIES),
        "citation_summary": summarize(report["citations"], ALL_CITATION_CATEGORIES),
        "sources": report["sources"],
        "citations": report["citations"],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _scope_tag(row: dict[str, Any]) -> str:
    return f" [{row['scope']}]" if row.get("scope") else ""


def render_human(report: dict[str, list[dict[str, Any]]], strict: bool) -> str:
    sources = report["sources"]
    citations = report["citations"]
    source_counts = summarize(sources, ALL_SOURCE_CATEGORIES)
    citation_counts = summarize(citations, ALL_CITATION_CATEGORIES)
    lines: list[str] = ["evidence health report", ""]

    # Source-level section (existence of each path-bearing registry source).
    lines.append("  SOURCES (existence of each path-bearing registry source):")
    for category in ALL_SOURCE_CATEGORIES:
        marker = "  (strict-fail)" if category in SOURCE_STRICT_FAILURES else ""
        lines.append(f"    {category:<28} {source_counts[category]}{marker}")
    lines.append(f"  sources checked: {len(sources)}")
    if not sources:
        lines.append("  (no path-bearing sources found)")
    for row in sources:
        tag = "FAIL" if (strict and row["category"] in SOURCE_STRICT_FAILURES) else "----"
        lines.append(f"  [{tag}] source/{row['category']}{_scope_tag(row)}: {row['file']} <- {row['source_id']}")
        lines.append(f"         {row['detail']}")
    lines.append("")

    # Citation-level section (anchor health of each evidence ref).
    lines.append("  CITATIONS (anchor health of each evidence ref):")
    for category in ALL_CITATION_CATEGORIES:
        marker = "  (strict-fail)" if category in CITATION_STRICT_FAILURES else ""
        lines.append(f"    {category:<28} {citation_counts[category]}{marker}")
    lines.append(f"  citations checked: {len(citations)}")
    if not citations:
        lines.append("  (no citations found)")
    for row in citations:
        tag = "FAIL" if (strict and row["category"] in CITATION_STRICT_FAILURES) else "----"
        location = f"{row['file']} :: {row['object_id']}"
        anchor = f" lines {row['lines']}" if row.get("lines") else ""
        lines.append(f"  [{tag}] citation/{row['category']}{_scope_tag(row)}: {location} <- {row['source_id']}{anchor}")
        lines.append(f"         {row['detail']}")

    exit_code = compute_exit(report, strict)
    verdict = "advisory (no --strict)" if not strict else ("PASS" if exit_code == 0 else "FAIL")
    lines.append("")
    lines.append(f"  verdict: {verdict} (exit {exit_code})")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI.
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Report evidence health at two levels: SOURCES (existence of each "
            "path-bearing registry source) and CITATIONS (re-hashed anchor health of "
            "each evidence ref). Repo-relative sources are verified portably; an "
            "available external absolute path is verified environment-locally only "
            "(scope=environment_local) and one that is unavailable here is advisory "
            "(unresolvable_in_environment / source unavailable_in_environment). "
            "--strict exits non-zero only on genuine drift/missing/invalid/unsupported "
            "anchors or a missing repo-relative source, never on advisory rows."
        )
    )
    parser.add_argument("--root", default=".", help="Repo root: file enumeration + repo-relative path resolution (default: cwd)")
    parser.add_argument("--client", help="Limit to a single client slug (e.g. femme-events); unknown slug is a usage error (exit 2)")
    parser.add_argument("--json", action="store_true", help="Emit deterministic JSON instead of the human report")
    parser.add_argument("--strict", action="store_true", help="Exit 1 on any genuine-failure category (drift/missing/invalid/unsupported)")
    return parser


def run(argv: Optional[list] = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root)
    if not root.exists():
        print(json.dumps({"error": f"root does not exist: {args.root}"}), file=sys.stderr)
        return 2
    try:
        report = collect_results(root, client_id=args.client)
    except CheckError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    if args.json:
        print(render_json(report, args.strict))
    else:
        print(render_human(report, args.strict))
    return compute_exit(report, args.strict)


if __name__ == "__main__":
    raise SystemExit(run())

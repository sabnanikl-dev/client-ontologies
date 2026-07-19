#!/usr/bin/env python3
"""Deterministic evidence-health check for portable citation anchors.

Evidence is the repo's core defense ("evidence beats memory", AGENTS.md rule 3),
but most citations point at an absolute local path on one machine with a bare
line range. This tool makes citations *verifiable*: where a citation carries a
`content_hash` anchor (added on `evidenceRef` by schemas/evidence.schema.json), it
re-hashes the cited span and reports whether the source still matches, drifted, or
cannot be resolved in the current environment.

Design goals (issue #23):

  * Stdlib only. Reuses scripts/ontology_loader.py for YAML parsing and file
    enumeration, so it inspects exactly the canonical file set the validator
    gates on (Ruby must be on PATH — see CLAUDE.md).
  * Portable. Only repo-relative sources (relative paths, or absolute paths that
    resolve *inside* the repo root) are truly verifiable across machines/CI.
    External absolute paths (`/Users/creator/...`) that are not available in the
    current environment are reported ``unresolvable_in_environment`` and stay
    advisory — never collapsed into a false ``verified_match``.
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

## Result categories (issue #23 contract)

Every citation (one ``evidence`` ref) is classified into exactly one category:

  * ``verified_match``               — anchor present, source resolved, hash matches.
  * ``content_drift``               — anchor present, source resolved, hash differs.
  * ``source_missing``              — a repo-relative source file does not exist.
  * ``anchor_missing``              — source resolvable but the citation has no
                                      ``content_hash`` to verify against.
  * ``invalid_range``               — ``lines`` is missing/malformed/out of bounds
                                      for a citation carrying a ``content_hash``.
  * ``unsupported_hash_version``    — ``content_hash`` is not ``sha256:utf8-lf-v1:``
                                      + 64 hex (a static data defect; schema also
                                      rejects it, so this is a robustness backstop).
  * ``unresolvable_in_environment`` — an external absolute path (or path escaping
                                      the repo) that is not available here; advisory.

## Exit behavior (precise)

  * exit 0 — a report ran and either ``--strict`` was not given, or ``--strict``
             found no genuine-failure category.
  * exit 1 — ``--strict`` and at least one citation is a genuine failure:
             ``content_drift``, ``source_missing``, ``invalid_range``, or
             ``unsupported_hash_version``. ``anchor_missing`` and
             ``unresolvable_in_environment`` never trip a non-zero exit — external
             unreachable sources stay advisory, so the CI step is non-blocking for
             non-resolvable paths.
  * exit 2 — usage error (unknown client, unreadable root, bad arguments).
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

# Categories whose presence makes --strict exit non-zero. Advisory categories
# (anchor_missing, unresolvable_in_environment) and passes (verified_match) do not.
STRICT_FAILURE_CATEGORIES = {
    "content_drift",
    "source_missing",
    "invalid_range",
    "unsupported_hash_version",
}
# Stable ordering for the human summary and JSON summary maps.
ALL_CATEGORIES = [
    "verified_match",
    "content_drift",
    "source_missing",
    "anchor_missing",
    "invalid_range",
    "unsupported_hash_version",
    "unresolvable_in_environment",
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
      it is ``"repo"`` (truly verifiable across machines), otherwise ``"external"``.
    * An absolute path that resolves inside ``repo_root`` is ``"repo"``; one that
      resolves outside (``/Users/creator/...`` etc.) is ``"external"`` and only
      advisory here.
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


# --------------------------------------------------------------------------- #
# Core per-citation classification.
# --------------------------------------------------------------------------- #
def check_citation(ref: dict[str, Any], source: Optional[dict[str, Any]], repo_root: Path) -> dict[str, Any]:
    """Classify one evidence ref against its resolved source registry entry.

    Returns a result dict with at least ``category`` and ``detail``. Precedence
    (first match wins) is documented in the module docstring; the ordering keeps
    static data defects (unsupported hash) visible and external-unreachable
    sources advisory rather than falsely verified.
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
        return {"category": "verified_match", "detail": f"hash matches for lines {lines}"}
    return {"category": "content_drift",
            "detail": f"hash mismatch for lines {lines}: expected {content_hash}, got {actual}"}


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


def collect_results(root: Path, client_id: Optional[str] = None) -> list[dict[str, Any]]:
    """Classify every citation under ``root`` (optionally one client), sorted.

    ``root`` is both the file-enumeration root and the repo root used to resolve
    repo-relative source paths, so the check is portable across checkouts.
    """
    root = root.resolve()
    results: list[dict[str, Any]] = []
    for path in iter_yaml(root):
        try:
            doc = parse_yaml(path)
        except Exception as exc:  # noqa: BLE001 - surface a parse error as a result row
            results.append({
                "file": _rel(path, root), "object_id": None, "source_id": None,
                "lines": None, "category": "source_missing",
                "detail": f"YAML parse failed: {exc}",
            })
            continue
        if client_id is not None and doc.get("client_id", doc.get("id")) != client_id:
            continue
        registry = source_registry(doc)
        for object_id, ref in iter_citations(doc):
            source_id = ref.get("source_id")
            source = registry.get(source_id)
            outcome = check_citation(ref, source, root)
            results.append({
                "file": _rel(path, root),
                "object_id": object_id,
                "source_id": source_id,
                "source_type": (source or {}).get("type"),
                "source_path": (source or {}).get("path"),
                "lines": ref.get("lines"),
                "content_hash": ref.get("content_hash"),
                "snapshot_date": ref.get("snapshot_date"),
                "category": outcome["category"],
                "detail": outcome["detail"],
            })
    results.sort(key=lambda r: (
        r["file"] or "", r["object_id"] or "", r["source_id"] or "", str(r["lines"] or "")
    ))
    return results


def _rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def summarize(results: list[dict[str, Any]]) -> dict[str, int]:
    """Deterministic per-category counts covering every category (zeros included)."""
    counts = {category: 0 for category in ALL_CATEGORIES}
    for result in results:
        counts[result["category"]] = counts.get(result["category"], 0) + 1
    return counts


def strict_failures(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if r["category"] in STRICT_FAILURE_CATEGORIES]


def compute_exit(results: list[dict[str, Any]], strict: bool) -> int:
    """Exit 1 only under ``--strict`` with a genuine-failure category present."""
    if strict and strict_failures(results):
        return 1
    return 0


# --------------------------------------------------------------------------- #
# Output rendering.
# --------------------------------------------------------------------------- #
def render_json(results: list[dict[str, Any]], strict: bool) -> str:
    payload = {
        "strict": strict,
        "summary": summarize(results),
        "exit_code": compute_exit(results, strict),
        "results": results,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def render_human(results: list[dict[str, Any]], strict: bool) -> str:
    counts = summarize(results)
    lines: list[str] = ["evidence health report"]
    lines.append("  summary:")
    for category in ALL_CATEGORIES:
        marker = "  (strict-fail)" if category in STRICT_FAILURE_CATEGORIES else ""
        lines.append(f"    {category:<28} {counts[category]}{marker}")
    lines.append(f"  citations checked: {len(results)}")
    lines.append("")
    if not results:
        lines.append("  no citations found")
    for result in results:
        tag = "FAIL" if (strict and result["category"] in STRICT_FAILURE_CATEGORIES) else "----"
        location = f"{result['file']} :: {result['object_id']}"
        anchor = ""
        if result.get("lines"):
            anchor = f" lines {result['lines']}"
        lines.append(f"  [{tag}] {result['category']}: {location} <- {result['source_id']}{anchor}")
        lines.append(f"         {result['detail']}")
    exit_code = compute_exit(results, strict)
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
            "Re-hash cited evidence spans and report per-citation health. Repo-relative "
            "sources are truly verified; external absolute paths unavailable here are "
            "advisory (unresolvable_in_environment). --strict exits non-zero only on "
            "genuine drift/missing/invalid/unsupported anchors, never on advisory rows."
        )
    )
    parser.add_argument("--root", default=".", help="Repo root: file enumeration + repo-relative path resolution (default: cwd)")
    parser.add_argument("--client", help="Limit to a single client slug (e.g. femme-events)")
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
        results = collect_results(root, client_id=args.client)
    except CheckError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 2
    if args.json:
        print(render_json(results, args.strict))
    else:
        print(render_human(results, args.strict))
    return compute_exit(results, args.strict)


if __name__ == "__main__":
    raise SystemExit(run())

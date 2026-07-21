#!/usr/bin/env python3
"""Transport-agnostic, read-only runtime service over the client ontologies.

This is the shared core for issue #19's runtime consumer surface. It exposes the
v1 read-only operations as plain functions that return JSON-serialisable dicts,
so a thin adapter (the CLI now, a thin MCP stdio adapter next, an HTTP adapter
later) can present the surface without re-deriving any ontology semantics:

    scripts/ontology_loader.py   -- load + resolve canonical YAML (stdlib, #21)
    scripts/check_rules.py       -- machine_check guardrail engine (stdlib, #11)
    scripts/ontology_service.py  -- transport-agnostic operations (this file)
            |
            +-- scripts/ontology_cli.py   <- v1 NOW  (stdlib CLI)
            +-- server/ontology_mcp.py    <- NEXT     (thin MCP stdio adapter)
            +-- server/ontology_api.py    <- LATER    (thin HTTP adapter)

Design contract (issue #19, read-only v1):

  * No create/modify/delete. Modeling an operation never grants authority to run
    it (AGENTS.md core rule 6). Every operation is a pure read.
  * Two interchangeable read backends behind one normalized data model:
      - ``yaml``   — canonical YAML via the shared loader (#21). Uses Ruby.
      - ``sqlite`` — a prebuilt ``build/*.sqlite`` runtime projection. Pure
        stdlib ``sqlite3``; it MUST NOT invoke Ruby.
    Both backends reconstruct the SAME per-resource documents (the SQLite export
    stores each resource's full ``raw_json``), so YAML and SQLite answers are
    equal by construction — and ``tests/run_cli.py`` proves it.
  * Fail closed: unknown client/projection, an unavailable/foreign SQLite file,
    and backend drift all raise ``ServiceError`` (the CLI maps these to a
    structured non-zero error). Projection-scoped operations never return an
    entity, rule, or module outside the selected projection.
  * Preserve semantics across backends: resource ids, ``status``,
    ``source_confidence``, and evidence pointers survive both modes unchanged.
    Planning-only values (``draft``/``inferred`` status or confidence, e.g.
    Femme's ``baseline: unknown`` local-visibility metrics) are flagged
    ``planning_only`` and never relabelled as recorded outcomes/baselines.
  * ``check_copy`` inherits issue #11's exit semantics exactly by reusing
    ``check_rules.evaluate_rule`` / ``check_rules.compute_exit`` — but selects the
    rules from the normalized dataset, so the SQLite path stays Ruby-free.

Stdlib only: no PyYAML, no jsonschema, no third-party packages. The ``yaml``
backend needs Ruby on PATH (via the shared loader); the ``sqlite`` backend does
not.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Siblings live next to this file; make them importable whether this module is
# run from an installed console entry point or imported by a test that inserted
# scripts/ onto sys.path (mirrors scripts/check_rules.py).
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ontology_loader import load_documents  # noqa: E402
import check_rules as rules_engine  # noqa: E402 (reuse #11's evaluate_rule/compute_exit)

# Statuses/confidences that mark a resource as planning-only, never a recorded
# outcome. Kept explicit so a draft metric can never be presented as achieved.
PLANNING_ONLY_VALUES = {"draft", "inferred"}
# Backends this service can read from.
KNOWN_SOURCES = {"yaml", "sqlite"}


class ServiceError(Exception):
    """A deterministic, user-facing runtime error (unknown client/projection,
    unavailable or malformed SQLite backend, bad argument). Adapters map this to
    a structured non-zero result."""


# --------------------------------------------------------------------------- #
# Normalized, backend-agnostic dataset
# --------------------------------------------------------------------------- #
@dataclass
class Dataset:
    """The loaded ontology, identical in shape regardless of read backend.

    ``clients``/``modules``/``projections`` map id -> the full resource document
    (the same dict a YAML parse or a SQLite ``raw_json`` round-trip yields).
    """

    read_mode: str
    clients: dict[str, dict[str, Any]] = field(default_factory=dict)
    modules: dict[str, dict[str, Any]] = field(default_factory=dict)
    projections: dict[str, dict[str, Any]] = field(default_factory=dict)
    root: Optional[Path] = None
    sqlite_path: Optional[Path] = None


def load_dataset(
    source: str = "yaml",
    root: Optional[Path] = None,
    sqlite_path: Optional[Path] = None,
) -> Dataset:
    """Load the normalized dataset from the chosen backend.

    ``source='yaml'`` reads canonical YAML under ``root`` via the shared loader
    (Ruby). ``source='sqlite'`` reads a prebuilt export at ``sqlite_path`` with
    stdlib ``sqlite3`` and never invokes Ruby. Raises ``ServiceError`` for an
    unknown source, a missing/unreadable SQLite file, or a SQLite file that is
    not a client-ontologies export (backend drift).
    """
    if source not in KNOWN_SOURCES:
        raise ServiceError(
            f"unknown --source {source!r}: choose one of {sorted(KNOWN_SOURCES)}"
        )
    if source == "yaml":
        return _load_from_yaml(Path(root or ".").resolve())
    return _load_from_sqlite(sqlite_path)


def _load_from_yaml(root: Path) -> Dataset:
    ds = Dataset(read_mode="yaml", root=root)
    try:
        docs = load_documents(root)
    except ValueError as exc:  # a parse error in canonical YAML
        raise ServiceError(f"cannot load canonical YAML under {root}: {exc}")
    for _path, data in docs.items():
        kind = data.get("kind")
        if kind == "client" and data.get("id"):
            ds.clients[data["id"]] = data
        elif kind == "ontology_module" and data.get("id"):
            ds.modules[data["id"]] = data
        elif kind == "projection" and data.get("id"):
            ds.projections[data["id"]] = data
    return ds


def _load_from_sqlite(sqlite_path: Optional[Path]) -> Dataset:
    if not sqlite_path:
        raise ServiceError("--source sqlite requires --sqlite-path")
    path = Path(sqlite_path)
    if not path.is_file():
        raise ServiceError(f"SQLite backend not found: {path}")
    ds = Dataset(read_mode="sqlite", sqlite_path=path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        ds.clients = _rows_by_raw_json(conn, "clients", "client_id")
        ds.modules = _rows_by_raw_json(conn, "modules", "module_id")
        ds.projections = _rows_by_raw_json(conn, "projections", "projection_id")
    except sqlite3.Error as exc:
        # Missing tables / not our schema / corrupt file: fail closed rather than
        # silently returning an empty ontology (backend drift).
        raise ServiceError(
            f"cannot read SQLite backend {path}: not a client-ontologies export "
            f"({exc}). Rebuild it with scripts/export_sqlite.py."
        )
    finally:
        conn.close()
    return ds


def _rows_by_raw_json(conn: sqlite3.Connection, table: str, id_col: str) -> dict[str, dict[str, Any]]:
    """Rebuild id -> full document from a table's ``raw_json`` column.

    The export writes each resource's exact document as ``raw_json``; parsing it
    back yields the same dict the YAML backend produces, so the two backends are
    equal by construction.
    """
    out: dict[str, dict[str, Any]] = {}
    for row_id, raw in conn.execute(f"SELECT {id_col}, raw_json FROM {table}"):
        try:
            doc = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ServiceError(f"{table}.{row_id}: corrupt raw_json ({exc})")
        if isinstance(doc, dict) and doc.get("id"):
            out[doc["id"]] = doc
    return out


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #
def _git_commit(root: Optional[Path]) -> Optional[str]:
    """Best-effort repo commit for the provenance stamp; None if unavailable."""
    cwd = str(root) if root else None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except (OSError, ValueError):
        return None
    if result.returncode != 0:
        return None
    commit = result.stdout.strip()
    return commit or None


def _meta(ds: Dataset) -> dict[str, Any]:
    """The provenance envelope every response carries (issue #19 ``_meta``)."""
    return {
        "read_mode": ds.read_mode,
        "sqlite_path": str(ds.sqlite_path) if ds.sqlite_path else None,
        "repo_commit": _git_commit(ds.root),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
    }


def _id_matches(candidate: Optional[str], patterns: list) -> bool:
    """True if ``candidate`` equals a pattern or matches its ``.*`` prefix wildcard
    (the projection ``includes`` semantics used by the validator / check_rules)."""
    if not isinstance(candidate, str):
        return False
    for pat in patterns or []:
        if not isinstance(pat, str):
            continue
        if pat.endswith(".*"):
            if candidate.startswith(pat[:-1]):
                return True
        elif candidate == pat:
            return True
    return False


def _planning_only(resource: dict[str, Any]) -> bool:
    """A resource is planning-only if its status or source_confidence is draft/
    inferred — it must never be presented as a recorded outcome/baseline."""
    return (
        resource.get("status") in PLANNING_ONLY_VALUES
        or resource.get("source_confidence") in PLANNING_ONLY_VALUES
    )


def _require_client(ds: Dataset, client_id: str) -> dict[str, Any]:
    client = ds.clients.get(client_id)
    if client is None:
        known = ", ".join(sorted(ds.clients)) or "(none)"
        raise ServiceError(f"unknown client: {client_id!r} (known clients: {known})")
    return client


def _require_projection(ds: Dataset, projection_id: str, client_id: Optional[str] = None) -> dict[str, Any]:
    projection = ds.projections.get(projection_id)
    if projection is None:
        known = ", ".join(sorted(ds.projections)) or "(none)"
        raise ServiceError(
            f"unknown projection: {projection_id!r} (known projections: {known})"
        )
    if client_id is not None and projection.get("client_id") != client_id:
        raise ServiceError(
            f"projection {projection_id!r} does not belong to client {client_id!r}"
        )
    return projection


def _client_modules(ds: Dataset, client_id: str) -> list[dict[str, Any]]:
    return [m for m in ds.modules.values() if m.get("client_id") == client_id]


def resolve_scope(
    ds: Dataset, client_id: str, projection_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return ``(entities, rules)`` the projection pulls into scope, each tagged
    with its owning ``module_id``.

    A resource is in scope iff its module is listed in ``includes.modules`` OR its
    id is named (or ``.*``-matched) in ``includes.entities`` / ``includes.rules``
    — the exact validator / check_rules / competency-runner semantics. Rows are
    restricted to ``client_id`` first, so no other client's resource can leak.
    Fails closed on an unknown client/projection.
    """
    _require_client(ds, client_id)
    projection = _require_projection(ds, projection_id, client_id)
    includes = projection.get("includes") or {}
    scoped_modules = set(includes.get("modules") or [])
    entity_patterns = includes.get("entities") or []
    rule_patterns = includes.get("rules") or []

    entities: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = []
    for module in _client_modules(ds, client_id):
        module_id = module.get("id")
        module_in_scope = module_id in scoped_modules
        for ent in module.get("entities") or []:
            if not isinstance(ent, dict):
                continue
            if module_in_scope or _id_matches(ent.get("id"), entity_patterns):
                entities.append({**ent, "module_id": module_id})
        for rule in module.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            if module_in_scope or _id_matches(rule.get("id"), rule_patterns):
                rules.append({**rule, "module_id": module_id})
    entities.sort(key=lambda e: e.get("id") or "")
    rules.sort(key=lambda r: r.get("id") or "")
    return entities, rules


def _entity_view(ent: dict[str, Any]) -> dict[str, Any]:
    """Consumer-facing entity projection: identity + status tags + fields +
    evidence pointers, plus the planning-only flag. Evidence pointers survive
    unchanged across backends (issue #19 AC)."""
    return {
        "id": ent.get("id"),
        "label": ent.get("label"),
        "entity_type": ent.get("entity_type"),
        "module_id": ent.get("module_id"),
        "status": ent.get("status"),
        "source_confidence": ent.get("source_confidence"),
        "public_facing": bool(ent.get("public_facing")),
        "planning_only": _planning_only(ent),
        "fields": ent.get("fields") or {},
        "evidence": ent.get("evidence") or [],
    }


def _rule_view(rule: dict[str, Any]) -> dict[str, Any]:
    """Consumer-facing rule projection: identity + status/severity + statement +
    evidence pointers, plus the planning-only flag."""
    return {
        "id": rule.get("id"),
        "title": rule.get("title"),
        "module_id": rule.get("module_id"),
        "status": rule.get("status"),
        "severity": rule.get("severity"),
        "rule_type": rule.get("rule_type"),
        "statement": rule.get("statement"),
        "source_confidence": rule.get("source_confidence"),
        "planning_only": _planning_only(rule),
        "machine_check": rule.get("machine_check"),
        "evidence": rule.get("evidence") or [],
    }


# --------------------------------------------------------------------------- #
# Operation 1: list_clients
# --------------------------------------------------------------------------- #
def list_clients(ds: Dataset) -> dict[str, Any]:
    """``[{id, name, status, client_type}]`` for every client, sorted by id."""
    clients = [
        {
            "id": c.get("id"),
            "name": c.get("name"),
            "status": c.get("status"),
            "client_type": c.get("client_type"),
        }
        for c in sorted(ds.clients.values(), key=lambda c: c.get("id") or "")
    ]
    return {"clients": clients, "_meta": _meta(ds)}


# --------------------------------------------------------------------------- #
# Operation 2: get_client_context
# --------------------------------------------------------------------------- #
def get_client_context(
    ds: Dataset, client_id: str, projection_id: Optional[str] = None
) -> dict[str, Any]:
    """Resolve a projection (default ``<client>.agent-context``) and return its
    in-scope entities plus its in-scope **active** rules, each status-tagged and
    with ``draft``/``inferred`` flagged ``planning_only``. Fails closed on an
    unknown client/projection; never returns out-of-scope resources."""
    _require_client(ds, client_id)
    projection_id = projection_id or f"{client_id}.agent-context"
    entities, rules = resolve_scope(ds, client_id, projection_id)
    active_rules = [r for r in rules if r.get("status") == "active"]
    return {
        "client_id": client_id,
        "projection": projection_id,
        "entities": [_entity_view(e) for e in entities],
        "active_rules": [_rule_view(r) for r in active_rules],
        "_meta": _meta(ds),
    }


# --------------------------------------------------------------------------- #
# Operation 3: list_rules
# --------------------------------------------------------------------------- #
def list_rules(
    ds: Dataset,
    client_id: str,
    severity: Optional[str] = None,
    workstream: Optional[str] = None,
) -> dict[str, Any]:
    """The client's guardrail rules, optionally narrowed to a ``severity`` and/or
    a ``workstream`` (modules whose ``workstreams`` list carries it). Fails closed
    on an unknown client."""
    _require_client(ds, client_id)
    selected: list[dict[str, Any]] = []
    for module in _client_modules(ds, client_id):
        module_workstreams = module.get("workstreams") or []
        if workstream is not None and workstream not in module_workstreams:
            continue
        for rule in module.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            if severity is not None and rule.get("severity") != severity:
                continue
            selected.append({**rule, "module_id": module.get("id")})
    selected.sort(key=lambda r: r.get("id") or "")
    return {
        "client_id": client_id,
        "severity": severity,
        "workstream": workstream,
        "rules": [_rule_view(r) for r in selected],
        "_meta": _meta(ds),
    }


# --------------------------------------------------------------------------- #
# Operation 4: check_copy  (the "apply ontology to ops" operation)
# --------------------------------------------------------------------------- #
def _select_check_rules(
    ds: Dataset,
    client_id: str,
    workstream: Optional[str],
    projection_id: Optional[str],
) -> list[dict[str, Any]]:
    """Select the rules ``check_copy`` runs, mirroring check_rules.select_rules
    scope semantics (default: all client rules; ``workstream``: modules carrying
    it; ``projection``: rules in ``includes.modules`` or named in
    ``includes.rules``) — but over the normalized dataset, so the SQLite path
    stays Ruby-free."""
    _require_client(ds, client_id)
    scoped_modules: Optional[set] = None
    rule_patterns: list = []
    if projection_id is not None:
        projection = _require_projection(ds, projection_id, client_id)
        includes = projection.get("includes") or {}
        scoped_modules = set(includes.get("modules") or [])
        rule_patterns = includes.get("rules") or []
    selected: list[dict[str, Any]] = []
    for module in _client_modules(ds, client_id):
        if workstream is not None and workstream not in (module.get("workstreams") or []):
            continue
        for rule in module.get("rules") or []:
            if not isinstance(rule, dict):
                continue
            if scoped_modules is not None:
                in_scope = module.get("id") in scoped_modules or _id_matches(
                    rule.get("id"), rule_patterns
                )
                if not in_scope:
                    continue
            selected.append(rule)
    return selected


def check_copy(
    ds: Dataset,
    client_id: str,
    text: str,
    workstream: Optional[str] = None,
    projection_id: Optional[str] = None,
    fail_on: str = "blocking",
) -> dict[str, Any]:
    """Run the client's applicable ``machine_check`` rules against ``text`` and
    return ``{violations, exit_code, _meta}``.

    Exit semantics are inherited from issue #11 exactly: this reuses
    ``check_rules.evaluate_rule`` (matching) and ``check_rules.compute_exit``
    (a non-zero exit only for an enforceable rule whose severity meets
    ``fail_on``; ``draft``/``proposed`` rules stay advisory). Because the rules
    come from the normalized dataset, the SQLite backend never touches Ruby.
    ``workstream`` and ``projection_id`` are mutually exclusive."""
    if workstream is not None and projection_id is not None:
        raise ServiceError("provide at most one of --workstream / --projection")
    if fail_on not in rules_engine.FAIL_ON_SEVERITIES:
        raise ServiceError(
            f"invalid --fail-on {fail_on!r}: choose one of "
            f"{sorted(rules_engine.FAIL_ON_SEVERITIES)}"
        )
    rules = _select_check_rules(ds, client_id, workstream, projection_id)
    try:
        violations = [v for v in (rules_engine.evaluate_rule(r, text) for r in rules) if v]
    except rules_engine.CheckError as exc:
        # e.g. an uncompilable regex_policy pattern. The validator already gates
        # this, but keep the runtime boundary structured rather than a traceback.
        raise ServiceError(str(exc))
    violations.sort(key=lambda v: (v.get("rule_id") or ""))
    exit_code = rules_engine.compute_exit(violations, fail_on)
    return {
        "client_id": client_id,
        "fail_on": fail_on,
        "violations": violations,
        "exit_code": exit_code,
        "_meta": _meta(ds),
    }


# --------------------------------------------------------------------------- #
# Operation 5: get_projection
# --------------------------------------------------------------------------- #
def get_projection(ds: Dataset, projection_id: str) -> dict[str, Any]:
    """Return the resolved projection slice plus a provenance stamp: its declared
    ``includes`` (modules/entities/rules, sorted), its ``projection_target``, and
    the resolved in-scope entity/rule rows. Fails closed on an unknown
    projection; never resolves resources outside the projection's client."""
    projection = _require_projection(ds, projection_id)
    client_id = projection.get("client_id")
    includes = projection.get("includes") or {}
    entities, rules = resolve_scope(ds, client_id, projection_id)
    return {
        "id": projection_id,
        "client_id": client_id,
        "status": projection.get("status"),
        "projection_target": projection.get("projection_target"),
        "includes": {
            "modules": sorted(includes.get("modules") or []),
            "entities": sorted(includes.get("entities") or []),
            "rules": sorted(includes.get("rules") or []),
        },
        "resolved": {
            "entities": [_entity_view(e) for e in entities],
            "rules": [_rule_view(r) for r in rules],
        },
        "_meta": _meta(ds),
    }

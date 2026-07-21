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
    # Fail closed when pointed at something that is not a client-ontologies
    # checkout: an installed consumer that runs the default ``--source yaml
    # --root .`` inside its OWN repo must get a structured error, not a silent
    # empty (and therefore vacuously "clean") result (Codex Reviewer A/B).
    if not (root / "clients").is_dir():
        raise ServiceError(
            f"no ontology found under {root}: expected a 'clients/' directory. "
            f"Point --root at a client-ontologies checkout, or use "
            f"--source sqlite --sqlite-path <pinned export>."
        )
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
    if not ds.clients:
        raise ServiceError(
            f"no client ontologies found under {root}: not a client-ontologies "
            f"checkout (a 'clients/' directory exists but declares no clients)."
        )
    return ds


# The complete set of tables ``scripts/export_sqlite.py`` writes. A backend that
# is missing any of them is not a full export (foreign / partial / drifted).
_REQUIRED_EXPORT_TABLES = (
    "manifests", "clients", "modules", "entities", "relationships",
    "rules", "projections", "sources", "evidence",
)


def _validate_export(conn: sqlite3.Connection) -> None:
    """Authenticate a SQLite backend as a genuine, internally consistent export.

    The prior loader queried only three tables and trusted each ``raw_json`` — so
    a schema-compatible foreign/drifted database (e.g. the three queried tables
    with a forged client and empty module rules) was accepted, silently disabling
    the very blocking guardrails ``check_copy`` enforces (Codex Reviewer A/B,
    Integration Auditor). Before any operation can read the backend, prove:

      1. every table the exporter writes is present (not a partial/foreign DB);
      2. the core tables are non-empty (an empty DB is not a real ontology);
      3. each row's primary-id column agrees with its ``raw_json`` ``id`` (no
         forged/mismatched row-id vs raw-id), and its ``client_id`` names a real
         client in this same export (no orphan/foreign ownership);
      4. every manifest's identity/ownership is intact (``manifest_id`` and
         embedded ``client_id`` agree with the row) AND every module/projection
         the manifest DECLARES is actually present as a row — so deleting a
         module/projection together with its normalized descendants, while
         leaving the manifest untouched, fails closed instead of quietly
         shrinking the enforced set to an empty-but-consistent slice (Reviewer B:
         correlated deletion);
      5. each module's and projection's EMBEDDED ``raw_json.client_id`` equals its
         SQL row owner — the service selects modules by the embedded value, so an
         embedded-owner rewrite (SQL owner untouched) would silently move a module
         out of its real client's enforcement (Integration Auditor: ownership
         drift);
      6. the normalized ``rules``/``entities`` tables agree with the rules/
         entities embedded in the module ``raw_json`` the service actually reads,
         by FULL CONTENT — not merely id sets — so a same-id semantic rewrite of a
         rule's ``machine_check``/``status``/``severity``/``evidence`` (which keeps
         the id set intact) fails as internally inconsistent rather than
         suppressing a blocking rule (Reviewer A: same-id tampering).

    Any failure raises ``ServiceError`` (mapped to a structured, non-zero CLI
    error). Genuine exports produced by ``export_sqlite.py`` always pass.
    """
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    missing = [t for t in _REQUIRED_EXPORT_TABLES if t not in existing]
    if missing:
        raise ServiceError(
            "SQLite backend is not a complete client-ontologies export: missing "
            f"table(s) {missing}. Rebuild it with scripts/export_sqlite.py."
        )
    for table in ("manifests", "clients"):
        count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        if not count:
            raise ServiceError(
                f"SQLite backend has an empty {table!r} table: empty or foreign "
                f"export. Rebuild it with scripts/export_sqlite.py."
            )

    def _raw(table: str, row_id: Any, raw: Any) -> dict[str, Any]:
        try:
            doc = json.loads(raw) if raw else {}
        except json.JSONDecodeError as exc:
            raise ServiceError(f"{table}.{row_id}: corrupt raw_json ({exc})")
        if not isinstance(doc, dict):
            raise ServiceError(f"{table}.{row_id}: raw_json is not a resource document")
        return doc

    client_ids: set = set()
    for cid, raw in conn.execute("SELECT client_id, raw_json FROM clients"):
        doc = _raw("clients", cid, raw)
        if doc.get("id") != cid:
            raise ServiceError(
                f"clients row {cid!r} disagrees with its raw_json id "
                f"{doc.get('id')!r}: drifted/tampered export."
            )
        client_ids.add(cid)

    # Manifests are the membership authority: identity + embedded ownership must
    # be intact, and we record every module/projection they DECLARE so a
    # correlated deletion (row + normalized descendants) can be caught below —
    # the manifest still declaring a now-absent module is the tell (Reviewer B).
    declared_modules: set = set()
    declared_projections: set = set()
    for mid, owner, raw in conn.execute("SELECT manifest_id, client_id, raw_json FROM manifests"):
        doc = _raw("manifests", mid, raw)
        if doc.get("id") != mid:
            raise ServiceError(
                f"manifests row {mid!r} disagrees with its raw_json id "
                f"{doc.get('id')!r}: drifted/tampered export."
            )
        if doc.get("client_id") != owner:
            raise ServiceError(
                f"manifests row {mid!r} SQL owner {owner!r} disagrees with its "
                f"embedded client_id {doc.get('client_id')!r}: ownership drift."
            )
        if owner not in client_ids:
            raise ServiceError(
                f"manifests row {mid!r} references unknown client {owner!r}: "
                f"foreign or internally inconsistent export."
            )
        for m in doc.get("modules") or []:
            if isinstance(m, dict) and isinstance(m.get("id"), str):
                declared_modules.add(m["id"])
        for p in doc.get("projections") or []:
            if isinstance(p, dict) and isinstance(p.get("id"), str):
                declared_projections.add(p["id"])

    # Per-resource rows: row-id/raw-id agreement, known owner, and — for the
    # top-level module/projection documents the service selects BY THEIR EMBEDDED
    # client_id — embedded-owner/SQL-owner agreement (Integration Auditor).
    module_ids: set = set()
    projection_ids: set = set()
    for table, id_col, check_embedded_owner in (
        ("modules", "module_id", True),
        ("projections", "projection_id", True),
        ("entities", "entity_id", False),
        ("rules", "rule_id", False),
    ):
        for row_id, owner, raw in conn.execute(
            f"SELECT {id_col}, client_id, raw_json FROM {table}"
        ):
            doc = _raw(table, row_id, raw)
            if doc.get("id") != row_id:
                raise ServiceError(
                    f"{table} row {row_id!r} disagrees with its raw_json id "
                    f"{doc.get('id')!r}: drifted/tampered export."
                )
            if owner not in client_ids:
                raise ServiceError(
                    f"{table} row {row_id!r} references unknown client {owner!r}: "
                    f"foreign or internally inconsistent export."
                )
            if check_embedded_owner and doc.get("client_id") != owner:
                raise ServiceError(
                    f"{table} row {row_id!r} SQL owner {owner!r} disagrees with "
                    f"its embedded client_id {doc.get('client_id')!r}: ownership "
                    f"drift (the service selects this resource by the embedded "
                    f"value, so it would silently change client)."
                )
            if table == "modules":
                module_ids.add(row_id)
            elif table == "projections":
                projection_ids.add(row_id)

    # Manifest membership: every DECLARED module/projection must exist as a row.
    # Deleting a module together with its normalized rules/entities leaves the
    # rule/entity content check (below) vacuously consistent, so this is the
    # layer that catches a correlated deletion behind an intact manifest.
    missing_modules = sorted(declared_modules - module_ids)
    if missing_modules:
        raise ServiceError(
            f"SQLite export is internally inconsistent: manifest declares "
            f"module(s) {missing_modules} that are absent from the 'modules' "
            f"table (correlated deletion behind an intact manifest). Rebuild it "
            f"with scripts/export_sqlite.py."
        )
    missing_projections = sorted(declared_projections - projection_ids)
    if missing_projections:
        raise ServiceError(
            f"SQLite export is internally inconsistent: manifest declares "
            f"projection(s) {missing_projections} that are absent from the "
            f"'projections' table (correlated deletion behind an intact "
            f"manifest). Rebuild it with scripts/export_sqlite.py."
        )

    # Projection envelope: the normalized ``includes_json`` column and the
    # ``raw_json.includes`` the service ACTUALLY consumes (via ``resolve_scope`` /
    # ``get_projection``) must agree. A genuine export writes BOTH from the same
    # projection document (``export_sqlite`` sets ``includes_json =
    # dump(data.get("includes", {}))`` and ``raw_json = dump(data)``), so they are
    # equal by construction. Emptying ONLY the embedded ``raw_json.includes`` while
    # leaving ``includes_json`` canonical (a same-id projection-content rewrite)
    # would otherwise pass authentication yet let a projection-scoped
    # ``check_copy``/``get_projection`` resolve zero modules/rules and report a
    # vacuous clean result (Reviewer A: same-id projection-content tampering).
    # Both sides are compared as PARSED documents: dict key order is already
    # normalized by the exporter's ``sort_keys`` dump and is order-independent under
    # ``==``, and list ordering is identical because both derive from the one source
    # document — so any surviving inequality is a genuine same-id divergence, not a
    # representation artifact the exporter already normalized.
    for pid, includes_json, raw in conn.execute(
        "SELECT projection_id, includes_json, raw_json FROM projections"
    ):
        doc = _raw("projections", pid, raw)
        try:
            normalized_includes = json.loads(includes_json) if includes_json else {}
        except json.JSONDecodeError as exc:
            raise ServiceError(f"projections.{pid}: corrupt includes_json ({exc})")
        embedded_includes = doc.get("includes") or {}
        if normalized_includes != embedded_includes:
            raise ServiceError(
                f"SQLite export is internally inconsistent: projection {pid!r} "
                f"normalized 'includes_json' disagrees with its embedded "
                f"raw_json.includes (same-id projection-content drift/tampering); "
                f"the service resolves projection scope from the embedded value, so "
                f"this would silently change what a scoped check enforces. Rebuild "
                f"it with scripts/export_sqlite.py."
            )

    # Cross-check the normalized rule/entity tables against the module documents
    # the service reads at runtime, by FULL CONTENT. A real export writes each
    # rule/entity into BOTH places identically; if the same id disagrees in any
    # field (machine_check/status/severity/evidence) the snapshot is tampered (a
    # suppressed blocking rule is exactly this shape) and must fail closed —
    # comparing id sets alone would miss same-id semantic drift (Reviewer A).
    def _docs(table: str, id_col: str) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for row_id, raw in conn.execute(f"SELECT {id_col}, raw_json FROM {table}"):
            out[row_id] = json.loads(raw) if raw else {}
        return out

    norm_rule_docs = _docs("rules", "rule_id")
    norm_entity_docs = _docs("entities", "entity_id")
    raw_rule_docs: dict[str, Any] = {}
    raw_entity_docs: dict[str, Any] = {}
    for (raw,) in conn.execute("SELECT raw_json FROM modules"):
        doc = json.loads(raw) if raw else {}
        if not isinstance(doc, dict):
            continue
        for rule in doc.get("rules") or []:
            if isinstance(rule, dict) and isinstance(rule.get("id"), str):
                raw_rule_docs[rule["id"]] = rule
        for ent in doc.get("entities") or []:
            if isinstance(ent, dict) and isinstance(ent.get("id"), str):
                raw_entity_docs[ent["id"]] = ent

    def _require_content_agreement(kind: str, normalized: dict[str, Any], embedded: dict[str, Any]) -> None:
        if set(normalized) != set(embedded):
            raise ServiceError(
                f"SQLite export is internally inconsistent: the normalized "
                f"{kind!r} table does not match the {kind} embedded in module "
                f"raw_json (drift/tampering). Rebuild it with "
                f"scripts/export_sqlite.py."
            )
        singular = kind[:-1]
        for _id in normalized:
            if normalized[_id] != embedded[_id]:
                raise ServiceError(
                    f"SQLite export is internally inconsistent: {singular} "
                    f"{_id!r} differs between the normalized {kind!r} table and "
                    f"its module raw_json (same-id semantic drift/tampering, e.g. "
                    f"machine_check/status/severity/evidence). Rebuild it with "
                    f"scripts/export_sqlite.py."
                )

    _require_content_agreement("rules", norm_rule_docs, raw_rule_docs)
    _require_content_agreement("entities", norm_entity_docs, raw_entity_docs)


def _load_from_sqlite(sqlite_path: Optional[Path]) -> Dataset:
    if not sqlite_path:
        raise ServiceError("--source sqlite requires --sqlite-path")
    path = Path(sqlite_path)
    if not path.is_file():
        raise ServiceError(f"SQLite backend not found: {path}")
    ds = Dataset(read_mode="sqlite", sqlite_path=path.resolve())
    conn = sqlite3.connect(str(path))
    try:
        # Authenticate the export contract BEFORE exposing any operation, so an
        # incomplete/foreign/inconsistent snapshot fails closed rather than
        # returning a clean-looking (and dangerous) enforcement result.
        _validate_export(conn)
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
    equal by construction. ``_validate_export`` has already proven row-id/raw-id
    agreement, so keying by ``doc['id']`` here cannot mask a mismatch.
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
    """Best-effort ontology-checkout commit for the provenance stamp.

    Returns ``None`` unless a concrete ontology ``root`` is supplied. Critically it
    NEVER falls back to the ambient process directory: a SQLite-backed dataset has
    no ``root``, so an installed consumer running the CLI inside its OWN repository
    must not have that consumer repo's commit mislabelled as ontology provenance
    (Codex Reviewer A/B, Integration Auditor). SQLite exports carry no embedded
    provenance today, so their ``repo_commit`` is ``None`` by construction.
    """
    if root is None:
        return None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root),
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
    """The provenance envelope every response carries (issue #19 ``_meta``).

    ``repo_commit`` is derived ONLY from the ontology ``root`` (the ``yaml``
    backend). For the ``sqlite`` backend ``root`` is ``None`` and no provenance is
    embedded in the artifact, so ``repo_commit`` is ``None`` — it is never taken
    from the ambient working directory's Git state.
    """
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


def _client_workstreams(ds: Dataset, client_id: str) -> set:
    """The recognized workstreams for a client: those declared on the client
    document (``workstreams[].id``) plus any carried by its modules. Used to
    reject an unrecognized ``--workstream`` instead of silently selecting zero
    rules."""
    names: set = set()
    client = ds.clients.get(client_id) or {}
    for ws in client.get("workstreams") or []:
        if isinstance(ws, dict) and isinstance(ws.get("id"), str):
            names.add(ws["id"])
        elif isinstance(ws, str):
            names.add(ws)
    for module in _client_modules(ds, client_id):
        for ws in module.get("workstreams") or []:
            if isinstance(ws, str):
                names.add(ws)
    return names


def _require_workstream(ds: Dataset, client_id: str, workstream: Optional[str]) -> None:
    """Fail closed on an unrecognized workstream.

    A misspelled ``--workstream`` (e.g. in a pre-publish hook) previously matched
    no module, selected zero rules, and let ``check_copy`` report success — a
    silent bypass of a blocking guardrail (Codex Reviewer A). Validate the scope
    name against the client's declared/used workstreams so a typo is a structured
    exit-2 error, not a green check."""
    if workstream is None:
        return
    known = _client_workstreams(ds, client_id)
    if workstream not in known:
        listed = ", ".join(sorted(known)) or "(none)"
        raise ServiceError(
            f"unknown workstream {workstream!r} for client {client_id!r} "
            f"(known workstreams: {listed})"
        )
    # A RECOGNIZED workstream must resolve to at least one module OWNED by this
    # client and carrying it. A workstream declared only on the client document
    # (``client.raw_json.workstreams``) with no owning module — e.g. a same-id
    # ``{"id": "bypass"}`` addition in a tampered SQLite snapshot — is recognized
    # by ``_client_workstreams`` yet selects zero rules in ``_select_check_rules`` /
    # ``list_rules``, turning a blocking ``check_copy`` into a vacuous clean pass.
    # Fail closed instead (Codex Reviewer A: same-id client workstream tampering).
    # Genuine workstreams are unaffected: every client-declared workstream in the
    # canonical data is also carried by a module, and unknown workstreams still hit
    # the branch above.
    if not any(
        workstream in (m.get("workstreams") or [])
        for m in _client_modules(ds, client_id)
    ):
        raise ServiceError(
            f"workstream {workstream!r} for client {client_id!r} resolves to no "
            f"owned module: it is declared on the client but no module carries it, "
            f"so scoping to it would select zero rules and report a vacuous clean "
            f"result. Refusing to run an unenforceable workstream scope."
        )


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
    on an unknown client or an unknown workstream."""
    _require_client(ds, client_id)
    _require_workstream(ds, client_id, workstream)
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
    # A misspelled workstream must fail closed, never select zero rules and let
    # check_copy report a clean pass (Codex Reviewer A).
    _require_workstream(ds, client_id, workstream)
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

#!/usr/bin/env python3
"""Deterministic competency-question runner (issue #31).

Structural validation answers "is this ontology well-formed?". This runner
answers the complementary question: "can a consumer still get the correct,
status-aware answer to the business question this ontology exists to support?".

It reads the competency-question registry (``tests/competency/questions.yaml`` —
TEST METADATA, never canonical truth), builds a throwaway SQLite export from the
canonical YAML using the SAME shared loader/export path the validator gates on
(``scripts/export_sqlite.py`` → ``scripts/ontology_loader.py``), then evaluates
each question's projection-scoped query against that export and compares the
normalized answer to the expected answer stored in the registry.

Design contract (issue #31):
  * Reuses #21's shared loader/export path; adds no second YAML parser. Parsing
    always goes through ``ontology_loader.parse_yaml`` and rows land in the same
    SQLite table shapes ``scripts/export_sqlite.py`` produces.
  * Builds the SQLite export in a temp dir; never touches the repo's build/.
  * Projection/client-directed LOADING (not just result filtering). Because every
    question names one client and one projection, the runner builds a SCOPED
    export per ``(client_id, projection_id)``: it reads only that client's
    manifest, ``client.yaml``, the named projection, and the module files that
    projection actually pulls into scope (``includes.modules``; if a reference
    points at a module outside ``includes.modules`` the scope widens to the full
    single-client module set rather than scanning-and-excluding other modules).
    It never parses another client's files, and never PARSES a module the
    projection excludes — reference resolution only ever opens in-scope modules.
    ``resolve_scope_paths`` computes this file set; ``run_loading_isolation_probes``
    and ``run_resolver_read_isolation_probe`` instrument the ACTUAL ``parse_yaml``
    calls (not just the returned path list) to prove nothing outside scope — no
    other client and no excluded module — is ever opened.
  * Scopes each query's RESULTS strictly through the named projection on top of
    the scoped export. An ``entities``/``rules`` query surfaces a row only if it
    belongs to the question's ``client_id`` AND its module is in the projection's
    ``includes.modules`` or its id is named (or ``.*``-matched) in
    ``includes.entities``/``includes.rules`` (mirrors the validator /
    ``check_rules`` semantics). No other client's rows and no
    unlisted-and-unreferenced module's rows can appear in an answer.
  * Deterministic: no network, model, API credential, or live-client call; rows
    and lists are sorted before comparison and output.
  * Emits a human-readable report by default and machine JSON with ``--json``;
    on a failed question it names the question and shows expected vs actual.
  * Exits non-zero when any REQUIRED question fails, the drift-isolation
    regression fails, the registry shape-validation probes fail, the
    loading-isolation probes detect cross-client / unrelated-module leakage, or
    the resolver-read isolation probe observes an excluded module being parsed.

Reuse seam for issue #19: import ``load_questions`` and
``evaluate_suite(db_path, questions)`` (or the lower-level ``evaluate_question``
/ ``run_query``) to prove YAML/SQLite answer equivalence against the SAME corpus
without copying any expected value into service code. Point ``evaluate_suite`` at
a service-produced export and compare its ``status`` fields. (``evaluate_suite``
evaluates every question against one given export; the runner itself instead
builds a scoped export per question for projection-directed loading.)

Run from the repo root:  python3 tests/run_competency.py
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import export_sqlite as e  # noqa: E402  (shared export path; pulls in ontology_loader)
from ontology_loader import parse_yaml  # noqa: E402  (single shared YAML entry point)

DEFAULT_QUESTIONS = Path(__file__).resolve().parent / "competency" / "questions.yaml"

# Query ops the registry may name. Kept small and explicit so an unknown op is a
# loud registry error rather than a silent no-op. ``relationships`` and ``path``
# (issue #41) extend the corpus to controlled subject/predicate/object answers
# and deliberately bounded, deterministic multi-hop traversal — NOT a general
# graph-query language: no GraphRAG, embeddings, model grading, or second store.
KNOWN_OPS = {"entities", "rules", "projection_resources", "relationships", "path"}
# Guard types the registry may name (safety / status / isolation boundaries).
# ``require_field_in`` / ``forbid_field_in`` generalize the status guards to any
# selected column (used for relationship ``source_confidence``);
# ``require_edge_confidence_in`` / ``forbid_edge_confidence_in`` bind to a path
# answer's edge confidences so a draft data-flow can never be asserted as
# verified current architecture (issue #41).
KNOWN_GUARDS = {
    "require_status", "forbid_status", "require_field_equals", "forbid_id_prefix",
    "require_field_in", "forbid_field_in",
    "require_edge_confidence_in", "forbid_edge_confidence_in",
}

# Per-op: the SQLite table, its stable id column, and the plain columns a query
# may filter on or select. Anything else in `select` is treated as a
# ``fields.<name>`` extraction from the row's raw_json. ``relationships`` scopes
# on module membership AND both endpoints being in the projection (see
# ``_scoped_relationships``), never on module membership alone.
OP_TABLE = {
    "entities": ("entities", "entity_id", {"entity_id", "module_id", "entity_type", "status", "source_confidence", "public_facing", "label"}),
    "rules": ("rules", "rule_id", {"rule_id", "module_id", "title", "status", "severity", "rule_type", "statement", "source_confidence"}),
    "relationships": ("relationships", "relationship_id", {"relationship_id", "module_id", "subject", "predicate", "object", "source_confidence"}),
}

# Row ops (a table of per-row columns) vs the two non-row ops.
ROW_OPS = set(OP_TABLE)  # entities, rules, relationships

# Plain columns a query may filter/select on, per op (the ``select`` list also
# accepts a ``fields.<name>`` raw_json path; nothing else is a valid token).
OP_COLUMNS = {op: cols for op, (_t, _id, cols) in OP_TABLE.items()}

# Bounded multi-hop path contract (issue #41). A path query MUST name its start
# and end node constraints, the allowed edge predicates, and explicit hop bounds;
# traversal is a simple-path DFS (no repeated node) capped at PATH_HOP_CAP so it
# stays deliberately bounded and deterministic — not a general graph query.
PATH_HOP_CAP = 6
# Keys a start/end node constraint may carry; at least one is required and all
# values must be strings.
NODE_CONSTRAINT_KEYS = {"id", "id_prefix", "entity_type"}

# Guards that read a path answer's per-edge source_confidence (path op only).
EDGE_CONFIDENCE_GUARDS = {"require_edge_confidence_in", "forbid_edge_confidence_in"}
# Guards that read a per-row status column (never valid on relationships/path/
# projection_resources answers, which have no such column).
STATUS_ROW_GUARDS = {"require_status", "forbid_status"}

# Per-guard operand contract. Each required operand carries a shape check so a
# misspelled operand (e.g. ``prefix`` for ``prefixes``) becomes a loud registry
# error rather than a silent no-op. ``value`` may be any type, so it is
# presence-only.
GUARD_REQUIRED = {
    "require_status": {"statuses": "nonempty_str_list"},
    "forbid_status": {"statuses": "nonempty_str_list"},
    "require_field_equals": {"field": "nonempty_str", "value": "present"},
    "forbid_id_prefix": {"prefixes": "nonempty_str_list"},
    "require_field_in": {"field": "nonempty_str", "values": "nonempty_str_list"},
    "forbid_field_in": {"field": "nonempty_str", "values": "nonempty_str_list"},
    "require_edge_confidence_in": {"values": "nonempty_str_list"},
    "forbid_edge_confidence_in": {"values": "nonempty_str_list"},
}


class QuestionError(Exception):
    """A malformed registry entry (usage error, not a data-answer failure)."""


# The complete, closed set of question-level envelope keys. Anything else is a
# structural error (fail closed) unless it is a deliberate ``x_``-prefixed local
# extension — mirroring the schema layer's escape hatch. A misspelled safety key
# (e.g. ``gaurds:`` for ``guards:``) must therefore be rejected as a usage error
# rather than silently ignored, which would drop every intended guard while the
# question still reported PASS (Codex Reviewer B).
ALLOWED_QUESTION_KEYS = frozenset({
    "id", "client_id", "projection", "question", "rationale",
    "query", "expect", "required", "guards",
})


# --------------------------------------------------------------------------- #
# Registry loading
# --------------------------------------------------------------------------- #
def _q_err(source: str, qid: Any, msg: str) -> None:
    raise QuestionError(f"{source}: question {qid!r}: {msg}")


def _is_scalar(value: Any) -> bool:
    """True for a bare filter/expected scalar (str/int/float/bool, not None)."""
    return isinstance(value, (str, int, float, bool)) and not isinstance(value, dict)


# --------------------------------------------------------------------------- #
# Controlled vocabularies (loaded from the canonical schemas, cached)
# --------------------------------------------------------------------------- #
# A relationship ``predicate``, an entity ``entity_type``, a ``source_confidence``
# or a ``status`` a question names is validated against the SAME controlled set the
# schema layer enforces (``schemas/module.schema.json`` / ``schemas/defs.schema.json``).
# Before this, path ``predicates`` and relationship ``predicate`` filters were
# accepted as bare non-empty strings, so a misspelled predicate (``contians``,
# ``ues``) produced a silently-empty answer that a required question with
# ``expect: []`` and vacuous guards reported as PASS (Codex Reviewer A / B and the
# Integration Auditor each reproduced this). Reading the vocabulary straight from
# the schema keeps the runner in lockstep: adding a predicate is still a single
# schema PR, and the runner picks it up with no second source of truth.
SCHEMAS_DIR = REPO_ROOT / "schemas"
_VOCAB_CACHE: dict[str, frozenset] = {}

# The bounded ``x_`` experimental escape hatch the schema allows on
# ``relationship.predicate`` (module.schema.json anyOf); mirror it so a deliberate
# local predicate extension is accepted while a typo is rejected. Anchored with
# ``\Z`` (portable here — this is Python ``re``, not the schema's ECMAScript
# ``pattern``) so an embedded newline cannot smuggle a token past the check.
_X_PREDICATE = re.compile(r"^x_[a-z][a-z0-9_]*\Z")


def _controlled_vocab(name: str) -> frozenset:
    """Return a controlled vocabulary from the canonical schemas (cached)."""
    if name not in _VOCAB_CACHE:
        if name == "predicate":
            doc = json.loads((SCHEMAS_DIR / "module.schema.json").read_text(encoding="utf-8"))
            vals = doc["$defs"]["predicateName"]["enum"]
        elif name == "entity_type":
            doc = json.loads((SCHEMAS_DIR / "module.schema.json").read_text(encoding="utf-8"))
            vals = doc["$defs"]["entity"]["properties"]["entity_type"]["enum"]
        elif name in ("confidence", "status"):
            doc = json.loads((SCHEMAS_DIR / "defs.schema.json").read_text(encoding="utf-8"))
            vals = doc["$defs"][name]["enum"]
        else:  # pragma: no cover - guarded by the closed _COLUMN_VOCAB map
            raise KeyError(name)
        _VOCAB_CACHE[name] = frozenset(vals)
    return _VOCAB_CACHE[name]


# Which controlled vocabulary each selectable/filterable column draws from, and
# whether the bounded ``x_`` escape applies (only ``predicate`` is x_-extensible in
# the schema). Keyed by the column / output-key name so it works uniformly for a
# filter operand, a guard operand, and an expected-row value.
_COLUMN_VOCAB = {
    "predicate": ("predicate", True),
    "entity_type": ("entity_type", False),
    "source_confidence": ("confidence", False),
    "status": ("status", False),
}


def _check_vocab_token(source: str, qid: Any, label: str, token: Any, vocab_name: str, allow_x: bool) -> None:
    """Reject a controlled-vocabulary operand that is not a real schema term."""
    if not isinstance(token, str):
        _q_err(source, qid, f"{label} must be a string, got {type(token).__name__}")
    if token in _controlled_vocab(vocab_name):
        return
    if allow_x and _X_PREDICATE.match(token):
        return
    hint = " (or an 'x_' experimental extension)" if allow_x else ""
    _q_err(source, qid,
           f"{label} {token!r} is not in the controlled {vocab_name} vocabulary "
           f"{sorted(_controlled_vocab(vocab_name))}{hint}")


def _check_vocab_values(source: str, qid: Any, label: str, values: Any, column: str) -> None:
    """Validate one-or-many operand value(s) for a controlled column (no-op otherwise)."""
    spec = _COLUMN_VOCAB.get(column)
    if spec is None:
        return
    vocab_name, allow_x = spec
    for v in (values if isinstance(values, list) else [values]):
        _check_vocab_token(source, qid, label, v, vocab_name, allow_x)


# Per-op output column(s) a ``forbid_id_prefix`` row guard needs in the select so
# there are ids to inspect. A relationship's leak vector is its ENDPOINTS, so an
# isolation guard on a relationships answer requires both ``subject`` and
# ``object`` to be selected (the id column alone would miss a foreign endpoint).
_OP_ID_OUTPUT = {
    "entities": ("entity_id",),
    "rules": ("rule_id",),
    "relationships": ("subject", "object"),
}


def _check_operand(source: str, qid: Any, guard: dict[str, Any], key: str, kind: str) -> None:
    """Enforce a guard operand's presence and shape (empty/typo operands fail)."""
    if key not in guard:
        _q_err(source, qid, f"guard {guard.get('type')!r} missing required operand {key!r}")
    if kind == "present":
        return
    val = guard[key]
    if kind == "nonempty_str_list":
        if not isinstance(val, list) or not val or not all(isinstance(x, str) for x in val):
            _q_err(source, qid, f"guard {guard.get('type')!r} operand {key!r} must be a non-empty list of strings")
    elif kind == "nonempty_str":
        if not isinstance(val, str) or not val:
            _q_err(source, qid, f"guard {guard.get('type')!r} operand {key!r} must be a non-empty string")


def _validate_guard(source: str, qid: Any, guard: Any, op: str, output_keys: Optional[set]) -> None:
    """Validate one guard: known type, no stray operands, required operands present + shaped.

    Crucially, the guard is also bound to the query's OUTPUT SHAPE so a safety
    assertion can never silently become a no-op (Codex Reviewer A / Integration
    Auditor): a status guard needs ``status`` selected, ``require_field_equals``
    needs its named field selected, and ``forbid_id_prefix`` on a row query needs
    the applicable id column selected. Field-reading guards are meaningless on a
    ``projection_resources`` answer (it has no per-row columns) and are rejected
    there; ``forbid_id_prefix`` is the only guard that applies to resources.
    """
    if not isinstance(guard, dict):
        _q_err(source, qid, "each guard must be a mapping")
    gtype = guard.get("type")
    if gtype not in KNOWN_GUARDS:
        _q_err(source, qid, f"unknown guard type: {gtype!r} (known: {sorted(KNOWN_GUARDS)})")
    required = GUARD_REQUIRED[gtype]
    allowed = {"type", *required.keys()}
    unknown = sorted(k for k in guard if k not in allowed and not k.startswith("x_"))
    if unknown:
        _q_err(source, qid, f"guard {gtype!r} has unknown operand(s) {unknown}; required {sorted(required)}")
    for key, kind in required.items():
        _check_operand(source, qid, guard, key, kind)
    _check_guard_vocab(source, qid, guard, gtype)
    _check_guard_applicability(source, qid, guard, gtype, op, output_keys)


def _check_guard_vocab(source: str, qid: Any, guard: dict[str, Any], gtype: str) -> None:
    """Validate a guard's controlled-vocabulary operands (status / confidence).

    A guard that asserts against a controlled column must name real schema terms:
    ``require_status``/``forbid_status`` against the status vocabulary, the
    edge-confidence guards against the confidence vocabulary, and
    ``require_field_in``/``forbid_field_in``/``require_field_equals`` against their
    field's vocabulary when that field is a controlled column. A typo'd operand
    (e.g. ``statuses: [activ]``) would otherwise make the guard silently vacuous.
    """
    if gtype in ("require_status", "forbid_status"):
        for s in guard.get("statuses") or []:
            _check_vocab_token(source, qid, f"guard {gtype!r} status", s, "status", False)
    elif gtype in EDGE_CONFIDENCE_GUARDS:
        for v in guard.get("values") or []:
            _check_vocab_token(source, qid, f"guard {gtype!r} confidence", v, "confidence", False)
    elif gtype in ("require_field_in", "forbid_field_in"):
        _check_vocab_values(source, qid, f"guard {gtype!r} value", guard.get("values"), guard.get("field"))
    elif gtype == "require_field_equals":
        _check_vocab_values(source, qid, f"guard {gtype!r} value", guard.get("value"), guard.get("field"))


def _check_guard_applicability(
    source: str, qid: Any, guard: dict[str, Any], gtype: str, op: str, output_keys: Optional[set]
) -> None:
    """Reject a guard that cannot bind to the query's output shape (silent no-op).

    Extended for issue #41's ops:
      * status guards read a per-row ``status`` column, so they apply only to
        ``entities``/``rules`` (relationships/path/projection_resources have none);
      * ``require_field_in`` / ``forbid_field_in`` and ``require_field_equals``
        need their field selected in a row op, and never apply to path/resources;
      * edge-confidence guards read a path answer's edge confidences and apply
        ONLY to a ``path`` op;
      * ``forbid_id_prefix`` applies to resources, row ops (needs the op's id
        column selected — both endpoints for relationships), and path answers
        (node ids, no select needed).
    """
    resources = op == "projection_resources"
    is_path = op == "path"
    fields = output_keys or set()

    if gtype in EDGE_CONFIDENCE_GUARDS:
        if not is_path:
            _q_err(source, qid, f"guard {gtype!r} reads path edge confidences and applies only to a path query, not {op!r}")
        return
    if gtype in STATUS_ROW_GUARDS:
        if op not in ("entities", "rules"):
            _q_err(source, qid, f"guard {gtype!r} reads a per-row 'status' column and does not apply to a {op!r} query")
        if "status" not in fields:
            _q_err(source, qid, f"guard {gtype!r} requires 'status' in the query's select {sorted(fields)}")
        return
    if gtype in ("require_field_equals", "require_field_in", "forbid_field_in"):
        if resources or is_path:
            _q_err(source, qid, f"guard {gtype!r} reads a row field and does not apply to a {op!r} query")
        field = guard.get("field")
        if field not in fields:
            _q_err(source, qid, f"guard {gtype!r} requires its field {field!r} in the query's select {sorted(fields)}")
        return
    if gtype == "forbid_id_prefix":
        # Applies to resources (ids from the resource lists) and to path answers
        # (node ids). On a row query it needs the op's id column(s) selected, else
        # there are no ids to check.
        if op in ROW_OPS:
            needed = _OP_ID_OUTPUT[op]
            missing = [c for c in needed if c not in fields]
            if missing:
                label = "id column" if len(needed) == 1 else "endpoint columns"
                _q_err(source, qid, f"guard {gtype!r} requires the {label} {list(needed)} in the query's select {sorted(fields)}")


def _validate_node_constraint(source: str, qid: Any, name: str, node: Any) -> None:
    """A path start/end constraint: a mapping with >=1 known key, all values str."""
    if not isinstance(node, dict) or not node:
        _q_err(source, qid, f"path {name!r} must be a non-empty mapping of node constraints")
    unknown = sorted(k for k in node if k not in NODE_CONSTRAINT_KEYS and not str(k).startswith("x_"))
    if unknown:
        _q_err(source, qid, f"path {name!r} has unknown constraint key(s) {unknown}; allowed {sorted(NODE_CONSTRAINT_KEYS)}")
    bound = [k for k in node if k in NODE_CONSTRAINT_KEYS]
    if not bound:
        _q_err(source, qid, f"path {name!r} must name at least one of {sorted(NODE_CONSTRAINT_KEYS)}")
    for key in bound:
        if not isinstance(node[key], str) or not node[key]:
            _q_err(source, qid, f"path {name!r} constraint {key!r} must be a non-empty string")
    # An ``entity_type`` node constraint must name a real schema entity type, so a
    # typo cannot silently constrain the traversal to nothing.
    if "entity_type" in node:
        _check_vocab_token(source, qid, f"path {name!r} entity_type", node["entity_type"], "entity_type", False)


def _validate_path_query(source: str, qid: Any, query: dict[str, Any]) -> None:
    """Validate a bounded multi-hop path query's discriminated shape (issue #41).

    A path query is deliberately explicit: it MUST name ``start``/``end`` node
    constraints, an allowed-``predicates`` list, and integer ``min_hops``/
    ``max_hops`` bounds (1 <= min <= max <= PATH_HOP_CAP). Anything missing,
    mistyped, or unbounded is a usage error rejected BEFORE traversal, so a
    malformed path can never silently return an empty or unbounded answer.
    """
    allowed = {"op", "start", "end", "predicates", "min_hops", "max_hops"}
    stray = sorted(k for k in query if k not in allowed and not k.startswith("x_"))
    if stray:
        _q_err(source, qid, f"path query has unknown key(s) {stray}; allowed {sorted(allowed - {'op'})}")
    for req in ("start", "end", "predicates", "min_hops", "max_hops"):
        if req not in query:
            _q_err(source, qid, f"path query missing required key {req!r}")
    _validate_node_constraint(source, qid, "start", query["start"])
    _validate_node_constraint(source, qid, "end", query["end"])
    preds = query["predicates"]
    if not isinstance(preds, list) or not preds or not all(isinstance(p, str) and p for p in preds):
        _q_err(source, qid, "path 'predicates' must be a non-empty list of non-empty strings")
    if len(set(preds)) != len(preds):
        _q_err(source, qid, f"path 'predicates' has duplicate entries: {preds}")
    # Every allowed predicate must be a real schema predicate (or an x_ extension).
    # This is the direct fix for the reproduced false-pass: a path over a misspelled
    # predicate (``creates_or_updtaes``, ``contians``) with ``expect.paths: []`` and
    # a universal edge-confidence guard used to validate, evaluate to ``[]``, and
    # report PASS because the guard is vacuous over an empty answer.
    for p in preds:
        _check_vocab_token(source, qid, "path predicate", p, "predicate", True)
    lo, hi = query["min_hops"], query["max_hops"]
    # bool is an int subclass; reject it so ``min_hops: true`` cannot pose as 1.
    for name, val in (("min_hops", lo), ("max_hops", hi)):
        if not isinstance(val, int) or isinstance(val, bool):
            _q_err(source, qid, f"path {name!r} must be an integer, got {type(val).__name__}")
    if lo < 1:
        _q_err(source, qid, f"path 'min_hops' must be >= 1, got {lo}")
    if hi < lo:
        _q_err(source, qid, f"path 'max_hops' ({hi}) must be >= 'min_hops' ({lo})")
    if hi > PATH_HOP_CAP:
        _q_err(source, qid, f"path 'max_hops' ({hi}) exceeds the bounded cap {PATH_HOP_CAP}")


def _validate_query(source: str, qid: Any, query: Any) -> tuple[str, Optional[set]]:
    """Validate a query's discriminated shape; return ``(op, output_keys)``.

    ``output_keys`` is the set of column keys a row answer will carry (None for
    ``projection_resources``). Unknown ops, stray keys, invalid filter/select
    columns, and duplicate output keys all fail here — so a typo can never
    resolve to a silent ``None`` at query time.
    """
    if not isinstance(query, dict):
        _q_err(source, qid, f"'query' must be a mapping, got {type(query).__name__}")
    op = query.get("op")
    if op not in KNOWN_OPS:
        _q_err(source, qid, f"unknown query op: {op!r} (known: {sorted(KNOWN_OPS)})")

    if op == "projection_resources":
        stray = sorted(k for k in query if k != "op" and not k.startswith("x_"))
        if stray:
            _q_err(source, qid, f"projection_resources query takes no operand(s) {stray}")
        return op, None

    if op == "path":
        _validate_path_query(source, qid, query)
        return op, None

    stray = sorted(k for k in query if k not in {"op", "filters", "select"} and not k.startswith("x_"))
    if stray:
        _q_err(source, qid, f"{op} query has unknown key(s) {stray}")
    columns = OP_COLUMNS[op]

    filters = query.get("filters")
    if filters is not None:
        if not isinstance(filters, dict):
            _q_err(source, qid, "'filters' must be a mapping")
        for col, want in filters.items():
            if col not in columns:
                _q_err(source, qid, f"filter column {col!r} is not a valid {op} column {sorted(columns)}")
            # A filter operand must be a scalar or a list of scalars — a mapping
            # (e.g. the reviewer's `status: {typo: draft}`) or a null can never
            # match a column value and would silently drop the filter, so reject.
            if isinstance(want, list):
                if not want or not all(_is_scalar(v) for v in want):
                    _q_err(source, qid, f"filter {col!r} list operand must be a non-empty list of scalars")
            elif not _is_scalar(want):
                _q_err(source, qid, f"filter {col!r} operand must be a scalar or list of scalars, got {type(want).__name__}")
            # A controlled column (predicate / entity_type / source_confidence /
            # status) must filter on a real schema term — a typo'd predicate would
            # otherwise silently match nothing and pass a required question.
            _check_vocab_values(source, qid, f"filter {col!r} value", want, col)

    select = query.get("select")
    if not isinstance(select, list) or not select or not all(isinstance(t, str) for t in select):
        _q_err(source, qid, "'select' must be a non-empty list of column tokens")
    output_keys: list[str] = []
    for tok in select:
        if tok.startswith("fields."):
            if not tok.split(".", 1)[1]:
                _q_err(source, qid, f"select token {tok!r} names no field")
        elif tok not in columns:
            _q_err(source, qid, f"select token {tok!r} is not a valid {op} column {sorted(columns)} and is not a 'fields.<name>' path")
        key = _output_key(tok)
        if key in output_keys:
            _q_err(source, qid, f"select produces duplicate output key {key!r}")
        output_keys.append(key)
    return op, set(output_keys)


def _expect_envelope_key(op: str) -> str:
    """The single legal top-level ``expect`` key for an op."""
    return {"projection_resources": "resources", "path": "paths"}.get(op, "rows")


def _validate_expect(source: str, qid: Any, op: str, output_keys: Optional[set], query: dict[str, Any], expect: Any) -> None:
    """Validate the expect payload against the op, the query, and its output keys.

    The ``expect`` envelope is CLOSED: only the op's one legal key (plus an ``x_``
    extension) is allowed, so a stray ``expect.pathz`` cannot leave the real
    ``paths`` empty while the misspelled twin is silently ignored (Codex Reviewer A
    / Integration Auditor). For a ``path`` op each expected chain is additionally
    validated AGAINST the query — its hop count within ``[min_hops, max_hops]``, its
    predicates drawn from the query's allowed set, and its endpoints consistent with
    the ``start``/``end`` id / id_prefix constraints — so an expected path that
    contradicts the query it claims to answer is a usage error, not a trusted
    false-positive.
    """
    if not isinstance(expect, dict):
        _q_err(source, qid, "'expect' must be a mapping")
    legal = _expect_envelope_key(op)
    stray = sorted(k for k in expect if k != legal and not str(k).startswith("x_"))
    if stray:
        _q_err(source, qid, f"expect has unknown key(s) {stray}; the only allowed expect key for a {op!r} query is {legal!r}")
    if op == "projection_resources":
        resources = expect.get("resources")
        if not isinstance(resources, dict):
            _q_err(source, qid, "projection_resources question must define expect.resources as a mapping")
        for key, val in resources.items():
            if key not in {"modules", "entities", "rules"}:
                _q_err(source, qid, f"expect.resources has unknown key {key!r} (allowed: modules/entities/rules)")
            if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
                _q_err(source, qid, f"expect.resources.{key} must be a list of strings")
        return
    if op == "path":
        paths = expect.get("paths")
        if not isinstance(paths, list):
            _q_err(source, qid, "path question must define expect.paths as a list")
        for path in paths:
            if not isinstance(path, dict) or set(path.keys()) != {"chain", "confidences"}:
                _q_err(source, qid, "each expect.paths entry must be a mapping with exactly 'chain' and 'confidences'")
            chain, conf = path["chain"], path["confidences"]
            if not isinstance(chain, list) or len(chain) < 3 or len(chain) % 2 == 0 or not all(isinstance(x, str) for x in chain):
                _q_err(source, qid, "expect.paths[].chain must be an odd-length (node,predicate,...,node) list of strings, length >= 3")
            if not isinstance(conf, list) or not all(isinstance(x, str) for x in conf) or len(conf) != (len(chain) - 1) // 2:
                _q_err(source, qid, "expect.paths[].confidences must be a list of strings, one per edge in 'chain'")
            _validate_expected_chain(source, qid, query, chain, conf)
        return
    rows = expect.get("rows")
    if not isinstance(rows, list):
        _q_err(source, qid, f"{op} question must define expect.rows as a list")
    for row in rows:
        if not isinstance(row, dict):
            _q_err(source, qid, "each expect.rows entry must be a mapping")
        keys = set(row.keys())
        if keys != output_keys:
            _q_err(source, qid, f"expect row keys {sorted(keys)} do not match select output keys {sorted(output_keys or [])}")
        # An expected value on a controlled column (predicate / source_confidence /
        # entity_type / status) must itself be a real schema term, so an expected
        # row cannot assert a typo'd predicate the query could never return.
        for col, val in row.items():
            _check_vocab_values(source, qid, f"expect row {col!r} value", val, col)


def _endpoint_matches(constraint: dict[str, Any], node: str) -> Optional[str]:
    """Return a mismatch reason if ``node`` violates the id/id_prefix constraint.

    ``entity_type`` is intentionally NOT checked here — that needs the export to
    know a node's type — so this validates only the constraints resolvable from the
    registry alone. The runtime comparison still catches a wrong-typed endpoint.
    """
    if "id" in constraint and node != constraint["id"]:
        return f"expected endpoint {node!r} != required id {constraint['id']!r}"
    if "id_prefix" in constraint and not node.startswith(constraint["id_prefix"]):
        return f"expected endpoint {node!r} does not start with required id_prefix {constraint['id_prefix']!r}"
    return None


def _validate_expected_chain(source: str, qid: Any, query: dict[str, Any], chain: list, conf: list) -> None:
    """Relate one expected path chain to the query's own bounds/predicates/endpoints."""
    nodes, preds = chain[0::2], chain[1::2]
    hops = len(preds)
    lo, hi = query["min_hops"], query["max_hops"]
    if not (lo <= hops <= hi):
        _q_err(source, qid, f"expect.paths[].chain has {hops} hop(s), outside the query bounds [{lo}, {hi}]")
    allowed = set(query["predicates"])
    bad = [p for p in preds if p not in allowed]
    if bad:
        _q_err(source, qid, f"expect.paths[].chain uses predicate(s) {sorted(set(bad))} not in the query's allowed predicates {sorted(allowed)}")
    for name, node in (("start", nodes[0]), ("end", nodes[-1])):
        reason = _endpoint_matches(query[name], node)
        if reason:
            _q_err(source, qid, f"expect.paths[] {name} {reason}")
    for c in conf:
        _check_vocab_token(source, qid, "expect.paths[].confidences value", c, "confidence", False)


def validate_questions(doc: Any, source: str) -> list[dict[str, Any]]:
    """Shape-check an already-parsed registry document; return its questions.

    Raises QuestionError on any structural defect — a non-mapping registry,
    missing/duplicate identity fields, a missing/non-string human-readable
    ``question`` or ``rationale``, an unknown question-level key (except a
    deliberate ``x_`` extension — so a misspelled ``gaurds:`` fails closed rather
    than silently dropping guards), an unknown/malformed query, an unknown or
    misspelled guard operand, a select token that is not a real column, duplicate
    output keys, or an expect payload whose keys/types do not match the query. A
    broken registry therefore fails as a usage error BEFORE evaluation instead of
    silently passing a plausible false-positive answer.
    """
    if not isinstance(doc, dict):
        raise QuestionError(f"{source}: registry must be a mapping")
    questions = doc.get("questions")
    if not isinstance(questions, list) or not questions:
        raise QuestionError(f"{source}: registry must define a non-empty 'questions' list")
    seen: set[str] = set()
    for q in questions:
        if not isinstance(q, dict):
            raise QuestionError(f"{source}: each question must be a mapping")
        # Envelope identity fields must be non-empty STRINGS. A mapping- or
        # list-valued id/client_id/projection previously slipped past a bare
        # truthiness check and then crashed with a raw ``TypeError`` (unhashable
        # dict in ``seen``, or ``root / client_id`` during path resolution) at
        # exit 1 instead of a structured QuestionError / exit 2 (Integration
        # Auditor). Validate the full envelope BEFORE any hashing, path
        # resolution, or evaluation.
        for field in ("id", "client_id", "projection"):
            val = q.get(field)
            if not isinstance(val, str) or not val:
                raise QuestionError(
                    f"{source}: each question needs a non-empty string {field!r}; got {val!r}"
                )
        qid = q["id"]
        # Fail-closed envelope: reject any unknown question-level key (except a
        # deliberate ``x_``-prefixed extension). Without this, a misspelled
        # ``gaurds:`` was accepted and the runner ignored every intended safety
        # assertion while still reporting PASS (Codex Reviewer B). Do this BEFORE
        # trusting the rest of the envelope so a typo cannot smuggle in a no-op.
        unknown = sorted(
            k for k in q
            if k not in ALLOWED_QUESTION_KEYS and not str(k).startswith("x_")
        )
        if unknown:
            _q_err(source, qid,
                   f"unknown question-level key(s) {unknown}; "
                   f"use an 'x_'-prefixed key for a deliberate local extension")
        # Issue #31 requires every question to carry a human-readable ``question``
        # and a ``rationale`` (the consumer job it protects). A missing or
        # non-string value previously slipped through; enforce both as non-empty
        # strings before evaluation (Codex Reviewer A / B).
        for field in ("question", "rationale"):
            val = q.get(field)
            if not isinstance(val, str) or not val.strip():
                _q_err(source, qid,
                       f"needs a non-empty string {field!r}; got {val!r}")
        if q.get("query") is None:
            _q_err(source, qid, "missing required field 'query'")
        if qid in seen:
            raise QuestionError(f"{source}: duplicate question id: {qid}")
        seen.add(qid)
        # ``required`` gates the exit code; a non-boolean (e.g. ``required: 0``)
        # would let a FAILING required question be treated as optional and exit
        # 0 (Codex Reviewer A). Enforce a real boolean before evaluation.
        req = q.get("required", True)
        if not isinstance(req, bool):
            _q_err(source, qid, f"'required' must be a boolean (true/false), got {type(req).__name__}")
        op, output_keys = _validate_query(source, qid, q["query"])
        _validate_expect(source, qid, op, output_keys, q["query"], q.get("expect") or {})
        guards = q.get("guards")
        if guards is not None and not isinstance(guards, list):
            _q_err(source, qid, "'guards' must be a list")
        for guard in guards or []:
            _validate_guard(source, qid, guard, op, output_keys)
    return questions


def load_questions(path: Path = DEFAULT_QUESTIONS) -> list[dict[str, Any]]:
    """Parse the competency registry through the shared loader and shape-check it.

    Returns the list of question dicts. Raises QuestionError on a structurally
    malformed registry (see ``validate_questions``) so a broken registry fails
    fast instead of silently passing. A non-mapping / unparseable registry root
    (``parse_yaml`` raises ``ValueError``) is normalized to ``QuestionError`` so
    it surfaces as a structured usage error / exit 2, not an uncaught traceback.
    """
    try:
        doc = parse_yaml(path)
    except ValueError as exc:
        raise QuestionError(f"{path}: {exc}") from exc
    return validate_questions(doc, str(path))


# --------------------------------------------------------------------------- #
# Projection scope resolution (mirrors the validator / check_rules semantics)
# --------------------------------------------------------------------------- #
def _id_matches(candidate: Optional[str], patterns: list) -> bool:
    """True if ``candidate`` equals a pattern or matches its ``.*`` prefix wildcard."""
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


def _load_projection_includes(conn: sqlite3.Connection, client_id: str, projection_id: str) -> dict[str, Any]:
    """Return the projection's ``includes`` mapping, or raise if it is unknown.

    The lookup is pinned to (client_id, projection_id) so a question can never
    accidentally resolve another client's projection.
    """
    row = conn.execute(
        "SELECT includes_json FROM projections WHERE client_id = ? AND projection_id = ?",
        (client_id, projection_id),
    ).fetchone()
    if row is None:
        raise QuestionError(f"unknown projection for client {client_id!r}: {projection_id!r}")
    includes = json.loads(row[0]) if row[0] else {}
    return includes if isinstance(includes, dict) else {}


def _scoped_rows(conn: sqlite3.Connection, op: str, client_id: str, includes: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the client's entity/rule rows that the projection pulls into scope.

    In scope ⟺ the row's module is listed in ``includes.modules`` OR the row's id
    is named (or ``.*``-matched) in ``includes.entities`` / ``includes.rules``.
    Rows are restricted to ``client_id`` first, so cross-client leakage is
    impossible even before scope filtering.
    """
    table, id_col, columns = OP_TABLE[op]
    ordered_cols = sorted(columns)
    select_cols = ", ".join(ordered_cols) + ", raw_json"
    scoped_modules = set(includes.get("modules") or [])
    id_patterns = includes.get("entities" if op == "entities" else "rules") or []

    rows: list[dict[str, Any]] = []
    for record in conn.execute(f"SELECT {select_cols} FROM {table} WHERE client_id = ?", (client_id,)):
        data = dict(zip(ordered_cols, record[:-1]))
        data["_raw"] = json.loads(record[-1]) if record[-1] else {}
        in_scope = data.get("module_id") in scoped_modules or _id_matches(data.get(id_col), id_patterns)
        if in_scope:
            rows.append(data)
    return rows


def _entity_scope_set(conn: sqlite3.Connection, client_id: str, includes: dict[str, Any]) -> set:
    """The client's entity ids the projection pulls into scope.

    In scope ⟺ the entity's module is in ``includes.modules`` OR its id is named
    (or ``.*``-matched) in ``includes.entities``. Restricted to ``client_id`` so
    no other client's entity can ever be treated as an in-scope endpoint.
    """
    scoped_modules = set(includes.get("modules") or [])
    id_patterns = includes.get("entities") or []
    in_scope: set = set()
    for eid, module_id in conn.execute(
        "SELECT entity_id, module_id FROM entities WHERE client_id = ?", (client_id,)
    ):
        if module_id in scoped_modules or _id_matches(eid, id_patterns):
            in_scope.add(eid)
    return in_scope


def _scoped_relationships(conn: sqlite3.Connection, client_id: str, includes: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the client's relationship rows the projection pulls into scope.

    A relationship is in scope ⟺ (a) it is restricted to ``client_id`` — no
    cross-client edge is ever considered — AND (b) its defining module is in
    ``includes.modules`` AND (c) BOTH its ``subject`` and ``object`` entities are
    in the projection's entity scope. Requiring both endpoints (not just module
    membership) is what enforces "projection scope for relationship endpoints"
    (issue #41): an edge whose object entity has left the projection is dropped,
    not silently surfaced with a foreign/out-of-scope endpoint.
    """
    table, _id_col, columns = OP_TABLE["relationships"]
    ordered_cols = sorted(columns)
    select_cols = ", ".join(ordered_cols) + ", raw_json"
    scoped_modules = set(includes.get("modules") or [])
    entity_scope = _entity_scope_set(conn, client_id, includes)

    rows: list[dict[str, Any]] = []
    for record in conn.execute(f"SELECT {select_cols} FROM {table} WHERE client_id = ?", (client_id,)):
        data = dict(zip(ordered_cols, record[:-1]))
        data["_raw"] = json.loads(record[-1]) if record[-1] else {}
        if (
            data.get("module_id") in scoped_modules
            and data.get("subject") in entity_scope
            and data.get("object") in entity_scope
        ):
            rows.append(data)
    return rows


# --------------------------------------------------------------------------- #
# Query execution
# --------------------------------------------------------------------------- #
def _filter_matches(row: dict[str, Any], filters: dict[str, Any]) -> bool:
    """Deterministic AND of column filters. A list value means membership."""
    for col, wanted in (filters or {}).items():
        actual = row.get(col)
        if isinstance(wanted, list):
            if actual not in wanted:
                return False
        elif actual != wanted:
            return False
    return True


def _resolve_select(row: dict[str, Any], token: str) -> Any:
    """Resolve one select token to a value: a plain column, or ``fields.<name>``.

    ``fields.<name>`` reads ``row.fields.<name>`` from the raw_json so a question
    can assert on entity/rule field values (e.g. ``fields.baseline``). The output
    key is the last path segment (``baseline``).
    """
    if token.startswith("fields."):
        name = token.split(".", 1)[1]
        fields = row.get("_raw", {}).get("fields", {})
        return fields.get(name) if isinstance(fields, dict) else None
    return row.get(token)


def _output_key(token: str) -> str:
    return token.split(".")[-1]


def run_query(conn: sqlite3.Connection, question: dict[str, Any]) -> Any:
    """Execute a question's projection-scoped query and return its normalized answer.

    ``entities`` / ``rules`` return a sorted list of projected row dicts.
    ``projection_resources`` returns ``{modules, entities, rules}`` with each list
    sorted. All ordering is deterministic so comparison and output are stable.
    """
    query = question["query"]
    op = query["op"]
    client_id = question["client_id"]
    includes = _load_projection_includes(conn, client_id, question["projection"])

    if op == "projection_resources":
        return {
            "modules": sorted(includes.get("modules") or []),
            "entities": sorted(includes.get("entities") or []),
            "rules": sorted(includes.get("rules") or []),
        }

    if op == "path":
        return _run_path(conn, client_id, includes, query)

    rows = (
        _scoped_relationships(conn, client_id, includes)
        if op == "relationships"
        else _scoped_rows(conn, op, client_id, includes)
    )
    filters = query.get("filters") or {}
    select = query.get("select") or []
    projected = [
        {_output_key(tok): _resolve_select(r, tok) for tok in select}
        for r in rows
        if _filter_matches(r, filters)
    ]
    return _normalize_rows(projected)


def _node_matches(entity_type: Optional[str], node_id: str, constraint: dict[str, Any]) -> bool:
    """True if a node satisfies a path start/end constraint (all keys ANDed)."""
    if "id" in constraint and node_id != constraint["id"]:
        return False
    if "id_prefix" in constraint and not node_id.startswith(constraint["id_prefix"]):
        return False
    if "entity_type" in constraint and entity_type != constraint["entity_type"]:
        return False
    return True


def _run_path(conn: sqlite3.Connection, client_id: str, includes: dict[str, Any], query: dict[str, Any]) -> list[dict[str, Any]]:
    """Enumerate bounded, deterministic simple paths within the projection scope.

    Traversal walks ONLY projection-scoped relationships (``_scoped_relationships``
    already guarantees both endpoints are in scope and the edge is single-client),
    following ONLY the query's allowed predicates. A path is recorded when its hop
    count is within ``[min_hops, max_hops]`` and its terminal node satisfies the
    ``end`` constraint; it is a simple path (no repeated node) capped at
    ``max_hops`` so the search is finite and deterministic. Every traversed node
    and edge therefore stays inside the named projection and client — the traversal
    can never cross into an excluded module or another client.
    """
    allowed_preds = set(query["predicates"])
    min_hops, max_hops = query["min_hops"], query["max_hops"]
    start_c, end_c = query["start"], query["end"]

    # Entity attributes for the client (node type + scope membership).
    entity_type: dict[str, str] = {}
    for eid, etype in conn.execute(
        "SELECT entity_id, entity_type FROM entities WHERE client_id = ?", (client_id,)
    ):
        entity_type[eid] = etype
    entity_scope = _entity_scope_set(conn, client_id, includes)

    # Scoped, predicate-filtered adjacency (subject -> list of edges).
    adjacency: dict[str, list[dict[str, Any]]] = {}
    for rel in _scoped_relationships(conn, client_id, includes):
        if rel["predicate"] in allowed_preds:
            adjacency.setdefault(rel["subject"], []).append(rel)

    starts = sorted(
        eid for eid in entity_scope if _node_matches(entity_type.get(eid), eid, start_c)
    )
    results: list[dict[str, Any]] = []

    def walk(node: str, nodes: list[str], preds: list[str], confs: list[str], hops: int) -> None:
        if min_hops <= hops <= max_hops and _node_matches(entity_type.get(node), node, end_c):
            chain: list[str] = []
            for i, n in enumerate(nodes):
                chain.append(n)
                if i < len(preds):
                    chain.append(preds[i])
            results.append({"chain": chain, "confidences": list(confs)})
        if hops >= max_hops:
            return
        for edge in sorted(adjacency.get(node, []), key=lambda e: e["relationship_id"]):
            nxt = edge["object"]
            if nxt in nodes:  # simple path only — never revisit a node (no cycles)
                continue
            walk(nxt, nodes + [nxt], preds + [edge["predicate"]], confs + [edge["source_confidence"]], hops + 1)

    for start in starts:
        walk(start, [start], [], [], 0)
    return _normalize_paths(results)


def _normalize_paths(paths: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort path dicts deterministically AND deduplicate identical ones.

    The public path representation is (nodes, predicates, confidences) and
    deliberately omits relationship IDs (Codex Reviewer A). Two PARALLEL edges with
    identical endpoints, predicate, and confidence therefore produce the same public
    path object; without dedup they surfaced as two indistinguishable paths and
    ``_compare_paths`` could only say "expected 1, got 2" with empty missing/
    unexpected diagnostics. Collapsing identical path objects makes the answer a
    set of distinct paths, which is the honest semantics for an edge-identity-free
    representation. Ordering stays deterministic (JSON key sort).
    """
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for p in sorted(paths, key=lambda p: json.dumps(p, sort_keys=True, ensure_ascii=False)):
        key = json.dumps(p, sort_keys=True, ensure_ascii=False)
        if key in seen:
            continue
        seen.add(key)
        ordered.append(p)
    return ordered


def _normalize_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort row dicts deterministically (by their sorted key/value tuples)."""
    return sorted(rows, key=lambda r: json.dumps(r, sort_keys=True, ensure_ascii=False))


# --------------------------------------------------------------------------- #
# Comparison + guards
# --------------------------------------------------------------------------- #
def _row_key(row: dict[str, Any]) -> str:
    return json.dumps(row, sort_keys=True, ensure_ascii=False)


def _compare_rows(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[str]:
    """Return human diagnostics for an expected-vs-actual row-set mismatch."""
    exp = _normalize_rows([dict(r) for r in expected])
    act = _normalize_rows([dict(r) for r in actual])
    if exp == act:
        return []
    exp_keys = {_row_key(r) for r in exp}
    act_keys = {_row_key(r) for r in act}
    missing = [r for r in exp if _row_key(r) not in act_keys]
    unexpected = [r for r in act if _row_key(r) not in exp_keys]
    failures = [f"row set mismatch: expected {len(exp)} row(s), got {len(act)}"]
    if missing:
        failures.append("  missing (expected, not returned): " + json.dumps(missing, ensure_ascii=False))
    if unexpected:
        failures.append("  unexpected (returned, not expected): " + json.dumps(unexpected, ensure_ascii=False))
    return failures


def _compare_resources(expected: dict[str, Any], actual: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in ("modules", "entities", "rules"):
        exp = sorted(expected.get(key) or [])
        act = sorted(actual.get(key) or [])
        if exp != act:
            missing = [x for x in exp if x not in act]
            unexpected = [x for x in act if x not in exp]
            failures.append(
                f"{key} mismatch: missing={missing} unexpected={unexpected}"
            )
    return failures


def _compare_paths(expected: list[dict[str, Any]], actual: list[dict[str, Any]]) -> list[str]:
    """Return human diagnostics for an expected-vs-actual path-set mismatch."""
    exp = _normalize_paths([dict(p) for p in expected])
    act = _normalize_paths([dict(p) for p in actual])
    if exp == act:
        return []
    exp_keys = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in exp}
    act_keys = {json.dumps(p, sort_keys=True, ensure_ascii=False) for p in act}
    missing = [p for p in exp if json.dumps(p, sort_keys=True, ensure_ascii=False) not in act_keys]
    unexpected = [p for p in act if json.dumps(p, sort_keys=True, ensure_ascii=False) not in exp_keys]
    failures = [f"path set mismatch: expected {len(exp)} path(s), got {len(act)}"]
    if missing:
        failures.append("  missing (expected, not returned): " + json.dumps(missing, ensure_ascii=False))
    if unexpected:
        failures.append("  unexpected (returned, not expected): " + json.dumps(unexpected, ensure_ascii=False))
    return failures


def _ids_of(op: str, actual: Any) -> list[str]:
    """The id values in a result, for id-prefix isolation guards."""
    if op == "projection_resources":
        return [*actual.get("modules", []), *actual.get("entities", []), *actual.get("rules", [])]
    if op == "path":
        # Every node visited by every returned path (chain positions 0,2,4,...).
        return [tok for path in actual for i, tok in enumerate(path.get("chain", [])) if i % 2 == 0]
    ids: list[str] = []
    for row in actual:
        # entity/rule id columns plus a relationship's endpoints + its own id.
        for key in ("entity_id", "rule_id", "subject", "object", "relationship_id"):
            if key in row and isinstance(row[key], str):
                ids.append(row[key])
    return ids


def _edge_confidences_of(actual: Any) -> list[str]:
    """Every per-edge source_confidence across all returned paths."""
    return [c for path in actual for c in path.get("confidences", [])]


def _check_guards(op: str, actual: Any, guards: list[dict[str, Any]]) -> list[str]:
    """Evaluate safety/status/isolation guards against the actual answer."""
    failures: list[str] = []
    rows = actual if op in ROW_OPS else []
    for guard in guards or []:
        gtype = guard.get("type")
        if gtype == "require_status":
            allowed = set(guard.get("statuses") or [])
            bad = sorted({r.get("status") for r in rows if r.get("status") not in allowed})
            if bad:
                failures.append(f"guard require_status: found disallowed status(es) {bad} (allowed {sorted(allowed)})")
        elif gtype == "forbid_status":
            forbidden = set(guard.get("statuses") or [])
            bad = sorted({r.get("status") for r in rows if r.get("status") in forbidden})
            if bad:
                failures.append(f"guard forbid_status: found forbidden status(es) {bad}")
        elif gtype == "require_field_equals":
            field, value = guard.get("field"), guard.get("value")
            bad = sorted({str(r.get(field)) for r in rows if r.get(field) != value})
            if bad:
                failures.append(f"guard require_field_equals: {field!r} must equal {value!r}, saw {bad}")
        elif gtype == "require_field_in":
            field, values = guard.get("field"), set(guard.get("values") or [])
            bad = sorted({str(r.get(field)) for r in rows if r.get(field) not in values})
            if bad:
                failures.append(f"guard require_field_in: {field!r} must be in {sorted(values)}, saw {bad}")
        elif gtype == "forbid_field_in":
            field, values = guard.get("field"), set(guard.get("values") or [])
            bad = sorted({str(r.get(field)) for r in rows if r.get(field) in values})
            if bad:
                failures.append(f"guard forbid_field_in: {field!r} must not be in {sorted(values)}, saw {bad}")
        elif gtype == "require_edge_confidence_in":
            values = set(guard.get("values") or [])
            bad = sorted({c for c in _edge_confidences_of(actual) if c not in values})
            if bad:
                failures.append(f"guard require_edge_confidence_in: every path edge confidence must be in {sorted(values)}, saw {bad}")
        elif gtype == "forbid_edge_confidence_in":
            values = set(guard.get("values") or [])
            bad = sorted({c for c in _edge_confidences_of(actual) if c in values})
            if bad:
                failures.append(f"guard forbid_edge_confidence_in: no path edge confidence may be in {sorted(values)}, saw {bad}")
        elif gtype == "forbid_id_prefix":
            prefixes = tuple(guard.get("prefixes") or [])
            bad = sorted({i for i in _ids_of(op, actual) if i.startswith(prefixes)})
            if bad:
                failures.append(f"guard forbid_id_prefix: ids leaked from {list(prefixes)}: {bad}")
    return failures


# --------------------------------------------------------------------------- #
# Evaluation
# --------------------------------------------------------------------------- #
def evaluate_question(conn: sqlite3.Connection, question: dict[str, Any]) -> dict[str, Any]:
    """Run one question and return a result record with pass/fail + diagnostics."""
    op = question["query"]["op"]
    expect = question.get("expect") or {}
    result: dict[str, Any] = {
        "id": question["id"],
        "client_id": question["client_id"],
        "projection": question["projection"],
        "op": op,
        "required": question.get("required", True),
    }
    failures: list[str] = []
    try:
        actual = run_query(conn, question)
    except QuestionError as exc:
        result.update(status="fail", failures=[f"query error: {exc}"], expected=expect, actual=None)
        return result

    if op == "projection_resources":
        expected = {k: sorted(v or []) for k, v in (expect.get("resources") or {}).items()}
        failures += _compare_resources(expected, actual)
    elif op == "path":
        expected = _normalize_paths([dict(p) for p in expect.get("paths") or []])
        failures += _compare_paths(expected, actual)
    else:
        expected = _normalize_rows([dict(r) for r in expect.get("rows") or []])
        failures += _compare_rows(expected, actual)
    failures += _check_guards(op, actual, question.get("guards") or [])

    result.update(
        status="pass" if not failures else "fail",
        failures=failures,
        expected=expected,
        actual=actual,
    )
    return result


def evaluate_suite(db_path: Path, questions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evaluate every question against the SQLite export at ``db_path``.

    Library entry point (issue #19 reuse seam): pass a service-produced export
    and compare the returned ``status`` fields to prove answer parity without
    re-encoding any expected value. Results are sorted by question id.
    """
    conn = sqlite3.connect(db_path)
    try:
        results = [evaluate_question(conn, q) for q in questions]
    finally:
        conn.close()
    return sorted(results, key=lambda r: r["id"])


# --------------------------------------------------------------------------- #
# Projection/client-directed loading (issue #31 acceptance criterion)
# --------------------------------------------------------------------------- #
def _safe_join(base: Path, rel: Any) -> Path:
    """Join a manifest-declared relative path, refusing to escape ``base``."""
    if not isinstance(rel, str) or not rel:
        raise QuestionError(f"manifest path must be a non-empty string, got {rel!r}")
    target = (base / rel).resolve()
    base_r = base.resolve()
    if target != base_r and base_r not in target.parents:
        raise QuestionError(f"manifest path {rel!r} escapes the client directory")
    return target


def _collect_ids(paths: list[Path]) -> set:
    """Parse module files and return the entity + rule ids they define."""
    ids: set = set()
    for p in paths:
        doc = parse_yaml(p)
        for ent in doc.get("entities") or []:
            if isinstance(ent, dict) and ent.get("id"):
                ids.add(ent["id"])
        for rule in doc.get("rules") or []:
            if isinstance(rule, dict) and rule.get("id"):
                ids.add(rule["id"])
    return ids


def resolve_scope_paths(root: Path, client_id: str, projection_id: str) -> tuple[list[Path], dict[str, Any]]:
    """Compute the minimal file set to answer one (client, projection) question.

    Projection/client-directed loading (issue #31 AC): reads the named client's
    manifest to discover module/projection membership, then returns ONLY the
    manifest, ``client.yaml``, the named projection, and the module files that
    projection pulls into scope — ``includes.modules`` plus, when the projection
    references an entity/rule owned by a module that is NOT in ``includes.modules``
    (or uses a ``.*`` wildcard), the client's full (still single-client) module
    set. It never reads another client's files, and it never PARSES a module the
    projection excludes: reference resolution only ever parses modules already in
    scope, and if that is not enough to resolve every referenced id it widens to
    the full client module set rather than scanning-and-excluding other modules
    (which would parse a file it then reports as excluded — Codex Reviewer A).
    Raises ``QuestionError`` for an unknown client/projection, a client_id that
    escapes ``clients/``, or a manifest that mislabels either — a structured
    usage error, not a silent empty answer.
    """
    clients_root = (root / "clients").resolve()
    cdir = (clients_root / client_id).resolve()
    # Containment: a registry-derived client_id must name a direct child of
    # clients/ (reject ``..`` traversal, nested paths, absolute paths) BEFORE any
    # file is opened.
    if cdir.parent != clients_root:
        raise QuestionError(f"invalid client_id {client_id!r}: must be a direct child of clients/")
    manifest_path = cdir / "ontology.yaml"
    if not manifest_path.is_file():
        raise QuestionError(f"unknown client {client_id!r}: no manifest at {manifest_path}")
    manifest = parse_yaml(manifest_path)
    if manifest.get("kind") != "ontology" or manifest.get("client_id") != client_id:
        raise QuestionError(f"{manifest_path}: not the ontology manifest for client {client_id!r}")

    module_paths: dict[str, Path] = {}
    for m in manifest.get("modules") or []:
        if isinstance(m, dict) and m.get("id"):
            module_paths[m["id"]] = _safe_join(cdir, m.get("path"))
    projection_paths: dict[str, Path] = {}
    for p in manifest.get("projections") or []:
        if isinstance(p, dict) and p.get("id"):
            projection_paths[p["id"]] = _safe_join(cdir, p.get("path"))
    if projection_id not in projection_paths:
        raise QuestionError(f"projection {projection_id!r} is not declared in {client_id!r}'s manifest")

    proj_path = projection_paths[projection_id]
    includes = (parse_yaml(proj_path).get("includes") or {})
    inc_modules = [m for m in (includes.get("modules") or []) if isinstance(m, str)]
    patterns = [p for p in ((includes.get("entities") or []) + (includes.get("rules") or [])) if isinstance(p, str)]

    needed = {m for m in inc_modules if m in module_paths}
    if any(p.endswith(".*") for p in patterns):
        # A wildcard can span modules we cannot resolve statically; load the full
        # (still single-client) module set to keep the answer complete.
        needed = set(module_paths)
    elif patterns:
        # Resolve explicit entity/rule references by parsing ONLY the in-scope
        # (``needed``) modules. If every referenced id is defined there, the scope
        # is already complete and nothing else is read. If some referenced id is
        # owned by a module NOT in ``includes.modules``, we do NOT scan the other
        # modules to locate its owner — that would parse a module we then exclude,
        # breaking the "never parses an excluded module" guarantee (Codex Reviewer
        # A). Instead widen to the full single-client module set, so every module
        # we parse stays in scope (and nothing is both parsed and excluded).
        defined = _collect_ids([module_paths[m] for m in sorted(needed)])
        if any(p not in defined for p in patterns):
            needed = set(module_paths)

    ordered = [manifest_path, cdir / "client.yaml"]
    ordered += [module_paths[m] for m in sorted(needed)]
    ordered.append(proj_path)
    ordered = [p for p in ordered if p.is_file()]

    excluded_modules = sorted(mid for mid in module_paths if mid not in needed)
    # ``ordered`` paths are resolved (via ``_safe_join``/resolved ``cdir``); make
    # the repo-relative view robust to a caller passing an unresolved ``root``
    # (e.g. a symlinked temp dir on macOS) by resolving both sides.
    root_r = root.resolve()
    meta = {
        "client_id": client_id,
        "projection": projection_id,
        "needed_module_ids": sorted(needed),
        "excluded_module_ids": excluded_modules,
        "parsed_files": [str(p.resolve().relative_to(root_r)) for p in ordered],
    }
    return ordered, meta


def _build_scope_exports(root: Path, questions: list[dict[str, Any]], tmpdir: Path) -> dict[tuple, dict[str, Any]]:
    """Build one scoped SQLite export per distinct (client_id, projection)."""
    exports: dict[tuple, dict[str, Any]] = {}
    for q in questions:
        key = (q["client_id"], q["projection"])
        if key in exports:
            continue
        paths, meta = resolve_scope_paths(root, key[0], key[1])
        db = tmpdir / f"scope-{len(exports)}.sqlite"
        e.export(root, db, paths=paths)
        exports[key] = {"db": db, "meta": meta}
    return exports


def _evaluate_with_exports(
    exports: dict[tuple, dict[str, Any]],
    questions: list[dict[str, Any]],
    overrides: Optional[dict[tuple, Path]] = None,
) -> list[dict[str, Any]]:
    """Evaluate each question against its scope export (or a per-scope override db)."""
    overrides = overrides or {}
    results: list[dict[str, Any]] = []
    for q in questions:
        key = (q["client_id"], q["projection"])
        db = overrides.get(key) or exports[key]["db"]
        conn = sqlite3.connect(db)
        try:
            results.append(evaluate_question(conn, q))
        finally:
            conn.close()
    return sorted(results, key=lambda r: r["id"])


# --------------------------------------------------------------------------- #
# Drift-isolation regression (the controlled semantic-drift negative case)
# --------------------------------------------------------------------------- #
def _drift_scenarios() -> list[dict[str, Any]]:
    """Controlled single-point mutations, each expected to fail exactly one question.

    Each mutation is applied to a COPY of the temp export (never the YAML, never a
    committed artifact). Isolation means: the named question flips to fail and
    every other question stays pass — proving a competency assertion pinpoints the
    drift instead of failing everything (or nothing).
    """
    def flip_metric_status(conn: sqlite3.Connection) -> None:
        # Rule-status / planning-boundary drift: a draft metric is promoted to
        # active, which must trip the Femme metric question (row mismatch + the
        # forbid_status/require_field guards) and nothing else.
        conn.execute(
            "UPDATE entities SET status = 'active' WHERE entity_id = ?",
            ("femme-events.visibility.metric.gbp-calls",),
        )

    def drop_projection_entity(conn: sqlite3.Connection) -> None:
        # Projection-membership drift: an entity leaves the inventory-workflow
        # projection's includes, which must trip only the JMD resources question.
        row = conn.execute(
            "SELECT includes_json FROM projections WHERE projection_id = ?",
            ("jmd-menswear.inventory-workflow",),
        ).fetchone()
        includes = json.loads(row[0])
        includes["entities"] = [x for x in includes["entities"] if x != "jmd-menswear.inventory.sync-run"]
        conn.execute(
            "UPDATE projections SET includes_json = ? WHERE projection_id = ?",
            (json.dumps(includes), "jmd-menswear.inventory-workflow"),
        )

    def flip_relationship_confidence(conn: sqlite3.Connection) -> None:
        # Relationship-confidence drift: the owner-reviewed grounding edge is
        # demoted to draft, which must trip only the Femme relationship-backed
        # grounding question (row mismatch + the require_field_in confidence guard).
        conn.execute(
            "UPDATE relationships SET source_confidence = 'draft' WHERE relationship_id = ?",
            ("femme-events.visibility.gbp-uses-business-fact",),
        )

    def promote_path_edge_confidence(conn: sqlite3.Connection) -> None:
        # Path-edge confidence drift: a draft data-flow edge is promoted to
        # verified, which must trip only the JMD multi-hop pipeline question
        # (path confidences mismatch + the require_edge_confidence_in guard). This
        # is the status-awareness case: a draft plan cannot masquerade as verified
        # current architecture without a competency question flipping to fail.
        conn.execute(
            "UPDATE relationships SET source_confidence = 'verified' WHERE relationship_id = ?",
            ("jmd-menswear.inventory.image-creates-sanity-asset",),
        )

    return [
        {
            "name": "metric-status-drift",
            "expect_failed": "femme-events.competency.local-visibility-outcome-metrics",
            "mutate": flip_metric_status,
        },
        {
            "name": "projection-membership-drift",
            "expect_failed": "jmd-menswear.competency.inventory-workflow-resources",
            "mutate": drop_projection_entity,
        },
        {
            "name": "relationship-confidence-drift",
            "expect_failed": "femme-events.competency.gbp-grounded-in-owner-reviewed-fact",
            "mutate": flip_relationship_confidence,
        },
        {
            "name": "path-edge-confidence-drift",
            "expect_failed": "jmd-menswear.competency.inventory-image-pipeline-path",
            "mutate": promote_path_edge_confidence,
        },
    ]


def run_drift_regression(
    exports: dict[tuple, dict[str, Any]], questions: list[dict[str, Any]], tmpdir: Path
) -> dict[str, Any]:
    """Prove each controlled drift isolates to exactly its one competency question.

    The mutation is applied to a COPY of only the target question's scoped export;
    every other question is still evaluated against its own clean scope. Isolation
    therefore holds even when two questions share one scoped export (both Femme
    questions share the ``local-seo`` scope), proving a single-point drift
    pinpoints its question rather than failing everything (or nothing).
    """
    by_id = {q["id"]: q for q in questions}
    cases: list[dict[str, Any]] = []
    passed = True
    for i, scenario in enumerate(_drift_scenarios()):
        target = by_id[scenario["expect_failed"]]
        key = (target["client_id"], target["projection"])
        mutated = tmpdir / f"drift-{i}.sqlite"
        shutil.copyfile(exports[key]["db"], mutated)
        conn = sqlite3.connect(mutated)
        try:
            scenario["mutate"](conn)
            conn.commit()
        finally:
            conn.close()
        results = _evaluate_with_exports(exports, questions, overrides={key: mutated})
        failed_ids = sorted(r["id"] for r in results if r["status"] == "fail")
        expected_failed = [scenario["expect_failed"]]
        diagnostic = next((r["failures"] for r in results if r["id"] == scenario["expect_failed"]), [])
        isolated = failed_ids == expected_failed and bool(diagnostic)
        passed = passed and isolated
        cases.append(
            {
                "name": scenario["name"],
                "expected_failed": scenario["expect_failed"],
                "actual_failed": failed_ids,
                "isolated": isolated,
                "diagnostic_present": bool(diagnostic),
            }
        )
    return {"passed": passed, "cases": cases}


# --------------------------------------------------------------------------- #
# Loading-isolation regression (projection-directed loading instrumentation)
# --------------------------------------------------------------------------- #
def _record_parse_calls(fn):
    """Run ``fn()`` while recording every actual ``parse_yaml(path)`` call.

    The shared parser is bound by name in three places (``ontology_loader``, this
    runner, and ``export_sqlite``); wrap all three so we capture the REAL file
    opens made during scope resolution AND the scoped export — not just the paths
    a function returns. This is what lets the loading-isolation regression prove a
    module the projection excludes is never parsed, closing the gap Codex Reviewer
    A found (the prior probe trusted the returned path list and missed the
    excluded-module reads inside ``resolve_scope_paths``). Returns
    ``(result, recorded_paths)`` and always restores the originals.
    """
    import ontology_loader as _ol
    this_mod = sys.modules[__name__]
    targets = [t for t in (this_mod, e, _ol) if hasattr(t, "parse_yaml")]
    real = _ol.parse_yaml
    recorded: list[Path] = []

    def wrapper(path):
        recorded.append(Path(path))
        return real(path)

    saved = [(m, m.parse_yaml) for m in targets]
    for m in targets:
        m.parse_yaml = wrapper
    try:
        result = fn()
    finally:
        for m, orig in saved:
            m.parse_yaml = orig
    return result, recorded


def run_loading_isolation_probes(root: Path, questions: list[dict[str, Any]]) -> dict[str, Any]:
    """Instrument each question's ACTUAL parse calls and prove loading is scoped.

    For every question this resolves the scope AND builds the real scoped export
    while instrumenting every ``parse_yaml`` file open (via ``_record_parse_calls``),
    then asserts, at the true parse boundary, that (a) no file outside the named
    client's directory is opened — no other client is scanned — and (b) no module
    the projection excludes is opened, even transiently during reference
    resolution. Basing the assertion on observed parses (not the returned path
    list) is the direct refutation of the "parses 9 Femme + 9 JMD files" finding
    and of the resolver-read gap: a Femme question opens only Femme files, and a
    projection that excludes a module never reads it. Deterministic; the export is
    built in a throwaway temp dir (never the repo's build/).
    """
    client_dirs = sorted(p.name for p in (root / "clients").glob("*") if p.is_dir())
    cases: list[dict[str, Any]] = []
    passed = True
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        for i, q in enumerate(questions):
            def _resolve_and_export(q=q, i=i):
                paths, meta = resolve_scope_paths(root, q["client_id"], q["projection"])
                db = tmpdir / f"loadprobe-{i}.sqlite"
                e.export(root, db, paths=paths)
                return meta

            meta, recorded = _record_parse_calls(_resolve_and_export)
            allowed = set(meta["parsed_files"])
            # Actual observed parses, repo-relative. Anything not in the declared
            # scope file set is a real leak (foreign client OR excluded module).
            observed = sorted({str(p.resolve().relative_to(root.resolve())) for p in recorded})
            leaked_files = sorted(r for r in observed if r not in allowed)
            prefix = f"clients/{q['client_id']}/"
            foreign_files = sorted(r for r in observed if not r.startswith(prefix))
            foreign_clients = sorted(
                c for c in client_dirs
                if c != q["client_id"] and any(r.startswith(f"clients/{c}/") for r in observed)
            )
            excluded_leaked = sorted(
                mid for mid in meta["excluded_module_ids"]
                if any(r.endswith(f"{mid.split('.')[-1]}.yaml") and "/modules/" in r for r in leaked_files)
            )
            ok = not leaked_files and not foreign_files and not foreign_clients and not excluded_leaked
            passed = passed and ok
            cases.append(
                {
                    "id": q["id"],
                    "client_id": q["client_id"],
                    "projection": q["projection"],
                    "parsed_file_count": len(observed),
                    "declared_scope_files": meta["parsed_files"],
                    "observed_parsed_files": observed,
                    "needed_module_ids": meta["needed_module_ids"],
                    "excluded_module_ids": meta["excluded_module_ids"],
                    "leaked_files": leaked_files,
                    "foreign_files": foreign_files,
                    "foreign_clients_touched": foreign_clients,
                    "excluded_modules_leaked": excluded_leaked,
                    "ok": ok,
                }
            )
    return {"passed": passed, "cases": cases}


# --------------------------------------------------------------------------- #
# Resolver-read isolation regression (synthetic; the excluded-module scan case)
# --------------------------------------------------------------------------- #
_RESOLVER_FIXTURE = {
    "clients/acme/ontology.yaml": (
        'schema_version: "0.1"\nkind: ontology\nid: acme.ontology\nclient_id: acme\n'
        "status: active\nmodules:\n"
        "  - {path: modules/brand.yaml, id: acme.brand}\n"
        "  - {path: modules/operations.yaml, id: acme.operations}\n"
        "  - {path: modules/inventory.yaml, id: acme.inventory}\n"
        "projections:\n"
        "  - {path: projections/tight.yaml, id: acme.tight}\n"
        "  - {path: projections/widen.yaml, id: acme.widen}\n"
    ),
    "clients/acme/client.yaml": (
        'schema_version: "0.1"\nkind: client\nid: acme\nname: Acme\nstatus: active\n'
    ),
    "clients/acme/modules/brand.yaml": (
        'schema_version: "0.1"\nkind: ontology_module\nid: acme.brand\nclient_id: acme\n'
        "entities: [{id: acme.brand.voice, label: v, entity_type: brand_object}]\n"
    ),
    "clients/acme/modules/operations.yaml": (
        'schema_version: "0.1"\nkind: ontology_module\nid: acme.operations\nclient_id: acme\n'
        "entities: [{id: acme.operations.boundary, label: b, entity_type: governance_object}]\n"
    ),
    "clients/acme/modules/inventory.yaml": (
        'schema_version: "0.1"\nkind: ontology_module\nid: acme.inventory\nclient_id: acme\n'
        "entities: [{id: acme.inventory.image, label: i, entity_type: system_resource}]\n"
    ),
    # tight: every reference resolves inside includes.modules → excluded modules
    # (brand, inventory) stay out of scope AND must never be parsed.
    "clients/acme/projections/tight.yaml": (
        'schema_version: "0.1"\nkind: projection\nid: acme.tight\nclient_id: acme\n'
        "status: active\nincludes:\n  modules: [acme.operations]\n"
        "  entities: [acme.operations.boundary]\n"
    ),
    # widen: references an entity owned by a module NOT in includes.modules →
    # the resolver widens to the full single-client set instead of scanning and
    # excluding (which would parse an excluded file).
    "clients/acme/projections/widen.yaml": (
        'schema_version: "0.1"\nkind: projection\nid: acme.widen\nclient_id: acme\n'
        "status: active\nincludes:\n  modules: [acme.operations]\n"
        "  entities: [acme.inventory.image]\n"
    ),
}


def run_resolver_read_isolation_probe(tmpdir: Path) -> dict[str, Any]:
    """Prove ``resolve_scope_paths`` never PARSES a module the projection excludes.

    The four live questions all reference ids owned by modules already in
    ``includes.modules``, so the resolver never has to look elsewhere for them.
    This synthetic single-client fixture exercises the two remaining resolver
    paths under ACTUAL ``parse_yaml`` instrumentation — the gap Codex Reviewer A
    found, where the prior loading probe trusted the returned path list and missed
    excluded-module reads during resolution:

      * ``tight`` — a projection whose references all resolve inside
        ``includes.modules``; the two excluded modules (brand, inventory) must be
        neither in scope nor parsed during resolution.
      * ``widen`` — a projection referencing an entity owned by a module NOT in
        ``includes.modules``; the resolver widens to the full single-client set
        (so nothing is both parsed and excluded) and pulls the referenced module
        into scope, keeping the answer complete.
    """
    root = tmpdir / "resolver-fixture"
    for rel, text in _RESOLVER_FIXTURE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    expectations = [
        # name, projection id, modules that must be excluded, module that must be in scope
        ("tight", "acme.tight", ["acme.brand", "acme.inventory"], "acme.operations"),
        ("widen", "acme.widen", [], "acme.inventory"),
    ]
    cases: list[dict[str, Any]] = []
    passed = True
    for name, pid, expect_excluded, must_be_in_scope in expectations:
        def _resolve(pid=pid):
            paths, meta = resolve_scope_paths(root, "acme", pid)
            return meta

        meta, recorded = _record_parse_calls(_resolve)
        observed = sorted({str(p.resolve().relative_to(root.resolve())) for p in recorded})
        excluded_parsed = sorted(
            mid for mid in meta["excluded_module_ids"]
            if any(r.endswith(f"modules/{mid.split('.')[-1]}.yaml") for r in observed)
        )
        excluded_ok = meta["excluded_module_ids"] == expect_excluded
        scope_ok = must_be_in_scope in meta["needed_module_ids"]
        no_excluded_parse = not excluded_parsed
        ok = excluded_ok and scope_ok and no_excluded_parse
        passed = passed and ok
        cases.append(
            {
                "name": name,
                "projection": pid,
                "needed_module_ids": meta["needed_module_ids"],
                "excluded_module_ids": meta["excluded_module_ids"],
                "observed_parsed_files": observed,
                "excluded_modules_parsed": excluded_parsed,
                "expected_excluded": expect_excluded,
                "module_expected_in_scope": must_be_in_scope,
                "ok": ok,
            }
        )
    return {"passed": passed, "cases": cases}


# --------------------------------------------------------------------------- #
# Relationship / path scope-isolation regression (issue #41; the endpoint- and
# traversal-leakage negative case, evaluated on a full export so the assertion
# targets RESULT-level projection scoping — the complement of the parse-level
# loading-isolation probe above)
# --------------------------------------------------------------------------- #
_SCOPE_FIXTURE = {
    "clients/acme/ontology.yaml": (
        'schema_version: "0.1"\nkind: ontology\nid: acme.ontology\nclient_id: acme\n'
        "status: active\nmodules:\n"
        "  - {path: modules/flow.yaml, id: acme.flow}\n"
        "  - {path: modules/hidden.yaml, id: acme.hidden}\n"
        "projections:\n"
        "  - {path: projections/p.yaml, id: acme.p}\n"
    ),
    "clients/acme/client.yaml": (
        'schema_version: "0.1"\nkind: client\nid: acme\nname: Acme\nstatus: active\n'
    ),
    # flow: the in-scope module. a→b→c is a two-hop contains/renders_in chain;
    # a→d is a 'uses' edge (excluded by predicate); c→z points at an entity that
    # lives in the EXCLUDED hidden module (out-of-scope endpoint).
    "clients/acme/modules/flow.yaml": (
        'schema_version: "0.1"\nkind: ontology_module\nid: acme.flow\nclient_id: acme\n'
        "title: Flow\nstatus: active\n"
        "entities:\n"
        "  - {id: acme.flow.a, label: a, entity_type: system_resource}\n"
        "  - {id: acme.flow.b, label: b, entity_type: media_asset}\n"
        "  - {id: acme.flow.c, label: c, entity_type: content_record}\n"
        "  - {id: acme.flow.d, label: d, entity_type: business_object}\n"
        "relationships:\n"
        "  - {id: acme.flow.a-contains-b, subject: acme.flow.a, predicate: contains, object: acme.flow.b, source_confidence: draft}\n"
        "  - {id: acme.flow.b-renders-c, subject: acme.flow.b, predicate: renders_in, object: acme.flow.c, source_confidence: draft}\n"
        "  - {id: acme.flow.a-uses-d, subject: acme.flow.a, predicate: uses, object: acme.flow.d, source_confidence: draft}\n"
        "  - {id: acme.flow.c-contains-z, subject: acme.flow.c, predicate: contains, object: acme.hidden.z, source_confidence: draft}\n"
    ),
    # hidden: excluded from projection p. Its entity z must never appear as a
    # relationship endpoint or a traversed path node.
    "clients/acme/modules/hidden.yaml": (
        'schema_version: "0.1"\nkind: ontology_module\nid: acme.hidden\nclient_id: acme\n'
        "title: Hidden\nstatus: active\n"
        "entities:\n"
        "  - {id: acme.hidden.z, label: z, entity_type: content_record}\n"
    ),
    "clients/acme/projections/p.yaml": (
        'schema_version: "0.1"\nkind: projection\nid: acme.p\nclient_id: acme\n'
        "status: active\nincludes:\n  modules: [acme.flow]\n"
    ),
}


def _scope_question(op: str, **query: Any) -> dict[str, Any]:
    """A minimal in-memory question for driving ``run_query`` in the scope probe."""
    return {"id": f"probe.{op}", "client_id": "acme", "projection": "acme.p", "query": {"op": op, **query}}


def run_query_scope_probes(tmpdir: Path) -> dict[str, Any]:
    """Prove relationship endpoints and path traversal stay within projection scope.

    Builds a synthetic single-client fixture with an in-scope ``flow`` module and
    an EXCLUDED ``hidden`` module, exports the FULL database (so nothing is
    pre-filtered at the parse boundary), then drives ``run_query`` through the
    ``acme.p`` projection (includes only ``flow``) and asserts, at the result
    boundary:

      * a relationship whose object lives in the excluded module
        (``flow.c → hidden.z``) is DROPPED — endpoint isolation, not just module
        membership;
      * a ``uses`` edge is excluded when the query's predicate allow-list omits it;
      * a bounded path never traverses into the out-of-scope node ``z`` and never
        follows a disallowed predicate, so exactly the in-scope chain is returned;
      * hop bounds are honored (a max_hops=1 search finds no 2-hop terminal).
    """
    root = tmpdir / "scope-fixture"
    for rel, text in _SCOPE_FIXTURE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    db = tmpdir / "scope-fixture.sqlite"
    e.export(root, db)  # full export — no pre-scoping at the parse layer

    conn = sqlite3.connect(db)
    cases: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any) -> None:
        cases.append({"name": name, "ok": ok, "detail": detail})

    try:
        # 1. Relationship endpoint isolation: c→z (object in excluded module) is
        #    dropped; only the two in-scope contains/renders_in edges remain.
        rels = run_query(conn, _scope_question(
            "relationships", filters={"predicate": ["contains", "renders_in"]},
            select=["subject", "predicate", "object", "source_confidence"],
        ))
        objs = {r["object"] for r in rels}
        ok = (
            {r["subject"] + "->" + r["object"] for r in rels}
            == {"acme.flow.a->acme.flow.b", "acme.flow.b->acme.flow.c"}
            and "acme.hidden.z" not in objs
        )
        record("relationship-endpoint-isolation", ok, rels)

        # 2. Predicate filter drops the 'uses' edge entirely.
        used = run_query(conn, _scope_question(
            "relationships", filters={"predicate": "uses"},
            select=["subject", "predicate", "object", "source_confidence"],
        ))
        record("relationship-predicate-filter", used == [
            {"subject": "acme.flow.a", "predicate": "uses", "object": "acme.flow.d", "source_confidence": "draft"}
        ], used)

        # 3. Path traversal stays in scope: exactly a→b→c, never reaching z, and
        #    never following the 'uses' edge.
        paths = run_query(conn, _scope_question(
            "path", start={"id": "acme.flow.a"}, end={"entity_type": "content_record"},
            predicates=["contains", "renders_in"], min_hops=1, max_hops=4,
        ))
        all_nodes = {tok for p in paths for i, tok in enumerate(p["chain"]) if i % 2 == 0}
        ok_path = (
            paths == [{"chain": ["acme.flow.a", "contains", "acme.flow.b", "renders_in", "acme.flow.c"],
                       "confidences": ["draft", "draft"]}]
            and "acme.hidden.z" not in all_nodes
            and "acme.flow.d" not in all_nodes
        )
        record("path-traversal-isolation", ok_path, paths)

        # 4. Hop bound: a max_hops=1 search finds no content_record terminal (c is
        #    two hops away), so the bounded traversal returns nothing.
        short = run_query(conn, _scope_question(
            "path", start={"id": "acme.flow.a"}, end={"entity_type": "content_record"},
            predicates=["contains", "renders_in"], min_hops=1, max_hops=1,
        ))
        record("path-hop-bound", short == [], short)
    finally:
        conn.close()

    passed = all(c["ok"] for c in cases)
    return {"passed": passed, "cases": cases}


# --------------------------------------------------------------------------- #
# Path-shape regression (issue #41; parallel edges, cycles, branching, order)
# --------------------------------------------------------------------------- #
_PATH_SHAPE_FIXTURE = {
    "clients/acme/ontology.yaml": (
        'schema_version: "0.1"\nkind: ontology\nid: acme.ontology\nclient_id: acme\n'
        "status: active\nmodules:\n"
        "  - {path: modules/graph.yaml, id: acme.graph}\n"
        "projections:\n"
        "  - {path: projections/p.yaml, id: acme.p}\n"
    ),
    "clients/acme/client.yaml": (
        'schema_version: "0.1"\nkind: client\nid: acme\nname: Acme\nstatus: active\n'
    ),
    # graph: a contains b via TWO parallel edges (same endpoints/predicate/
    # confidence), a also contains c (branching), b contains a (a back-edge that
    # must never re-enter a on a simple path), and c contains d (depth).
    "clients/acme/modules/graph.yaml": (
        'schema_version: "0.1"\nkind: ontology_module\nid: acme.graph\nclient_id: acme\n'
        "title: Graph\nstatus: active\n"
        "entities:\n"
        "  - {id: acme.g.a, label: a, entity_type: system_resource}\n"
        "  - {id: acme.g.b, label: b, entity_type: content_record}\n"
        "  - {id: acme.g.c, label: c, entity_type: content_record}\n"
        "  - {id: acme.g.d, label: d, entity_type: content_record}\n"
        "relationships:\n"
        "  - {id: acme.g.a-contains-b-1, subject: acme.g.a, predicate: contains, object: acme.g.b, source_confidence: draft}\n"
        "  - {id: acme.g.a-contains-b-2, subject: acme.g.a, predicate: contains, object: acme.g.b, source_confidence: draft}\n"
        "  - {id: acme.g.a-contains-c, subject: acme.g.a, predicate: contains, object: acme.g.c, source_confidence: draft}\n"
        "  - {id: acme.g.b-contains-a, subject: acme.g.b, predicate: contains, object: acme.g.a, source_confidence: draft}\n"
        "  - {id: acme.g.c-contains-d, subject: acme.g.c, predicate: contains, object: acme.g.d, source_confidence: draft}\n"
    ),
    "clients/acme/projections/p.yaml": (
        'schema_version: "0.1"\nkind: projection\nid: acme.p\nclient_id: acme\n'
        "status: active\nincludes:\n  modules: [acme.graph]\n"
    ),
}


def run_path_shape_probes(tmpdir: Path) -> dict[str, Any]:
    """Prove parallel edges, cycles, branching, and ordering behave deterministically.

    Directly answers Codex Reviewer A's parallel-edge finding: because the public
    path representation omits relationship IDs, two parallel edges must collapse to
    ONE path, not two indistinguishable duplicates. Also proves a back-edge cannot
    make a simple-path traversal revisit a node (cycle safety / termination),
    branching yields distinct paths, and repeated runs return byte-identical order.
    """
    root = tmpdir / "path-shape-fixture"
    for rel, text in _PATH_SHAPE_FIXTURE.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    db = tmpdir / "path-shape-fixture.sqlite"
    e.export(root, db)

    conn = sqlite3.connect(db)
    cases: list[dict[str, Any]] = []

    def record(name: str, ok: bool, detail: Any) -> None:
        cases.append({"name": name, "ok": ok, "detail": detail})

    def pathq(**q: Any) -> dict[str, Any]:
        return _scope_question("path", **q)

    try:
        # 1. Parallel edges (a→b twice) collapse to exactly one path.
        parallel = run_query(conn, pathq(
            start={"id": "acme.g.a"}, end={"id": "acme.g.b"},
            predicates=["contains"], min_hops=1, max_hops=1,
        ))
        record("parallel-edge-dedup", parallel == [
            {"chain": ["acme.g.a", "contains", "acme.g.b"], "confidences": ["draft"]}
        ], parallel)

        # 2. Branching from a yields two DISTINCT one-hop paths (to b and to c),
        #    the deduped a→b among them (not three).
        branch = run_query(conn, pathq(
            start={"id": "acme.g.a"}, end={"entity_type": "content_record"},
            predicates=["contains"], min_hops=1, max_hops=1,
        ))
        ends = sorted(p["chain"][-1] for p in branch)
        record("branching-distinct-paths", len(branch) == 2 and ends == ["acme.g.b", "acme.g.c"], branch)

        # 3. Cycle safety: the b→a back-edge must never re-enter a; every returned
        #    chain is a simple path (no repeated node) and traversal terminates.
        deep = run_query(conn, pathq(
            start={"id": "acme.g.a"}, end={"entity_type": "content_record"},
            predicates=["contains"], min_hops=1, max_hops=3,
        ))
        no_repeat = all(len(n) == len(set(n)) for p in deep for n in [p["chain"][0::2]])
        has_depth = any(
            p["chain"] == ["acme.g.a", "contains", "acme.g.c", "contains", "acme.g.d"] for p in deep
        )
        record("cycle-simple-path-bounded", no_repeat and has_depth, deep)

        # 4. Stable ordering: two identical runs return byte-identical results.
        again = run_query(conn, pathq(
            start={"id": "acme.g.a"}, end={"entity_type": "content_record"},
            predicates=["contains"], min_hops=1, max_hops=3,
        ))
        record("stable-ordering", again == deep, again)
    finally:
        conn.close()

    passed = all(c["ok"] for c in cases)
    return {"passed": passed, "cases": cases}


# --------------------------------------------------------------------------- #
# Registry shape-validation regression (the malformed-registry negative case)
# --------------------------------------------------------------------------- #
def _negative_probe_docs() -> list[tuple[str, dict[str, Any], Optional[str]]]:
    """Malformed (and one valid) registry documents for the shape-validator.

    Each tuple is ``(name, doc, expected_substring)``. A malformed doc names the
    substring its QuestionError must contain; the lone valid control uses
    ``None`` (must NOT raise). These lock the exact false-passes the reviewers
    reproduced: a non-string id/client_id/projection, a missing or non-string
    human-readable question/rationale, a misspelled (`gaurds`) or otherwise
    unknown question-level key, a non-boolean required, a non-mapping query, an
    unknown select column, a misspelled guard operand, an unknown filter column,
    duplicate output keys, a wrong-typed expect payload, a missing guard operand,
    an expected-row key typo, a projection_resources question missing its
    resources, a status/field/id guard not bound to a selected output key
    (silent no-op), a non-scalar filter operand, and a row-field guard on a
    projection_resources answer.
    """
    _DROP = object()  # sentinel: a probe passes _DROP to remove a base field

    def q(**over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": "probe.q",
            "client_id": "c",
            "projection": "p",
            "question": "Does the probe stay well-formed?",
            "rationale": "Locks the control case as valid.",
            "query": {"op": "entities", "filters": {"entity_type": "metric"}, "select": ["entity_id", "status"]},
            "expect": {"rows": [{"entity_id": "c.x", "status": "draft"}]},
        }
        base.update(over)
        # Allow a probe to DELETE a base field (e.g. drop `question`) by passing
        # the ``_DROP`` sentinel as its value.
        for key in [k for k, v in base.items() if v is _DROP]:
            del base[key]
        return base

    def doc(question: dict[str, Any]) -> dict[str, Any]:
        return {"questions": [question]}

    return [
        # Valid control — the shape validator must accept a well-formed question.
        ("valid-control", doc(q(guards=[{"type": "forbid_id_prefix", "prefixes": ["other"]}])), None),
        # Integration Auditor exact repro: a mapping-valued `id` must fail as a
        # QuestionError BEFORE it is hashed into `seen` (previously an unhashable
        # dict → raw TypeError / exit 1).
        ("id-not-a-string", doc(q(id={"oops": 1})), "non-empty string 'id'"),
        # Integration Auditor exact repro: a mapping-/list-valued `client_id` must
        # fail before path resolution (previously `root / client_id` → TypeError).
        ("client-id-not-a-string", doc(q(client_id=["c"])), "non-empty string 'client_id'"),
        # A non-string `projection` must fail before scope resolution.
        ("projection-not-a-string", doc(q(projection=3)), "non-empty string 'projection'"),
        # Reviewer A / B: issue #31 requires a human-readable `question` and
        # `rationale`. A missing or non-string value must fail closed as a usage
        # error, not slip through unvalidated.
        ("question-missing", doc(q(question=_DROP)), "non-empty string 'question'"),
        ("question-not-a-string", doc(q(question={"oops": 1})), "non-empty string 'question'"),
        ("rationale-missing", doc(q(rationale=_DROP)), "non-empty string 'rationale'"),
        ("rationale-not-a-string", doc(q(rationale=7)), "non-empty string 'rationale'"),
        # Reviewer B exact repro: a misspelled safety key `gaurds:` must be
        # rejected as an unknown question-level key rather than silently ignored
        # (which would drop every intended guard while the question reports PASS).
        ("misspelled-gaurds-key",
         doc(q(gaurds=[{"type": "forbid_id_prefix", "prefixes": ["other"]}])),
         "unknown question-level key"),
        # Any arbitrary unknown envelope key must also fail closed.
        ("arbitrary-unknown-question-key", doc(q(nope="x")), "unknown question-level key"),
        # Codex Reviewer A exact repro: a non-boolean `required` (e.g. `0`) could
        # make a FAILING required question exit 0; reject it up front.
        ("required-not-a-boolean", doc(q(required=0)), "'required' must be a boolean"),
        # Reviewer A / Auditor follow-up: a non-mapping query (`query: nope`) must
        # be a QuestionError, not an AttributeError traceback.
        ("query-not-a-mapping", doc(q(query="nope")), "'query' must be a mapping"),
        # Reviewer A: a misspelled select token (`statsu`) must not resolve to None.
        ("unknown-select-column", doc(q(query={"op": "entities", "select": ["entity_id", "statsu"]})), "statsu"),
        # Reviewer A exact repro: `prefix` instead of `prefixes` must be rejected.
        ("misspelled-guard-operand", doc(q(guards=[{"type": "forbid_id_prefix", "prefix": ["c"]}])), "unknown operand"),
        # A filter on a non-column must fail loudly, not silently drop the filter.
        ("unknown-filter-column", doc(q(query={"op": "entities", "filters": {"entity_typ": "metric"}, "select": ["entity_id"]})), "filter column"),
        # Two tokens collapsing to the same output key is ambiguous → reject.
        ("duplicate-output-key", doc(q(query={"op": "rules", "select": ["status", "fields.status"]}, expect={"rows": []})), "duplicate output key"),
        # expect.rows must be a list, not a mapping.
        ("expect-rows-wrong-type", doc(q(expect={"rows": {}})), "expect.rows as a list"),
        # A guard missing its required operand is a no-op false-pass → reject.
        ("guard-missing-operand", doc(q(guards=[{"type": "require_status"}])), "missing required operand"),
        # Reviewer A: an expected-row typo matching a real column must be caught
        # even when the select tokens are valid.
        ("expect-row-key-typo", doc(q(expect={"rows": [{"entity_id": "c.x", "statsu": "draft"}]})), "do not match select output keys"),
        # projection_resources must define expect.resources (a missing/empty expect
        # has no resources mapping to compare against).
        ("projection-resources-missing", doc(q(query={"op": "projection_resources"}, expect={})), "expect.resources"),
        # Reviewer A / Auditor: a status guard when 'status' is not selected is a
        # silent no-op at evaluation → reject before evaluation.
        ("forbid-status-not-selected",
         doc(q(query={"op": "entities", "select": ["entity_id"]},
               expect={"rows": [{"entity_id": "c.x"}]},
               guards=[{"type": "forbid_status", "statuses": ["active"]}])),
         "requires 'status'"),
        # Reviewer A: forbid_id_prefix when neither entity_id nor rule_id is
        # selected can never see an id → reject.
        ("forbid-id-prefix-no-id-selected",
         doc(q(query={"op": "entities", "select": ["status"]},
               expect={"rows": [{"status": "draft"}]},
               guards=[{"type": "forbid_id_prefix", "prefixes": ["other"]}])),
         "requires the id column"),
        # Reviewer A / Auditor exact repro: require_field_equals on a field that is
        # not selected (misspelled 'statsu', value: null) is a dict.get()-None
        # no-op → reject.
        ("require-field-not-selected",
         doc(q(query={"op": "entities", "select": ["entity_id", "status"]},
               expect={"rows": [{"entity_id": "c.x", "status": "draft"}]},
               guards=[{"type": "require_field_equals", "field": "statsu", "value": None}])),
         "requires its field 'statsu'"),
        # Auditor exact repro: a non-scalar filter operand (`status: {typo: draft}`)
        # can never match a column value and would silently drop the filter → reject.
        ("filter-operand-not-scalar",
         doc(q(query={"op": "entities", "filters": {"status": {"typo": "draft"}}, "select": ["entity_id", "status"]},
               expect={"rows": [{"entity_id": "c.x", "status": "draft"}]})),
         "must be a scalar"),
        # A row-field guard on a projection_resources answer has no row columns to
        # read → reject rather than silently pass.
        ("guard-not-applicable-to-resources",
         doc(q(query={"op": "projection_resources"}, expect={"resources": {}},
               guards=[{"type": "require_status", "statuses": ["active"]}])),
         "does not apply to a 'projection_resources'"),
        # ---- issue #41: relationships op shape --------------------------------
        # Valid relationships control — a well-formed subject/predicate/object
        # query with an endpoint-bound isolation guard must be accepted.
        ("valid-relationships-control",
         doc(q(query={"op": "relationships", "filters": {"predicate": "uses"},
                      "select": ["subject", "predicate", "object", "source_confidence"]},
               expect={"rows": [{"subject": "c.a", "predicate": "uses", "object": "c.b", "source_confidence": "verified"}]},
               guards=[{"type": "forbid_id_prefix", "prefixes": ["other"]},
                       {"type": "require_field_in", "field": "source_confidence", "values": ["verified", "owner_reviewed"]}])),
         None),
        # A select token that is not a real relationships column must be rejected.
        ("relationships-unknown-select-column",
         doc(q(query={"op": "relationships", "select": ["subject", "bogus"]},
               expect={"rows": []})),
         "bogus"),
        # forbid_id_prefix on relationships needs BOTH endpoints selected — the id
        # column alone would miss a foreign/out-of-scope endpoint.
        ("relationships-id-guard-without-endpoints",
         doc(q(query={"op": "relationships", "select": ["subject", "predicate"]},
               expect={"rows": [{"subject": "c.a", "predicate": "uses"}]},
               guards=[{"type": "forbid_id_prefix", "prefixes": ["other"]}])),
         "endpoint columns"),
        # A per-row status guard has no 'status' column on a relationships answer.
        ("status-guard-on-relationships",
         doc(q(query={"op": "relationships", "select": ["subject", "object", "source_confidence"]},
               expect={"rows": [{"subject": "c.a", "object": "c.b", "source_confidence": "draft"}]},
               guards=[{"type": "require_status", "statuses": ["active"]}])),
         "does not apply to a 'relationships'"),
        # require_field_in whose field is not selected is a silent no-op → reject.
        ("require-field-in-not-selected",
         doc(q(query={"op": "relationships", "select": ["subject", "object"]},
               expect={"rows": [{"subject": "c.a", "object": "c.b"}]},
               guards=[{"type": "require_field_in", "field": "source_confidence", "values": ["verified"]}])),
         "requires its field 'source_confidence'"),
        # ---- issue #41: bounded path op shape ---------------------------------
        # Valid path control — explicit start/end/predicates/hops + an edge
        # confidence guard must be accepted.
        ("valid-path-control",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"entity_type": "content_record"},
                      "predicates": ["contains", "renders_in"], "min_hops": 1, "max_hops": 3},
               expect={"paths": [{"chain": ["c.a", "contains", "c.b"], "confidences": ["draft"]}]},
               guards=[{"type": "require_edge_confidence_in", "values": ["draft"]},
                       {"type": "forbid_id_prefix", "prefixes": ["other"]}])),
         None),
        # An unknown path key must fail closed, not be silently ignored.
        ("path-unknown-key",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2, "hops": 3},
               expect={"paths": []})),
         "unknown key(s)"),
        # A missing start constraint is a usage error.
        ("path-missing-start",
         doc(q(query={"op": "path", "end": {"id": "c.b"}, "predicates": ["contains"],
                      "min_hops": 1, "max_hops": 2},
               expect={"paths": []})),
         "missing required key 'start'"),
        # A missing predicates list is a usage error.
        ("path-missing-predicates",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "min_hops": 1, "max_hops": 2},
               expect={"paths": []})),
         "missing required key 'predicates'"),
        # An empty start constraint names no node → reject.
        ("path-empty-start",
         doc(q(query={"op": "path", "start": {}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": []})),
         "non-empty mapping"),
        # An unknown start constraint key → reject.
        ("path-unknown-constraint-key",
         doc(q(query={"op": "path", "start": {"typ": "x"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": []})),
         "unknown constraint key"),
        # predicates must be a non-empty list of strings, not a bare string.
        ("path-predicates-not-list",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": "contains", "min_hops": 1, "max_hops": 2},
               expect={"paths": []})),
         "must be a non-empty list"),
        # min_hops must not exceed max_hops.
        ("path-min-gt-max",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 3, "max_hops": 2},
               expect={"paths": []})),
         "must be >= 'min_hops'"),
        # max_hops must stay within the bounded cap — traversal is deliberately bounded.
        ("path-max-over-cap",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 99},
               expect={"paths": []})),
         "exceeds the bounded cap"),
        # A boolean min_hops must not pose as an integer 1.
        ("path-hops-not-int",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": True, "max_hops": 2},
               expect={"paths": []})),
         "must be an integer"),
        # An edge-confidence guard reads path edges and never applies to a row op.
        ("edge-confidence-guard-on-entities",
         doc(q(guards=[{"type": "require_edge_confidence_in", "values": ["draft"]}])),
         "applies only to a path query"),
        # require_edge_confidence_in missing its values operand → reject.
        ("edge-confidence-guard-missing-values",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": []},
               guards=[{"type": "require_edge_confidence_in"}])),
         "missing required operand"),
        # expect.paths must be a list.
        ("path-expect-not-list",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": {}})),
         "expect.paths as a list"),
        # An even-length chain is not a valid (node,predicate,...,node) alternation.
        ("path-expect-chain-even",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": [{"chain": ["c.a", "contains"], "confidences": ["draft"]}]})),
         "odd-length"),
        # ---- issue #41 fix cycle 1: controlled-vocabulary fail-closed -----------
        # Integration Auditor / Codex Reviewer A+B EXACT repro: a path over a
        # MISSPELLED predicate with expect.paths:[] and a universal edge-confidence
        # guard used to validate, evaluate to [], and report PASS (the guard was
        # vacuous over the empty answer). It must now be rejected at validation.
        ("path-predicate-typo-with-vacuous-guard",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"entity_type": "content_record"},
                      "predicates": ["creates_or_updtaes"], "min_hops": 1, "max_hops": 3},
               expect={"paths": []},
               guards=[{"type": "require_edge_confidence_in", "values": ["draft"]},
                       {"type": "forbid_id_prefix", "prefixes": ["other"]}])),
         "controlled predicate vocabulary"),
        # Codex Reviewer B EXACT repro: a relationship filter on a misspelled
        # predicate (`ues`) with expect.rows:[] must fail closed, not match nothing.
        ("relationships-predicate-filter-typo",
         doc(q(query={"op": "relationships", "filters": {"predicate": "ues"},
                      "select": ["subject", "predicate", "object", "source_confidence"]},
               expect={"rows": []})),
         "controlled predicate vocabulary"),
        # The bounded `x_` experimental predicate escape hatch (schema anyOf) is
        # still accepted, so a deliberate local extension is not a typo.
        ("relationships-predicate-x-extension-ok",
         doc(q(query={"op": "relationships", "filters": {"predicate": "x_experimental"},
                      "select": ["subject", "predicate", "object"]},
               expect={"rows": []})),
         None),
        # A misspelled entity_type filter must be rejected against the schema vocab.
        ("entities-entity-type-filter-typo",
         doc(q(query={"op": "entities", "filters": {"entity_type": "metrik"}, "select": ["entity_id", "status"]},
               expect={"rows": [{"entity_id": "c.x", "status": "draft"}]})),
         "controlled entity_type vocabulary"),
        # A misspelled source_confidence filter must be rejected against the vocab.
        ("relationships-confidence-filter-typo",
         doc(q(query={"op": "relationships", "filters": {"source_confidence": "verifed"},
                      "select": ["subject", "object", "source_confidence"]},
               expect={"rows": []})),
         "controlled confidence vocabulary"),
        # A misspelled entity_type in a path node constraint must be rejected.
        ("path-start-entity-type-typo",
         doc(q(query={"op": "path", "start": {"entity_type": "systm_resource"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": []})),
         "controlled entity_type vocabulary"),
        # A misspelled edge-confidence guard operand must be rejected (else vacuous).
        ("edge-confidence-guard-value-typo",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": []},
               guards=[{"type": "require_edge_confidence_in", "values": ["draaft"]}])),
         "controlled confidence vocabulary"),
        # A misspelled require_status operand must be rejected against the vocab.
        ("require-status-value-typo",
         doc(q(query={"op": "entities", "select": ["entity_id", "status"]},
               expect={"rows": [{"entity_id": "c.x", "status": "draft"}]},
               guards=[{"type": "require_status", "statuses": ["activ"]}])),
         "controlled status vocabulary"),
        # Codex Reviewer A EXACT repro: a stray `expect.pathz` twin key must be
        # rejected — the CLOSED expect envelope prevents the real `paths` from
        # silently staying empty while the misspelled twin is ignored.
        ("expect-envelope-stray-key-path",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 2},
               expect={"paths": [], "pathz": [{"chain": ["c.a", "contains", "c.b"], "confidences": ["draft"]}]})),
         "unknown key(s)"),
        ("expect-envelope-stray-key-rows",
         doc(q(expect={"rows": [{"entity_id": "c.x", "status": "draft"}], "rowz": []})),
         "unknown key(s)"),
        # Codex Reviewer A EXACT repro class: an expected path CONTRADICTING the
        # query it claims to answer must be rejected — a disallowed predicate, an
        # out-of-bounds hop count, a mismatched endpoint, or a bad confidence token.
        ("expect-chain-predicate-not-allowed",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.c"},
                      "predicates": ["contains"], "min_hops": 2, "max_hops": 2},
               expect={"paths": [{"chain": ["c.a", "renders_in", "c.b", "contains", "c.c"], "confidences": ["draft", "draft"]}]})),
         "not in the query's allowed predicates"),
        ("expect-chain-hops-out-of-bounds",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 2, "max_hops": 2},
               expect={"paths": [{"chain": ["c.a", "contains", "c.b"], "confidences": ["draft"]}]})),
         "outside the query bounds"),
        ("expect-chain-start-endpoint-mismatch",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 1},
               expect={"paths": [{"chain": ["c.z", "contains", "c.b"], "confidences": ["draft"]}]})),
         "start expected endpoint"),
        ("expect-chain-confidence-typo",
         doc(q(query={"op": "path", "start": {"id": "c.a"}, "end": {"id": "c.b"},
                      "predicates": ["contains"], "min_hops": 1, "max_hops": 1},
               expect={"paths": [{"chain": ["c.a", "contains", "c.b"], "confidences": ["draaft"]}]})),
         "controlled confidence vocabulary"),
        # An expected relationship row asserting a typo'd predicate must be rejected.
        ("expect-row-predicate-typo",
         doc(q(query={"op": "relationships", "filters": {"predicate": "uses"},
                      "select": ["subject", "predicate", "object", "source_confidence"]},
               expect={"rows": [{"subject": "c.a", "predicate": "uzes", "object": "c.b", "source_confidence": "verified"}]})),
         "controlled predicate vocabulary"),
    ]


def run_registry_negative_probes() -> dict[str, Any]:
    """Prove the shape validator rejects every reproduced false-pass registry.

    Deterministic, in-memory, no export needed. Passes iff each malformed probe
    raises QuestionError with its expected diagnostic and the valid control is
    accepted.
    """
    cases: list[dict[str, Any]] = []
    passed = True
    for name, document, expected_sub in _negative_probe_docs():
        raised: Optional[str] = None
        try:
            validate_questions(document, "<probe>")
        except QuestionError as exc:
            raised = str(exc)
        if expected_sub is None:
            ok = raised is None
        else:
            ok = raised is not None and expected_sub in raised
        passed = passed and ok
        cases.append({
            "name": name,
            "expected_substring": expected_sub,
            "rejected": raised is not None,
            "ok": ok,
            "detail": raised if raised is not None else "(accepted)",
        })
    return {"passed": passed, "cases": cases}


# --------------------------------------------------------------------------- #
# Reporting + CLI
# --------------------------------------------------------------------------- #
def _print_human(
    results: list[dict[str, Any]],
    drift: dict[str, Any],
    probes: dict[str, Any],
    loading: dict[str, Any],
    resolver: dict[str, Any],
    scope: dict[str, Any],
    path_shape: dict[str, Any],
) -> None:
    print("Competency questions\n" + "=" * 20)
    for r in results:
        mark = "PASS" if r["status"] == "pass" else "FAIL"
        req = "" if r["required"] else " (optional)"
        print(f"[{mark}] {r['id']}{req}")
        print(f"       client={r['client_id']} projection={r['projection']} op={r['op']}")
        if r["status"] == "fail":
            print("       expected: " + json.dumps(r["expected"], ensure_ascii=False))
            print("       actual:   " + json.dumps(r["actual"], ensure_ascii=False))
            for line in r["failures"]:
                print("       - " + line)
    print("\nDrift-isolation regression\n" + "-" * 26)
    for case in drift["cases"]:
        mark = "PASS" if case["isolated"] else "FAIL"
        print(f"[{mark}] {case['name']}: expected only {case['expected_failed']} to fail; "
              f"actual failed = {case['actual_failed']}")
    print("\nRegistry shape-validation regression\n" + "-" * 36)
    for case in probes["cases"]:
        mark = "PASS" if case["ok"] else "FAIL"
        want = "accepted" if case["expected_substring"] is None else f"rejected ~ {case['expected_substring']!r}"
        print(f"[{mark}] {case['name']}: expected {want}")
    print("\nLoading-isolation regression (projection-directed loading)\n" + "-" * 57)
    for case in loading["cases"]:
        mark = "PASS" if case["ok"] else "FAIL"
        print(f"[{mark}] {case['id']}: parsed {case['parsed_file_count']} file(s) "
              f"(client={case['client_id']}, projection={case['projection']}); "
              f"excluded modules={case['excluded_module_ids']}")
        if not case["ok"]:
            print(f"       foreign files={case['foreign_files']} "
                  f"foreign clients={case['foreign_clients_touched']} "
                  f"excluded leaked={case['excluded_modules_leaked']}")
    print("\nResolver-read isolation regression (synthetic; instrumented parses)\n" + "-" * 65)
    for case in resolver["cases"]:
        mark = "PASS" if case["ok"] else "FAIL"
        print(f"[{mark}] {case['name']} ({case['projection']}): "
              f"needed={case['needed_module_ids']} excluded={case['excluded_module_ids']}; "
              f"excluded modules parsed={case['excluded_modules_parsed']}")
    print("\nRelationship/path scope-isolation regression (synthetic; result boundary)\n" + "-" * 71)
    for case in scope["cases"]:
        mark = "PASS" if case["ok"] else "FAIL"
        print(f"[{mark}] {case['name']}")
    print("\nPath-shape regression (synthetic; parallel/cycle/branching/order)\n" + "-" * 64)
    for case in path_shape["cases"]:
        mark = "PASS" if case["ok"] else "FAIL"
        print(f"[{mark}] {case['name']}")


def run(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Evaluate competency questions against a temp SQLite export of the canonical YAML."
    )
    parser.add_argument("--root", default=str(REPO_ROOT), help="Repository root to export from (default: repo root)")
    parser.add_argument("--questions", default=str(DEFAULT_QUESTIONS), help="Competency registry path")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a human report")
    parser.add_argument("--no-drift", action="store_true", help="Skip the drift-isolation regression")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    try:
        questions = load_questions(Path(args.questions))
    except QuestionError as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"registry error: {exc}", file=sys.stderr)
        return 2

    # Self-check the shape validator itself: every reproduced false-pass registry
    # must be rejected (and the valid control accepted) before we trust any answer.
    probes = run_registry_negative_probes()

    # Instrument projection-directed loading BEFORE building any export: prove no
    # question's scoped load reaches another client or an unreferenced module.
    try:
        loading = run_loading_isolation_probes(root, questions)
    except QuestionError as exc:
        print(json.dumps({"error": str(exc)}) if args.json else f"registry error: {exc}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        # Synthetic resolver-read isolation regression: prove resolution never
        # PARSES a module the projection excludes (Codex Reviewer A), instrumented
        # at the real parse boundary on a throwaway fixture.
        resolver = run_resolver_read_isolation_probe(tmpdir)
        # Synthetic relationship/path scope-isolation regression (issue #41): prove
        # a relationship's endpoints and a path's traversal stay inside the named
        # projection (an excluded-module endpoint and a disallowed predicate never
        # leak), evaluated at the RESULT boundary on a full export.
        scope = run_query_scope_probes(tmpdir)
        # Path-shape regression (issue #41): parallel-edge dedup, cycle safety,
        # branching, and deterministic ordering (Codex Reviewer A parallel-edge finding).
        path_shape = run_path_shape_probes(tmpdir)
        try:
            exports = _build_scope_exports(root, questions, tmpdir)
        except QuestionError as exc:
            print(json.dumps({"error": str(exc)}) if args.json else f"registry error: {exc}", file=sys.stderr)
            return 2
        results = _evaluate_with_exports(exports, questions)
        drift = {"passed": True, "cases": []} if args.no_drift else run_drift_regression(exports, questions, tmpdir)

    failed_required = [r for r in results if r["status"] == "fail" and r["required"]]
    exit_code = 1 if (
        failed_required
        or not drift["passed"]
        or not probes["passed"]
        or not loading["passed"]
        or not resolver["passed"]
        or not scope["passed"]
        or not path_shape["passed"]
    ) else 0

    if args.json:
        print(json.dumps(
            {
                "root": str(root),
                "questions_total": len(results),
                "questions_failed": sum(1 for r in results if r["status"] == "fail"),
                "results": results,
                "drift_regression": drift,
                "registry_probes": probes,
                "loading_isolation": loading,
                "resolver_read_isolation": resolver,
                "query_scope_isolation": scope,
                "path_shape": path_shape,
                "exit_code": exit_code,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        _print_human(results, drift, probes, loading, resolver, scope, path_shape)
        if exit_code == 0:
            print(f"\nall {len(results)} competency question(s) passed; drift isolation + registry shape "
                  "+ loading isolation + resolver-read isolation + query scope-isolation + path-shape checks hold")
        else:
            if failed_required:
                print(f"\nFAILED: {len(failed_required)} required competency question(s) failed", file=sys.stderr)
            if not drift["passed"]:
                print("FAILED: drift-isolation regression did not isolate to one question", file=sys.stderr)
            if not probes["passed"]:
                bad = [c["name"] for c in probes["cases"] if not c["ok"]]
                print(f"FAILED: registry shape-validation regression did not reject/accept as expected: {bad}", file=sys.stderr)
            if not loading["passed"]:
                bad = [c["id"] for c in loading["cases"] if not c["ok"]]
                print(f"FAILED: loading-isolation regression detected cross-client/unrelated-module leakage: {bad}", file=sys.stderr)
            if not resolver["passed"]:
                bad = [c["name"] for c in resolver["cases"] if not c["ok"]]
                print(f"FAILED: resolver-read isolation regression parsed an excluded module: {bad}", file=sys.stderr)
            if not scope["passed"]:
                bad = [c["name"] for c in scope["cases"] if not c["ok"]]
                print(f"FAILED: relationship/path scope-isolation regression leaked out-of-scope resources: {bad}", file=sys.stderr)
            if not path_shape["passed"]:
                bad = [c["name"] for c in path_shape["cases"] if not c["ok"]]
                print(f"FAILED: path-shape regression (parallel/cycle/branching/order) did not hold: {bad}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())

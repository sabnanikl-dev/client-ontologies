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
  * Reuses #21's shared loader/export path; adds no second YAML parser.
  * Builds the SQLite export in a temp dir; never touches the repo's build/.
  * Scopes each query's RESULTS strictly through the named projection. The shared
    export parses every client's canonical YAML (it is a full canonical export,
    not a projection-directed load); isolation is therefore a property of the
    ANSWER, not of file loading. An ``entities``/``rules`` query surfaces a row
    only if it belongs to the question's ``client_id`` AND its module is in the
    projection's ``includes.modules`` or its id is named (or ``.*``-matched) in
    ``includes.entities``/``includes.rules``. No other client's rows and no
    unlisted-and-unreferenced module's rows can appear in an answer.
  * Deterministic: no network, model, API credential, or live-client call; rows
    and lists are sorted before comparison and output.
  * Emits a human-readable report by default and machine JSON with ``--json``;
    on a failed question it names the question and shows expected vs actual.
  * Exits non-zero when any REQUIRED question fails or the drift-isolation
    regression fails.

Reuse seam for issue #19: import ``load_questions`` and
``evaluate_suite(db_path, questions)`` (or the lower-level ``evaluate_question``
/ ``run_query``) to prove YAML/SQLite answer equivalence against the SAME corpus
without copying any expected value into service code. Point ``evaluate_suite`` at
a service-produced export and compare its ``status`` fields.

Run from the repo root:  python3 tests/run_competency.py
"""
from __future__ import annotations

import argparse
import json
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
# loud registry error rather than a silent no-op.
KNOWN_OPS = {"entities", "rules", "projection_resources"}
# Guard types the registry may name (safety / status / isolation boundaries).
KNOWN_GUARDS = {"require_status", "forbid_status", "require_field_equals", "forbid_id_prefix"}

# Per-op: the SQLite table, its stable id column, and the plain columns a query
# may filter on or select. Anything else in `select` is treated as a
# ``fields.<name>`` extraction from the row's raw_json.
OP_TABLE = {
    "entities": ("entities", "entity_id", {"entity_id", "module_id", "entity_type", "status", "source_confidence", "public_facing", "label"}),
    "rules": ("rules", "rule_id", {"rule_id", "module_id", "title", "status", "severity", "rule_type", "statement", "source_confidence"}),
}

# Plain columns a query may filter/select on, per op (the ``select`` list also
# accepts a ``fields.<name>`` raw_json path; nothing else is a valid token).
OP_COLUMNS = {op: cols for op, (_t, _id, cols) in OP_TABLE.items()}

# Per-guard operand contract. Each required operand carries a shape check so a
# misspelled operand (e.g. ``prefix`` for ``prefixes``) becomes a loud registry
# error rather than a silent no-op. ``value`` may be any type, so it is
# presence-only.
GUARD_REQUIRED = {
    "require_status": {"statuses": "nonempty_str_list"},
    "forbid_status": {"statuses": "nonempty_str_list"},
    "require_field_equals": {"field": "nonempty_str", "value": "present"},
    "forbid_id_prefix": {"prefixes": "nonempty_str_list"},
}


class QuestionError(Exception):
    """A malformed registry entry (usage error, not a data-answer failure)."""


# --------------------------------------------------------------------------- #
# Registry loading
# --------------------------------------------------------------------------- #
def _q_err(source: str, qid: Any, msg: str) -> None:
    raise QuestionError(f"{source}: question {qid!r}: {msg}")


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


def _validate_guard(source: str, qid: Any, guard: Any) -> None:
    """Validate one guard: known type, no stray operands, required operands present + shaped."""
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

    stray = sorted(k for k in query if k not in {"op", "filters", "select"} and not k.startswith("x_"))
    if stray:
        _q_err(source, qid, f"{op} query has unknown key(s) {stray}")
    columns = OP_COLUMNS[op]

    filters = query.get("filters")
    if filters is not None:
        if not isinstance(filters, dict):
            _q_err(source, qid, "'filters' must be a mapping")
        for col in filters:
            if col not in columns:
                _q_err(source, qid, f"filter column {col!r} is not a valid {op} column {sorted(columns)}")

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


def _validate_expect(source: str, qid: Any, op: str, output_keys: Optional[set], expect: Any) -> None:
    """Validate the expect payload against the op and the query's output keys."""
    if not isinstance(expect, dict):
        _q_err(source, qid, "'expect' must be a mapping")
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
    rows = expect.get("rows")
    if not isinstance(rows, list):
        _q_err(source, qid, f"{op} question must define expect.rows as a list")
    for row in rows:
        if not isinstance(row, dict):
            _q_err(source, qid, "each expect.rows entry must be a mapping")
        keys = set(row.keys())
        if keys != output_keys:
            _q_err(source, qid, f"expect row keys {sorted(keys)} do not match select output keys {sorted(output_keys or [])}")


def validate_questions(doc: Any, source: str) -> list[dict[str, Any]]:
    """Shape-check an already-parsed registry document; return its questions.

    Raises QuestionError on any structural defect — a non-mapping registry,
    missing/duplicate identity fields, an unknown/malformed query, an unknown or
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
        for field in ("id", "client_id", "projection", "query"):
            if not q.get(field):
                raise QuestionError(f"{source}: question missing required field {field!r}: {q.get('id')!r}")
        qid = q["id"]
        if qid in seen:
            raise QuestionError(f"{source}: duplicate question id: {qid}")
        seen.add(qid)
        op, output_keys = _validate_query(source, qid, q["query"])
        _validate_expect(source, qid, op, output_keys, q.get("expect") or {})
        guards = q.get("guards")
        if guards is not None and not isinstance(guards, list):
            _q_err(source, qid, "'guards' must be a list")
        for guard in guards or []:
            _validate_guard(source, qid, guard)
    return questions


def load_questions(path: Path = DEFAULT_QUESTIONS) -> list[dict[str, Any]]:
    """Parse the competency registry through the shared loader and shape-check it.

    Returns the list of question dicts. Raises QuestionError on a structurally
    malformed registry (see ``validate_questions``) so a broken registry fails
    fast instead of silently passing.
    """
    return validate_questions(parse_yaml(path), str(path))


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

    rows = _scoped_rows(conn, op, client_id, includes)
    filters = query.get("filters") or {}
    select = query.get("select") or []
    projected = [
        {_output_key(tok): _resolve_select(r, tok) for tok in select}
        for r in rows
        if _filter_matches(r, filters)
    ]
    return _normalize_rows(projected)


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


def _ids_of(op: str, actual: Any) -> list[str]:
    """The id values in a result, for id-prefix isolation guards."""
    if op == "projection_resources":
        return [*actual.get("modules", []), *actual.get("entities", []), *actual.get("rules", [])]
    ids: list[str] = []
    for row in actual:
        for key in ("entity_id", "rule_id"):
            if key in row and isinstance(row[key], str):
                ids.append(row[key])
    return ids


def _check_guards(op: str, actual: Any, guards: list[dict[str, Any]]) -> list[str]:
    """Evaluate safety/status/isolation guards against the actual answer."""
    failures: list[str] = []
    rows = [] if op == "projection_resources" else actual
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


def _export_temp(root: Path, tmpdir: Path) -> Path:
    """Build a throwaway SQLite export of ``root`` (never the repo's build/)."""
    db_path = tmpdir / "competency.sqlite"
    e.export(root, db_path)
    return db_path


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
    ]


def run_drift_regression(base_db: Path, questions: list[dict[str, Any]], tmpdir: Path) -> dict[str, Any]:
    """Prove each controlled drift isolates to exactly its one competency question."""
    cases: list[dict[str, Any]] = []
    passed = True
    for i, scenario in enumerate(_drift_scenarios()):
        mutated = tmpdir / f"drift-{i}.sqlite"
        shutil.copyfile(base_db, mutated)
        conn = sqlite3.connect(mutated)
        try:
            scenario["mutate"](conn)
            conn.commit()
        finally:
            conn.close()
        results = evaluate_suite(mutated, questions)
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
# Registry shape-validation regression (the malformed-registry negative case)
# --------------------------------------------------------------------------- #
def _negative_probe_docs() -> list[tuple[str, dict[str, Any], Optional[str]]]:
    """Malformed (and one valid) registry documents for the shape-validator.

    Each tuple is ``(name, doc, expected_substring)``. A malformed doc names the
    substring its QuestionError must contain; the lone valid control uses
    ``None`` (must NOT raise). These lock the exact false-passes the reviewers
    reproduced: a non-mapping query, an unknown select column, a misspelled guard
    operand, an unknown filter column, duplicate output keys, a wrong-typed
    expect payload, a missing guard operand, an expected-row key typo, and a
    projection_resources question missing its resources.
    """
    def q(**over: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "id": "probe.q",
            "client_id": "c",
            "projection": "p",
            "query": {"op": "entities", "filters": {"entity_type": "metric"}, "select": ["entity_id", "status"]},
            "expect": {"rows": [{"entity_id": "c.x", "status": "draft"}]},
        }
        base.update(over)
        return base

    def doc(question: dict[str, Any]) -> dict[str, Any]:
        return {"questions": [question]}

    return [
        # Valid control — the shape validator must accept a well-formed question.
        ("valid-control", doc(q(guards=[{"type": "forbid_id_prefix", "prefixes": ["other"]}])), None),
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
        # projection_resources must define expect.resources (not rows).
        ("projection-resources-missing", doc(q(query={"op": "projection_resources"}, expect={"rows": []})), "expect.resources"),
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
def _print_human(results: list[dict[str, Any]], drift: dict[str, Any], probes: dict[str, Any]) -> None:
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

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        db_path = _export_temp(root, tmpdir)
        results = evaluate_suite(db_path, questions)
        drift = {"passed": True, "cases": []} if args.no_drift else run_drift_regression(db_path, questions, tmpdir)

    failed_required = [r for r in results if r["status"] == "fail" and r["required"]]
    exit_code = 1 if (failed_required or not drift["passed"] or not probes["passed"]) else 0

    if args.json:
        print(json.dumps(
            {
                "root": str(root),
                "questions_total": len(results),
                "questions_failed": sum(1 for r in results if r["status"] == "fail"),
                "results": results,
                "drift_regression": drift,
                "registry_probes": probes,
                "exit_code": exit_code,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        _print_human(results, drift, probes)
        if exit_code == 0:
            print(f"\nall {len(results)} competency question(s) passed; drift isolation + registry shape checks hold")
        else:
            if failed_required:
                print(f"\nFAILED: {len(failed_required)} required competency question(s) failed", file=sys.stderr)
            if not drift["passed"]:
                print("FAILED: drift-isolation regression did not isolate to one question", file=sys.stderr)
            if not probes["passed"]:
                bad = [c["name"] for c in probes["cases"] if not c["ok"]]
                print(f"FAILED: registry shape-validation regression did not reject/accept as expected: {bad}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())

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
  * Scopes strictly through the named projection — an ``entities``/``rules``
    query considers only entities/rules whose module is in the projection's
    ``includes.modules`` or whose id is named (or ``.*``-matched) in
    ``includes.entities``/``includes.rules``. It never scans other clients or
    unlisted-and-unreferenced modules.
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


class QuestionError(Exception):
    """A malformed registry entry (usage error, not a data-answer failure)."""


# --------------------------------------------------------------------------- #
# Registry loading
# --------------------------------------------------------------------------- #
def load_questions(path: Path = DEFAULT_QUESTIONS) -> list[dict[str, Any]]:
    """Parse the competency registry through the shared loader and shape-check it.

    Returns the list of question dicts. Raises QuestionError on a structurally
    malformed registry (missing questions list, missing identity fields, unknown
    op/guard) so a broken registry fails fast instead of silently passing.
    """
    doc = parse_yaml(path)
    questions = doc.get("questions")
    if not isinstance(questions, list) or not questions:
        raise QuestionError(f"{path}: registry must define a non-empty 'questions' list")
    seen: set[str] = set()
    for q in questions:
        if not isinstance(q, dict):
            raise QuestionError(f"{path}: each question must be a mapping")
        for field in ("id", "client_id", "projection", "query"):
            if not q.get(field):
                raise QuestionError(f"{path}: question missing required field {field!r}: {q.get('id')!r}")
        qid = q["id"]
        if qid in seen:
            raise QuestionError(f"{path}: duplicate question id: {qid}")
        seen.add(qid)
        op = (q.get("query") or {}).get("op")
        if op not in KNOWN_OPS:
            raise QuestionError(f"{path}: question {qid} has unknown query op: {op!r}")
        if op in ("entities", "rules") and (q.get("expect") or {}).get("rows") is None:
            raise QuestionError(f"{path}: question {qid} ({op}) must define expect.rows")
        if op == "projection_resources" and (q.get("expect") or {}).get("resources") is None:
            raise QuestionError(f"{path}: question {qid} (projection_resources) must define expect.resources")
        for guard in q.get("guards") or []:
            if guard.get("type") not in KNOWN_GUARDS:
                raise QuestionError(f"{path}: question {qid} has unknown guard type: {guard.get('type')!r}")
    return questions


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
# Reporting + CLI
# --------------------------------------------------------------------------- #
def _print_human(results: list[dict[str, Any]], drift: dict[str, Any]) -> None:
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

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        db_path = _export_temp(root, tmpdir)
        results = evaluate_suite(db_path, questions)
        drift = {"passed": True, "cases": []} if args.no_drift else run_drift_regression(db_path, questions, tmpdir)

    failed_required = [r for r in results if r["status"] == "fail" and r["required"]]
    exit_code = 1 if (failed_required or not drift["passed"]) else 0

    if args.json:
        print(json.dumps(
            {
                "root": str(root),
                "questions_total": len(results),
                "questions_failed": sum(1 for r in results if r["status"] == "fail"),
                "results": results,
                "drift_regression": drift,
                "exit_code": exit_code,
            },
            ensure_ascii=False,
            indent=2,
        ))
    else:
        _print_human(results, drift)
        if exit_code == 0:
            print(f"\nall {len(results)} competency question(s) passed; drift isolation holds")
        else:
            if failed_required:
                print(f"\nFAILED: {len(failed_required)} required competency question(s) failed", file=sys.stderr)
            if not drift["passed"]:
                print("FAILED: drift-isolation regression did not isolate to one question", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(run())

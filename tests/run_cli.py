#!/usr/bin/env python3
"""Runtime-surface regression tests for the read-only ontology CLI + service (#19).

Stdlib-only, no new dependencies, runnable from the repo root:

    python3 tests/run_cli.py

It proves, deterministically and against the LIVE client data:

  1. CLI acceptance (issue #19 acceptance criteria):
       * ``check-copy jmd-menswear "Add to cart today"`` flags the blocking
         ``showroom-not-ecommerce`` rule and exits non-zero.
       * ``check-copy femme-events "...world-class luxury firm"`` reports the
         ``no-corporate-tone`` WARNING but exits 0; ``--fail-on warning`` exits
         non-zero. (Exit semantics inherited verbatim from issue #11.)
       * ``context --projection femme-events.local-seo`` resolves the projection's
         entities + its rules.
       * Structured, deterministic, non-zero errors for an unknown client, an
         unknown projection, an unavailable SQLite path, and malformed args.

  2. YAML ⇄ SQLite backend parity: every operation returns byte-identical results
     (modulo the ``_meta`` provenance stamp) from the ``yaml`` and ``sqlite``
     backends, and the SQLite backend never invokes Ruby.

  3. Projection isolation (negative): a projection-scoped context excludes
     resources from a module the projection does not pull in — leakage is caught.

  4. Competency-corpus reuse (issue #31): every question's answer computed through
     the runtime service equals the competency runner's own computed answer, in
     BOTH backends, and every required question passes — WITHOUT copying any
     expected value into service or test code (they live only in the registry).

  5. Planning-only preservation: Femme's ``draft`` / ``baseline: unknown`` metrics
     stay draft, ``planning_only: true``, baseline unknown across both backends —
     never presented as recorded outcomes.

  6. Packaging: the ``ontology`` / ``ontology-mcp`` console entry points declared
     in ``pyproject.toml`` resolve to importable callables.
"""
from __future__ import annotations

import io
import json
import sqlite3
import sys
import tempfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import ontology_service as svc  # noqa: E402
import ontology_cli as cli  # noqa: E402
import export_sqlite as e  # noqa: E402
import run_competency as rc  # noqa: E402


# --------------------------------------------------------------------------- #
# Harness
# --------------------------------------------------------------------------- #
class Failure(AssertionError):
    pass


def check(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def run_cli(argv: list[str], stdin_text: Optional[str] = None) -> tuple[int, str, str]:
    """Invoke the CLI in-process; return ``(exit_code, stdout, stderr)``.

    argparse usage errors raise SystemExit(2); normalize them to an exit code so
    "malformed args" is testable exactly like a ServiceError."""
    out, err = io.StringIO(), io.StringIO()
    stdin = io.StringIO(stdin_text if stdin_text is not None else "")
    try:
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(argv, stdin=stdin)
    except SystemExit as exc:  # argparse
        code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue(), err.getvalue()


# --------------------------------------------------------------------------- #
# 1. CLI acceptance
# --------------------------------------------------------------------------- #
def test_check_copy_blocking() -> None:
    code, out, _ = run_cli(
        ["check-copy", "--client", "jmd-menswear", "--text", "Add to cart today", "--root", str(REPO_ROOT)]
    )
    payload = json.loads(out)
    ids = [v["rule_id"] for v in payload["violations"]]
    check("jmd-menswear.website.showroom-not-ecommerce" in ids, f"blocking rule not flagged: {ids}")
    check(code == 1, f"blocking violation must exit 1, got {code}")


def test_check_copy_warning_default_then_failon() -> None:
    args = ["check-copy", "--client", "femme-events", "--text", "We are a world-class luxury firm", "--root", str(REPO_ROOT)]
    code, out, _ = run_cli(args)
    ids = [v["rule_id"] for v in json.loads(out)["violations"]]
    check("femme-events.brand.no-corporate-tone" in ids, f"warning rule not reported: {ids}")
    check(code == 0, f"a warning must exit 0 by default, got {code}")
    code2, _, _ = run_cli(args + ["--fail-on", "warning"])
    check(code2 == 1, f"--fail-on warning must exit non-zero, got {code2}")


def test_check_copy_clean_exits_zero() -> None:
    code, out, _ = run_cli(
        ["check-copy", "--client", "jmd-menswear", "--text", "Recently on the floor", "--root", str(REPO_ROOT)]
    )
    check(json.loads(out)["violations"] == [], "clean copy should report no violations")
    check(code == 0, f"clean copy must exit 0, got {code}")


def test_context_resolves_projection() -> None:
    code, out, _ = run_cli(
        ["context", "--client", "femme-events", "--projection", "femme-events.local-seo", "--root", str(REPO_ROOT)]
    )
    check(code == 0, f"context must exit 0, got {code}")
    payload = json.loads(out)
    ent_ids = {e_["id"] for e_ in payload["entities"]}
    rule_ids = {r["id"] for r in payload["active_rules"]}
    # The projection's explicitly listed entities/rules must resolve.
    check("femme-events.visibility.business-fact" in ent_ids, "listed entity missing")
    check("femme-events.visibility.service-area" in ent_ids, "service-area missing")
    check(
        "femme-events.visibility.public-account-mutations-require-approval" in rule_ids,
        "listed approval rule missing",
    )


def test_error_unknown_client() -> None:
    code, _, err = run_cli(["context", "--client", "does-not-exist", "--root", str(REPO_ROOT)])
    check(code == 2, f"unknown client must exit 2, got {code}")
    check("error" in json.loads(err), "unknown client must emit structured {'error':...}")


def test_error_unknown_projection() -> None:
    code, _, err = run_cli(["projection", "--id", "femme-events.nope", "--root", str(REPO_ROOT)])
    check(code == 2, f"unknown projection must exit 2, got {code}")
    check("error" in json.loads(err), "unknown projection must be structured")


def test_error_unavailable_sqlite() -> None:
    code, _, err = run_cli(
        ["list-clients", "--source", "sqlite", "--sqlite-path", str(REPO_ROOT / "no-such.sqlite")]
    )
    check(code == 2, f"unavailable sqlite must exit 2, got {code}")
    check("error" in json.loads(err), "unavailable sqlite must be structured")


def test_error_malformed_args() -> None:
    # Unknown flag -> argparse usage error, exit 2.
    code, _, _ = run_cli(["context", "--client", "femme-events", "--bogus-flag", "x"])
    check(code == 2, f"malformed args must exit 2, got {code}")
    # Two text sources -> structured ServiceError, exit 2.
    code2, _, err2 = run_cli(
        ["check-copy", "--client", "femme-events", "--text", "a", "--file", "b", "--root", str(REPO_ROOT)]
    )
    check(code2 == 2, f"two text sources must exit 2, got {code2}")
    check("error" in json.loads(err2), "two text sources must be structured")


def test_error_backend_drift() -> None:
    # A non-export SQLite file must fail closed, not silently return empty data.
    with tempfile.TemporaryDirectory() as tmp:
        bogus = Path(tmp) / "bogus.sqlite"
        conn = sqlite3.connect(bogus)
        conn.execute("CREATE TABLE unrelated (x INTEGER)")
        conn.commit()
        conn.close()
        code, _, err = run_cli(["list-clients", "--source", "sqlite", "--sqlite-path", str(bogus)])
        check(code == 2, f"backend drift must exit 2, got {code}")
        check("error" in json.loads(err), "backend drift must be structured")


# --------------------------------------------------------------------------- #
# 2. YAML ⇄ SQLite parity
# --------------------------------------------------------------------------- #
def _strip_meta(obj: Any) -> Any:
    """Drop the ``_meta`` provenance envelope so backend-invariant content can be
    compared (read_mode/commit/timestamp legitimately differ)."""
    if isinstance(obj, dict):
        return {k: _strip_meta(v) for k, v in obj.items() if k != "_meta"}
    if isinstance(obj, list):
        return [_strip_meta(v) for v in obj]
    return obj


def test_backend_parity(ds_yaml: svc.Dataset, ds_sqlite: svc.Dataset) -> None:
    calls = [
        lambda ds: svc.list_clients(ds),
        lambda ds: svc.get_client_context(ds, "femme-events"),
        lambda ds: svc.get_client_context(ds, "femme-events", "femme-events.local-seo"),
        lambda ds: svc.get_client_context(ds, "jmd-menswear", "jmd-menswear.website-build"),
        lambda ds: svc.list_rules(ds, "jmd-menswear"),
        lambda ds: svc.list_rules(ds, "femme-events", severity="blocking"),
        lambda ds: svc.list_rules(ds, "jmd-menswear", workstream="website"),
        lambda ds: svc.get_projection(ds, "jmd-menswear.inventory-workflow"),
        lambda ds: svc.get_projection(ds, "femme-events.local-seo"),
        lambda ds: svc.check_copy(ds, "jmd-menswear", "Add to cart, in stock, checkout"),
        lambda ds: svc.check_copy(ds, "femme-events", "world-class luxury firm", fail_on="warning"),
    ]
    for i, call in enumerate(calls):
        a = _strip_meta(call(ds_yaml))
        b = _strip_meta(call(ds_sqlite))
        check(a == b, f"backend parity mismatch in call #{i}:\n yaml={json.dumps(a)}\n sqlite={json.dumps(b)}")
    # The SQLite dataset must carry no repo root -> it cannot have shelled to Ruby.
    check(ds_sqlite.read_mode == "sqlite" and ds_sqlite.root is None, "sqlite backend must not reference a YAML root")


# --------------------------------------------------------------------------- #
# 3. Projection isolation (negative leakage)
# --------------------------------------------------------------------------- #
def test_projection_isolation(ds_yaml: svc.Dataset) -> None:
    # website-build excludes the inventory-images module; no inventory resource
    # may appear in its resolved context.
    ctx = svc.get_client_context(ds_yaml, "jmd-menswear", "jmd-menswear.website-build")
    ids = [x["id"] for x in ctx["entities"]] + [r["id"] for r in ctx["active_rules"]]
    leaks = [i for i in ids if isinstance(i, str) and i.startswith("jmd-menswear.inventory")]
    check(not leaks, f"inventory resources leaked into website-build context: {leaks}")
    # And no other client's resources ever appear.
    foreign = [i for i in ids if isinstance(i, str) and not i.startswith("jmd-menswear.")]
    check(not foreign, f"foreign-client resources leaked: {foreign}")
    # get_projection resolves only within the projection's own client.
    proj = svc.get_projection(ds_yaml, "jmd-menswear.website-build")
    rids = [r["id"] for r in proj["resolved"]["rules"]]
    check(
        not any(i.startswith("jmd-menswear.inventory") for i in rids),
        f"get_projection leaked inventory rules: {rids}",
    )


# --------------------------------------------------------------------------- #
# 4. Competency-corpus reuse (issue #31) — service answers == runner answers
# --------------------------------------------------------------------------- #
_ENTITY_COLS = ("entity_id", "module_id", "entity_type", "status", "source_confidence", "public_facing", "label")
_RULE_COLS = ("rule_id", "module_id", "title", "status", "severity", "rule_type", "statement", "source_confidence")


def _row_for(op: str, resource: dict[str, Any]) -> dict[str, Any]:
    """Shape a service resource into the competency runner's row+_raw contract so
    the runner's own ``_filter_matches`` / ``_resolve_select`` apply unchanged."""
    id_key = "entity_id" if op == "entities" else "rule_id"
    cols = _ENTITY_COLS if op == "entities" else _RULE_COLS
    row: dict[str, Any] = {"_raw": resource}
    for col in cols:
        row[col] = resource.get("id") if col == id_key else resource.get(col)
    return row


def service_answer(ds: svc.Dataset, question: dict[str, Any]) -> Any:
    """Answer a competency question purely through the runtime service, then apply
    the runner's OWN filter/select/normalize helpers — so no query semantics and
    no expected values are re-encoded here."""
    query = question["query"]
    op = query["op"]
    client_id, projection_id = question["client_id"], question["projection"]
    projection = svc._require_projection(ds, projection_id, client_id)
    includes = projection.get("includes") or {}
    if op == "projection_resources":
        return {
            "modules": sorted(includes.get("modules") or []),
            "entities": sorted(includes.get("entities") or []),
            "rules": sorted(includes.get("rules") or []),
        }
    entities, rules = svc.resolve_scope(ds, client_id, projection_id)
    source = entities if op == "entities" else rules
    rows = [_row_for(op, r) for r in source]
    filters = query.get("filters") or {}
    select = query.get("select") or []
    projected = [
        {rc._output_key(tok): rc._resolve_select(r, tok) for tok in select}
        for r in rows
        if rc._filter_matches(r, filters)
    ]
    return rc._normalize_rows(projected)


def test_competency_parity(ds_yaml: svc.Dataset, ds_sqlite: svc.Dataset, canonical_db: Path) -> None:
    questions = rc.load_questions()
    check(len(questions) >= 4, "expected the live competency corpus to have >= 4 questions")
    conn = sqlite3.connect(canonical_db)
    try:
        for q in questions:
            runner = rc.evaluate_question(conn, q)  # the corpus's own computed answer
            if q.get("required", True):
                check(
                    runner["status"] == "pass",
                    f"required competency question failed against export: {q['id']}: {runner['failures']}",
                )
            ans_yaml = service_answer(ds_yaml, q)
            ans_sqlite = service_answer(ds_sqlite, q)
            check(
                ans_yaml == ans_sqlite,
                f"service YAML/SQLite answers differ for {q['id']}",
            )
            check(
                ans_yaml == runner["actual"],
                f"service answer != competency-runner answer for {q['id']}:\n"
                f" service={json.dumps(ans_yaml, sort_keys=True)}\n"
                f" runner ={json.dumps(runner['actual'], sort_keys=True)}",
            )
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# 5. Planning-only preservation (Femme draft metrics)
# --------------------------------------------------------------------------- #
_METRIC_IDS = {
    "femme-events.visibility.metric.gbp-calls",
    "femme-events.visibility.metric.gbp-direction-requests",
    "femme-events.visibility.metric.website-clicks",
}


def test_metric_planning_only(ds_yaml: svc.Dataset, ds_sqlite: svc.Dataset) -> None:
    for ds in (ds_yaml, ds_sqlite):
        ctx = svc.get_client_context(ds, "femme-events", "femme-events.local-seo")
        by_id = {e_["id"]: e_ for e_ in ctx["entities"]}
        for mid in _METRIC_IDS:
            check(mid in by_id, f"[{ds.read_mode}] metric missing from context: {mid}")
            m = by_id[mid]
            check(m["status"] == "draft", f"[{ds.read_mode}] {mid} status must stay draft, got {m['status']}")
            check(m["source_confidence"] == "draft", f"[{ds.read_mode}] {mid} confidence must stay draft")
            check(m["planning_only"] is True, f"[{ds.read_mode}] {mid} must be flagged planning_only")
            check(
                m["fields"].get("baseline") == "unknown",
                f"[{ds.read_mode}] {mid} baseline must stay unknown, got {m['fields'].get('baseline')!r}",
            )
        # A draft metric must never surface as an ACTIVE rule/outcome in context.
        rule_ids = {r["id"] for r in ctx["active_rules"]}
        check(not (_METRIC_IDS & rule_ids), "a draft metric must never appear as an active rule")


def test_evidence_pointers_preserved(ds_yaml: svc.Dataset, ds_sqlite: svc.Dataset) -> None:
    # An entity with evidence must carry the same evidence pointers in both modes.
    def biz_fact(ds: svc.Dataset) -> dict[str, Any]:
        ctx = svc.get_client_context(ds, "femme-events", "femme-events.local-seo")
        return next(e_ for e_ in ctx["entities"] if e_["id"] == "femme-events.visibility.business-fact")

    y, s = biz_fact(ds_yaml), biz_fact(ds_sqlite)
    check(y["evidence"] and y["evidence"] == s["evidence"], "evidence pointers must survive both backends unchanged")


# --------------------------------------------------------------------------- #
# 6. Packaging entry points
# --------------------------------------------------------------------------- #
def _read_pyproject_project() -> dict[str, Any]:
    """Return the ``[project]`` fields this test asserts on (``scripts`` +
    ``dependencies``), staying stdlib-only across the declared Python floor.

    ``tomllib`` only exists on Python 3.11+, but ``pyproject.toml`` declares
    ``requires-python = ">=3.10"`` and the runtime/test suite is meant to stay
    dependency-light (no ``tomli`` backport). So we use ``tomllib`` when present
    and otherwise parse the narrow subset we check with a tiny table reader —
    keeping the packaging regression runnable on the intended interpreter floor.
    """
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    try:
        import tomllib
    except ModuleNotFoundError:
        return _parse_pyproject_subset(text)
    data = tomllib.loads(text)
    project = data["project"]
    return {"scripts": project.get("scripts", {}), "dependencies": project.get("dependencies")}


def _parse_pyproject_subset(text: str) -> dict[str, Any]:
    """Minimal TOML reader for ``[project.scripts]`` string entries and
    ``[project]``'s inline ``dependencies`` list — the only fields asserted
    here. Not a general TOML parser; deliberately narrow and stdlib-only."""
    scripts: dict[str, str] = {}
    dependencies: Optional[list[str]] = None
    section: Optional[str] = None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip().strip('"'), val.strip()
        if section == "project.scripts":
            scripts[key] = val.strip('"')
        elif section == "project" and key == "dependencies":
            if val.startswith("[") and val.endswith("]"):
                inner = val[1:-1].strip()
                dependencies = (
                    [x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip()]
                    if inner
                    else []
                )
    return {"scripts": scripts, "dependencies": dependencies}


def test_entry_points() -> None:
    project = _read_pyproject_project()
    scripts = project["scripts"]
    check(scripts.get("ontology") == "ontology_cli:main", "ontology entry point mismatch")
    check(
        scripts.get("ontology-mcp") == "ontology_cli:mcp_entrypoint",
        "ontology-mcp entry point mismatch",
    )
    check(callable(cli.main) and callable(cli.mcp_entrypoint), "entry-point callables must resolve")
    # The registered ontology-mcp placeholder must fail closed (MCP is the next PR).
    with redirect_stderr(io.StringIO()):
        code = cli.mcp_entrypoint([])
    check(code == 2, f"ontology-mcp placeholder must fail closed with exit 2, got {code}")
    # The core must add no third-party dependencies.
    check(project["dependencies"] == [], "core must declare zero runtime dependencies")


# --------------------------------------------------------------------------- #
# Runner
# --------------------------------------------------------------------------- #
def main() -> int:
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        canonical_db = Path(tmp) / "runtime.sqlite"
        e.export(REPO_ROOT, canonical_db)  # the SQLite backend a Ruby-free consumer reads
        ds_yaml = svc.load_dataset("yaml", root=REPO_ROOT)
        ds_sqlite = svc.load_dataset("sqlite", sqlite_path=canonical_db)

        tests = [
            ("check_copy_blocking", lambda: test_check_copy_blocking()),
            ("check_copy_warning", lambda: test_check_copy_warning_default_then_failon()),
            ("check_copy_clean", lambda: test_check_copy_clean_exits_zero()),
            ("context_resolves_projection", lambda: test_context_resolves_projection()),
            ("error_unknown_client", lambda: test_error_unknown_client()),
            ("error_unknown_projection", lambda: test_error_unknown_projection()),
            ("error_unavailable_sqlite", lambda: test_error_unavailable_sqlite()),
            ("error_malformed_args", lambda: test_error_malformed_args()),
            ("error_backend_drift", lambda: test_error_backend_drift()),
            ("backend_parity", lambda: test_backend_parity(ds_yaml, ds_sqlite)),
            ("projection_isolation", lambda: test_projection_isolation(ds_yaml)),
            ("competency_parity", lambda: test_competency_parity(ds_yaml, ds_sqlite, canonical_db)),
            ("metric_planning_only", lambda: test_metric_planning_only(ds_yaml, ds_sqlite)),
            ("evidence_pointers_preserved", lambda: test_evidence_pointers_preserved(ds_yaml, ds_sqlite)),
            ("entry_points", lambda: test_entry_points()),
        ]
        for name, fn in tests:
            try:
                fn()
                print(f"ok: {name}")
            except Failure as exc:
                failures.append(f"{name}: {exc}")
            except Exception as exc:  # noqa: BLE001 - any unexpected error is a test failure
                failures.append(f"{name}: unexpected {type(exc).__name__}: {exc}")

    if failures:
        print("\nCLI/SERVICE TEST FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1
    print("\nall runtime CLI/service tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

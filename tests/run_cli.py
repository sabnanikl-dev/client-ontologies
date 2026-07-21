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
import shutil
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
    # Unknown flag -> argparse usage error mapped to the STRUCTURED {"error":...}
    # contract, exit 2 (Codex Reviewer A / Integration Auditor: argparse must not
    # terminate with free-form usage text a machine consumer cannot parse).
    code, out, err = run_cli(["context", "--client", "femme-events", "--bogus-flag", "x"])
    check(code == 2, f"malformed args must exit 2, got {code}")
    check(out == "", "malformed args must print nothing on stdout")
    parsed = json.loads(err)  # must be parseable JSON, not argparse usage text
    check("error" in parsed, f"malformed args must emit structured {{'error':...}}, got {err!r}")
    # A missing required subcommand is also structured.
    code_sub, _, err_sub = run_cli([])
    check(code_sub == 2, f"missing subcommand must exit 2, got {code_sub}")
    check("error" in json.loads(err_sub), "missing subcommand must be structured")
    # A missing required option (--client) is structured too.
    code_req, _, err_req = run_cli(["context", "--root", str(REPO_ROOT)])
    check(code_req == 2, f"missing --client must exit 2, got {code_req}")
    check("error" in json.loads(err_req), "missing required option must be structured")
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


def test_error_unknown_workstream_no_bypass() -> None:
    """A misspelled --workstream must fail closed, never silently select zero
    rules and let a blocking check_copy report a clean pass (Codex Reviewer A)."""
    text = "Add to cart today"
    # Sanity: with NO scope, this trips the blocking showroom-not-ecommerce rule.
    code_ok, out_ok, _ = run_cli(
        ["check-copy", "--client", "jmd-menswear", "--text", text, "--root", str(REPO_ROOT)]
    )
    ids = [v["rule_id"] for v in json.loads(out_ok)["violations"]]
    check(
        "jmd-menswear.website.showroom-not-ecommerce" in ids and code_ok == 1,
        "precondition: unscoped check must flag the blocking rule and exit 1",
    )
    # A misspelled workstream must NOT quietly pass — it must be a structured exit 2.
    code, out, err = run_cli(
        ["check-copy", "--client", "jmd-menswear", "--workstream", "definitely-not-a-workstream",
         "--text", text, "--root", str(REPO_ROOT)]
    )
    check(code == 2, f"unknown workstream must exit 2 (not a silent pass), got {code}")
    check(out == "", "unknown workstream must not print a clean result on stdout")
    check("error" in json.loads(err), "unknown workstream must be structured")
    # list-rules must reject an unknown workstream the same way.
    code_r, _, err_r = run_cli(
        ["rules", "--client", "jmd-menswear", "--workstream", "nope", "--root", str(REPO_ROOT)]
    )
    check(code_r == 2, f"list-rules unknown workstream must exit 2, got {code_r}")
    check("error" in json.loads(err_r), "list-rules unknown workstream must be structured")
    # A REAL workstream still works (no over-rejection).
    code_g, _, _ = run_cli(
        ["check-copy", "--client", "jmd-menswear", "--workstream", "website",
         "--text", text, "--root", str(REPO_ROOT)]
    )
    check(code_g == 1, f"real workstream 'website' must still enforce the blocking rule, got {code_g}")


def _foreign_partial_sqlite(path: Path) -> None:
    """Reviewer A's exact repro: a schema-*compatible* but INCOMPLETE database with
    only the three tables the old loader queried, a forged JMD client, and a
    website module whose raw_json carries no rules. The old loader accepted this
    and reported ``violations: [], exit_code: 0`` for blocking copy."""
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE clients (client_id TEXT PRIMARY KEY, raw_json TEXT)")
    conn.execute("CREATE TABLE modules (module_id TEXT PRIMARY KEY, client_id TEXT, raw_json TEXT)")
    conn.execute("CREATE TABLE projections (projection_id TEXT PRIMARY KEY, client_id TEXT, raw_json TEXT)")
    conn.execute(
        "INSERT INTO clients VALUES (?, ?)",
        ("jmd-menswear", json.dumps({"kind": "client", "id": "jmd-menswear", "name": "JMD"})),
    )
    conn.execute(
        "INSERT INTO modules VALUES (?, ?, ?)",
        ("jmd-menswear.website", "jmd-menswear",
         json.dumps({"kind": "ontology_module", "id": "jmd-menswear.website",
                     "client_id": "jmd-menswear", "rules": []})),
    )
    conn.commit()
    conn.close()


def test_error_foreign_partial_sqlite_no_bypass() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        foreign = Path(tmp) / "foreign.sqlite"
        _foreign_partial_sqlite(foreign)
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--text", "Add to cart today",
             "--source", "sqlite", "--sqlite-path", str(foreign)]
        )
        check(code == 2, f"foreign/partial sqlite must fail closed with exit 2, got {code} (out={out!r})")
        check("error" in json.loads(err), "foreign/partial sqlite must be structured")


def test_error_drifted_sqlite_suppressed_rule(canonical_db: Path) -> None:
    """Schema-compatible DRIFT (Codex Reviewer B): a genuine full export whose
    module raw_json was emptied of its rules must fail closed, because the
    normalized `rules` table no longer matches the module documents the service
    reads — otherwise a tampered snapshot could suppress a blocking rule."""
    with tempfile.TemporaryDirectory() as tmp:
        drifted = Path(tmp) / "drifted.sqlite"
        shutil.copyfile(canonical_db, drifted)
        conn = sqlite3.connect(drifted)
        try:
            row = conn.execute(
                "SELECT raw_json FROM modules WHERE module_id = ?", ("jmd-menswear.website",)
            ).fetchone()
            doc = json.loads(row[0])
            doc["rules"] = []  # strip the blocking showroom-not-ecommerce rule
            conn.execute(
                "UPDATE modules SET raw_json = ? WHERE module_id = ?",
                (json.dumps(doc), "jmd-menswear.website"),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--text", "Add to cart today",
             "--source", "sqlite", "--sqlite-path", str(drifted)]
        )
        check(code == 2, f"drifted export must fail closed with exit 2, got {code} (out={out!r})")
        check("error" in json.loads(err), "drifted export must be structured")


def test_error_forged_rawid_mismatch(canonical_db: Path) -> None:
    """A row whose primary id disagrees with its raw_json id is a tampered/drifted
    export and must fail closed (row-id/raw-id agreement)."""
    with tempfile.TemporaryDirectory() as tmp:
        forged = Path(tmp) / "forged.sqlite"
        shutil.copyfile(canonical_db, forged)
        conn = sqlite3.connect(forged)
        try:
            row = conn.execute(
                "SELECT raw_json FROM clients WHERE client_id = ?", ("jmd-menswear",)
            ).fetchone()
            doc = json.loads(row[0])
            doc["id"] = "not-jmd-menswear"  # raw id no longer matches the PK
            conn.execute(
                "UPDATE clients SET raw_json = ? WHERE client_id = ?",
                (json.dumps(doc), "jmd-menswear"),
            )
            conn.commit()
        finally:
            conn.close()
        code, _, err = run_cli(["list-clients", "--source", "sqlite", "--sqlite-path", str(forged)])
        check(code == 2, f"row-id/raw-id mismatch must fail closed with exit 2, got {code}")
        check("error" in json.loads(err), "row-id/raw-id mismatch must be structured")


def test_error_sqlite_same_id_rule_tampering(canonical_db: Path) -> None:
    """Same-ID SEMANTIC tampering (Codex Reviewer A, exception-cycle #1): a genuine
    full export whose module ``raw_json`` keeps the blocking rule's ID but rewrites
    its ``machine_check`` (so it no longer matches ``Add to cart today``), while the
    normalized ``rules`` table is left intact. The ID sets still agree, so an
    id-set-only check would pass and the rewritten module document the service
    reads would suppress the blocking rule. Full-content authentication must fail
    closed with structured exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        tampered = Path(tmp) / "same-id-tamper.sqlite"
        shutil.copyfile(canonical_db, tampered)
        conn = sqlite3.connect(tampered)
        try:
            row = conn.execute(
                "SELECT raw_json FROM modules WHERE module_id = ?", ("jmd-menswear.website",)
            ).fetchone()
            doc = json.loads(row[0])
            hit = False
            for rule in doc.get("rules") or []:
                if rule.get("id") == "jmd-menswear.website.showroom-not-ecommerce":
                    # Keep the id; neuter the executable check the service runs.
                    rule["machine_check"] = {
                        "type": "disallowed_terms",
                        "disallowed_terms": ["never-match-this"],
                    }
                    hit = True
            check(hit, "precondition: showroom-not-ecommerce must be present to tamper with")
            conn.execute(
                "UPDATE modules SET raw_json = ? WHERE module_id = ?",
                (json.dumps(doc), "jmd-menswear.website"),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--text", "Add to cart today",
             "--source", "sqlite", "--sqlite-path", str(tampered)]
        )
        check(code == 2, f"same-id rule tampering must fail closed with exit 2, got {code} (out={out!r})")
        check("error" in json.loads(err), "same-id rule tampering must be structured")


def test_error_sqlite_correlated_module_deletion(canonical_db: Path) -> None:
    """Correlated deletion (Codex Reviewer B, exception-cycle #2): delete a
    manifest-declared module AND its normalized rules/entities descendants
    together, leaving the manifest intact. The normalized-vs-embedded content
    check is vacuously consistent (both sides lose the module's resources), so
    manifest-membership validation must catch the manifest still declaring the
    now-absent module and fail closed with structured exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        deleted = Path(tmp) / "correlated-delete.sqlite"
        shutil.copyfile(canonical_db, deleted)
        conn = sqlite3.connect(deleted)
        try:
            # Delete the module row and every normalized descendant that referenced
            # it — but NOT its manifest declaration.
            conn.execute("DELETE FROM modules WHERE module_id = ?", ("jmd-menswear.website",))
            conn.execute("DELETE FROM rules WHERE module_id = ?", ("jmd-menswear.website",))
            conn.execute("DELETE FROM entities WHERE module_id = ?", ("jmd-menswear.website",))
            conn.execute("DELETE FROM relationships WHERE module_id = ?", ("jmd-menswear.website",))
            conn.commit()
            # Sanity: the manifest still declares the deleted module.
            man = json.loads(
                conn.execute(
                    "SELECT raw_json FROM manifests WHERE client_id = ?", ("jmd-menswear",)
                ).fetchone()[0]
            )
            declared = {m.get("id") for m in man.get("modules") or []}
            check("jmd-menswear.website" in declared, "precondition: manifest must still declare the deleted module")
        finally:
            conn.close()
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--text", "Add to cart today",
             "--source", "sqlite", "--sqlite-path", str(deleted)]
        )
        check(code == 2, f"correlated module deletion must fail closed with exit 2, got {code} (out={out!r})")
        check("error" in json.loads(err), "correlated module deletion must be structured")


def test_error_sqlite_embedded_owner_drift(canonical_db: Path) -> None:
    """Embedded ownership drift (Integration Auditor, exception-cycle #3): rewrite
    only a module's EMBEDDED ``raw_json.client_id`` (jmd-menswear -> femme-events, a
    real client) while leaving the SQL row owner, normalized rules, and all IDs
    unchanged. The service selects modules by the embedded value, so the JMD
    website module would silently drop out of JMD enforcement. Embedded-owner/SQL-
    owner authentication must fail closed with structured exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        drifted = Path(tmp) / "owner-drift.sqlite"
        shutil.copyfile(canonical_db, drifted)
        conn = sqlite3.connect(drifted)
        try:
            row = conn.execute(
                "SELECT client_id, raw_json FROM modules WHERE module_id = ?",
                ("jmd-menswear.website",),
            ).fetchone()
            check(row[0] == "jmd-menswear", "precondition: SQL owner must be jmd-menswear")
            doc = json.loads(row[1])
            doc["client_id"] = "femme-events"  # embedded owner only; SQL owner untouched
            conn.execute(
                "UPDATE modules SET raw_json = ? WHERE module_id = ?",
                (json.dumps(doc), "jmd-menswear.website"),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--text", "Add to cart today",
             "--source", "sqlite", "--sqlite-path", str(drifted)]
        )
        check(code == 2, f"embedded owner drift must fail closed with exit 2, got {code} (out={out!r})")
        check("error" in json.loads(err), "embedded owner drift must be structured")


def test_error_sqlite_projection_includes_tampering(canonical_db: Path) -> None:
    """Same-ID projection-content tampering (Codex Reviewer A, review 4741489241):
    a genuine full export whose projection ``raw_json.includes`` is emptied while
    the normalized ``includes_json`` column stays canonical. The service resolves
    projection scope from the EMBEDDED ``raw_json.includes``, so a projection-scoped
    ``check_copy`` would resolve zero rules and report a clean pass, even though the
    normalized column still looks canonical. Projection-envelope authentication
    (``includes_json`` == ``raw_json.includes``) must fail closed with structured
    exit 2."""
    pid = "jmd-menswear.website-build"
    with tempfile.TemporaryDirectory() as tmp:
        tampered = Path(tmp) / "proj-includes-tamper.sqlite"
        shutil.copyfile(canonical_db, tampered)
        conn = sqlite3.connect(tampered)
        try:
            row = conn.execute(
                "SELECT includes_json, raw_json FROM projections WHERE projection_id = ?",
                (pid,),
            ).fetchone()
            check(row is not None, f"precondition: projection {pid} must exist in the export")
            norm = json.loads(row[0])
            check(
                norm.get("modules") and "jmd-menswear.website" in norm["modules"],
                "precondition: website-build includes_json must pull in the website module",
            )
            doc = json.loads(row[1])
            # Empty ONLY the embedded includes the service consumes; leave the
            # normalized includes_json column canonical.
            doc["includes"] = {"modules": [], "entities": [], "rules": []}
            conn.execute(
                "UPDATE projections SET raw_json = ? WHERE projection_id = ?",
                (json.dumps(doc), pid),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--projection", pid,
             "--text", "Add to cart today", "--source", "sqlite", "--sqlite-path", str(tampered)]
        )
        check(
            code == 2,
            f"projection includes tampering must fail closed with exit 2, got {code} (out={out!r})",
        )
        check("error" in json.loads(err), "projection includes tampering must be structured")


def test_error_sqlite_client_only_workstream_bypass(canonical_db: Path) -> None:
    """Same-ID client workstream tampering (Codex Reviewer A, review 4741489241):
    a genuine full export whose JMD ``clients.raw_json.workstreams`` gains a
    client-only ``{"id": "bypass"}`` entry that NO module carries. The client id and
    SQL row are unchanged, so the export authenticates — but scoping ``check_copy``
    to ``bypass`` would select zero rules and report a clean result. An enforceable
    workstream must resolve to at least one owned module, so this must fail closed
    with structured exit 2."""
    with tempfile.TemporaryDirectory() as tmp:
        tampered = Path(tmp) / "workstream-bypass.sqlite"
        shutil.copyfile(canonical_db, tampered)
        conn = sqlite3.connect(tampered)
        try:
            row = conn.execute(
                "SELECT raw_json FROM clients WHERE client_id = ?", ("jmd-menswear",)
            ).fetchone()
            doc = json.loads(row[0])
            workstreams = list(doc.get("workstreams") or [])
            check(
                all(w.get("id") != "bypass" for w in workstreams if isinstance(w, dict)),
                "precondition: 'bypass' must not already be a declared workstream",
            )
            workstreams.append({"id": "bypass", "status": "active"})  # client-only; no module carries it
            doc["workstreams"] = workstreams
            conn.execute(
                "UPDATE clients SET raw_json = ? WHERE client_id = ?",
                (json.dumps(doc), "jmd-menswear"),
            )
            conn.commit()
        finally:
            conn.close()
        code, out, err = run_cli(
            ["check-copy", "--client", "jmd-menswear", "--workstream", "bypass",
             "--text", "Add to cart today", "--source", "sqlite", "--sqlite-path", str(tampered)]
        )
        check(
            code == 2,
            f"client-only workstream bypass must fail closed with exit 2, got {code} (out={out!r})",
        )
        check("error" in json.loads(err), "client-only workstream bypass must be structured")


def test_error_yaml_root_not_a_repo() -> None:
    """The default `--source yaml --root .` pointed at a non-checkout (e.g. an
    installed consumer's own repo) must fail closed, not return an empty and
    vacuously clean result (Codex Reviewer A/B)."""
    with tempfile.TemporaryDirectory() as tmp:
        code, out, err = run_cli(["list-clients", "--root", tmp])
        check(code == 2, f"non-checkout root must exit 2, got {code} (out={out!r})")
        check("error" in json.loads(err), "non-checkout root must be structured")


def test_sqlite_provenance_not_ambient(ds_yaml: svc.Dataset, ds_sqlite: svc.Dataset) -> None:
    """SQLite `_meta.repo_commit` must never be the ambient working directory's Git
    HEAD — the export carries no provenance, so it must be null (Codex Reviewer
    A/B, Integration Auditor). The YAML backend derives it from its real root."""
    check(
        svc._meta(ds_sqlite)["repo_commit"] is None,
        "sqlite repo_commit must be null (no ambient-cwd git leakage)",
    )
    # The YAML dataset has a concrete root; its commit (if git is present) comes
    # from that root, and must equal the ontology checkout HEAD — never derived
    # from a rootless dataset.
    yaml_commit = svc._meta(ds_yaml)["repo_commit"]
    check(
        yaml_commit is None or (isinstance(yaml_commit, str) and len(yaml_commit) == 40),
        f"yaml repo_commit must be a real commit or null, got {yaml_commit!r}",
    )


def test_installed_consumer_snapshot_contract(canonical_db: Path) -> None:
    """Prove the documented installed-consumer contract from OUTSIDE the ontology
    repo (Codex Reviewer B): run the console command's module in a foreign working
    directory and show (a) a pinned SQLite snapshot enforces the blocking rule with
    exit 1 and needs no `clients/` checkout and no Ruby, and (b) the ambient
    `--source yaml --root .` default fails closed there instead of silently
    passing."""
    import os
    import subprocess

    cli_path = REPO_ROOT / "scripts" / "ontology_cli.py"
    with tempfile.TemporaryDirectory() as consumer:
        # Copy the pinned snapshot into the consumer repo (as a SessionStart hook
        # would), then run the CLI with the consumer dir as cwd — no ontology
        # checkout present.
        pinned = Path(consumer) / "ontology.sqlite"
        shutil.copyfile(canonical_db, pinned)
        env = {**os.environ, "PATH": os.environ.get("PATH", "")}
        # Force the sqlite backend to prove no Ruby is needed even if ruby exists;
        # blank PATH would also disable git, which is fine for a sqlite read.
        env["ONTOLOGY_TEST_MARKER"] = "1"

        # (a) Pinned-snapshot enforcement: blocking violation -> exit 1.
        enforce = subprocess.run(
            [sys.executable, str(cli_path), "check-copy", "--client", "jmd-menswear",
             "--source", "sqlite", "--sqlite-path", str(pinned), "--text", "Add to cart today"],
            cwd=consumer, capture_output=True, text=True, env=env,
        )
        check(
            enforce.returncode == 1,
            f"pinned-snapshot check must exit 1 from a consumer repo, got {enforce.returncode} "
            f"(stderr={enforce.stderr!r})",
        )
        ids = [v["rule_id"] for v in json.loads(enforce.stdout)["violations"]]
        check(
            "jmd-menswear.website.showroom-not-ecommerce" in ids,
            f"pinned-snapshot check must flag the blocking rule, got {ids}",
        )
        # The provenance stamp must NOT borrow the consumer repo's git state.
        check(
            json.loads(enforce.stdout)["_meta"]["repo_commit"] is None,
            "pinned-snapshot response must carry null repo_commit (no consumer-repo leakage)",
        )

        # (b) Default yaml/--root . in a consumer repo (no clients/) fails closed.
        ambient = subprocess.run(
            [sys.executable, str(cli_path), "check-copy", "--client", "jmd-menswear",
             "--text", "Add to cart today"],
            cwd=consumer, capture_output=True, text=True, env=env,
        )
        check(
            ambient.returncode == 2,
            f"ambient yaml/--root . in a consumer repo must fail closed with exit 2, "
            f"got {ambient.returncode} (stdout={ambient.stdout!r})",
        )
        check("error" in json.loads(ambient.stderr), "ambient failure must be structured")


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
    """Answer a competency question through the PUBLIC runtime operation a consumer
    calls (``get_projection``), then apply the runner's OWN filter/select/normalize
    helpers — so no query semantics and no expected values are re-encoded here.

    Routing through ``get_projection`` (not private helpers) is deliberate: it
    means a regression in the public operation's projection resolution, scope
    isolation, or resource views is caught by the competency parity check, instead
    of the test quietly re-deriving the answer around the operation it claims to
    verify (Integration Auditor finding 4)."""
    query = question["query"]
    op = query["op"]
    client_id, projection_id = question["client_id"], question["projection"]
    # PUBLIC operation. get_projection fails closed on an unknown projection and
    # resolves only within the projection's own client; assert that client matches
    # the question so cross-client resolution can never masquerade as a pass.
    resolved = svc.get_projection(ds, projection_id)
    check(
        resolved["client_id"] == client_id,
        f"get_projection({projection_id}) client {resolved['client_id']!r} != question client {client_id!r}",
    )
    if op == "projection_resources":
        # get_projection already returns the includes sorted (its public contract).
        return {
            "modules": resolved["includes"]["modules"],
            "entities": resolved["includes"]["entities"],
            "rules": resolved["includes"]["rules"],
        }
    source = resolved["resolved"]["entities"] if op == "entities" else resolved["resolved"]["rules"]
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
            ("error_unknown_workstream_no_bypass", lambda: test_error_unknown_workstream_no_bypass()),
            ("error_foreign_partial_sqlite_no_bypass", lambda: test_error_foreign_partial_sqlite_no_bypass()),
            ("error_drifted_sqlite_suppressed_rule", lambda: test_error_drifted_sqlite_suppressed_rule(canonical_db)),
            ("error_forged_rawid_mismatch", lambda: test_error_forged_rawid_mismatch(canonical_db)),
            ("error_sqlite_same_id_rule_tampering", lambda: test_error_sqlite_same_id_rule_tampering(canonical_db)),
            ("error_sqlite_correlated_module_deletion", lambda: test_error_sqlite_correlated_module_deletion(canonical_db)),
            ("error_sqlite_embedded_owner_drift", lambda: test_error_sqlite_embedded_owner_drift(canonical_db)),
            ("error_sqlite_projection_includes_tampering", lambda: test_error_sqlite_projection_includes_tampering(canonical_db)),
            ("error_sqlite_client_only_workstream_bypass", lambda: test_error_sqlite_client_only_workstream_bypass(canonical_db)),
            ("error_yaml_root_not_a_repo", lambda: test_error_yaml_root_not_a_repo()),
            ("sqlite_provenance_not_ambient", lambda: test_sqlite_provenance_not_ambient(ds_yaml, ds_sqlite)),
            ("installed_consumer_snapshot_contract", lambda: test_installed_consumer_snapshot_contract(canonical_db)),
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

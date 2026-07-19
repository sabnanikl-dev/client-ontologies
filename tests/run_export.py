#!/usr/bin/env python3
"""Exercise the validate -> export runtime path against the `valid` fixture.

Proves two things on every run:
  1. tests/fixtures/valid is a complete, passing ontology (validation finds no errors).
  2. scripts/export_sqlite.py turns it into a SQLite projection whose tables and
     row counts match the fixture's known shape.

The database is written to a throwaway temp file, never the repo's build/ dir.
Exits non-zero on any validation error, missing table, or count mismatch.

Run from the repo root:  python3 tests/run_export.py
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import validate_ontology as v  # noqa: E402
import export_sqlite as e  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "valid"

# Tables export_sqlite.py is contracted to create, and the exact row count each
# must hold for the `valid` fixture (1 manifest; 1 client, 1 module w/ 2 entities
# + 1 relationship + 1 rule, 1 projection; 3 source registries; 4 evidence refs).
EXPECTED_COUNTS = {
    "manifests": 1,
    "clients": 1,
    "modules": 1,
    "entities": 2,
    "relationships": 1,
    "rules": 1,
    "projections": 1,
    "sources": 3,
    "evidence": 4,
}


def check_shared_enumeration() -> list[str]:
    """Prove the validator and exporter enumerate the exact same file set.

    Both scripts import iter_yaml from the single shared loader, so the function
    object is identical and the enumerated paths must match for any root. This is
    the guard against the pre-#21 drift where the exporter skipped manifests.
    """
    problems: list[str] = []
    if v.iter_yaml is not e.iter_yaml:
        problems.append(
            "validator and exporter use different iter_yaml functions "
            "(enumeration is not shared)"
        )
    v_paths = set(v.iter_yaml(FIXTURE))
    e_paths = set(e.iter_yaml(FIXTURE))
    if v_paths != e_paths:
        problems.append(
            f"enumeration mismatch: validator-only={sorted(str(p) for p in v_paths - e_paths)}, "
            f"exporter-only={sorted(str(p) for p in e_paths - v_paths)}"
        )
    manifest = FIXTURE / "clients" / "demo" / "ontology.yaml"
    if manifest not in v_paths:
        problems.append(f"shared enumeration does not include the manifest {manifest}")
    return problems


def main() -> int:
    failures: list[str] = []

    enumeration_problems = check_shared_enumeration()
    if enumeration_problems:
        failures.extend(enumeration_problems)
    else:
        print("ok: validator and exporter share one file enumeration (manifest included)")

    errors = v.validate(FIXTURE)
    if errors:
        joined = "\n    ".join(errors)
        failures.append(f"valid fixture failed validation:\n    {joined}")
    else:
        print("ok: valid fixture passed validation")

    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "demo.sqlite"
        try:
            e.export(FIXTURE, output)
        except Exception as exc:  # noqa: BLE001 - surface any export failure as a test failure
            failures.append(f"export raised: {exc}")
            output = None

        if output is not None:
            conn = sqlite3.connect(output)
            try:
                present = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
                for table, expected in EXPECTED_COUNTS.items():
                    if table not in present:
                        failures.append(f"export: table {table!r} missing")
                        continue
                    actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if actual != expected:
                        failures.append(
                            f"export: table {table!r} has {actual} rows, expected {expected}"
                        )
                    else:
                        print(f"ok: {table} -> {actual} row(s)")
            finally:
                conn.close()

    if failures:
        print("\nEXPORT TEST FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1
    print("\nvalid fixture validates and exports with expected counts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

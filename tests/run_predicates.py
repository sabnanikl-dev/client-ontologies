#!/usr/bin/env python3
"""Deterministic predicate-vocabulary sync test.

The controlled predicate enum in schemas/module.schema.json ($defs.predicateName)
is the single source of truth for relationship predicates. This test fails if:

  1. any scripts/validate_ontology.py PREDICATE_CONSTRAINTS key is absent from the
     schema enum (a constraint on a predicate the schema no longer accepts), or
  2. any `inverse` name configured on a live relationship is absent from the enum
     (an inverse that does not name a real vocabulary predicate).

Keeping these in lockstep prevents schema/validator drift. The validator also
runs check (1) at runtime; this test additionally scans live inverse usage and
guards the invariant independently in CI.

Run from the repo root:  python3 tests/run_predicates.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import validate_ontology as v  # noqa: E402
from ontology_loader import iter_yaml, parse_yaml  # noqa: E402


def schema_predicate_enum() -> set[str]:
    data = json.loads((ROOT / "schemas" / "module.schema.json").read_text(encoding="utf-8"))
    return set(data["$defs"]["predicateName"]["enum"])


def main() -> int:
    vocab = schema_predicate_enum()
    failures: list[str] = []

    if not vocab:
        failures.append("schemas/module.schema.json $defs.predicateName.enum is empty or missing")

    for key in v.PREDICATE_CONSTRAINTS:
        if key not in vocab:
            failures.append(f"PREDICATE_CONSTRAINTS key {key!r} is absent from the schema predicate enum")

    inverse_count = 0
    for path in iter_yaml(ROOT):
        data = parse_yaml(path)
        if data.get("kind") != "ontology_module":
            continue
        for rel in data.get("relationships", []) or []:
            if not isinstance(rel, dict):
                continue
            inverse = rel.get("inverse")
            if inverse is None:
                continue
            inverse_count += 1
            if inverse not in vocab:
                failures.append(
                    f"{path.relative_to(ROOT)}: relationship {rel.get('id')} inverse {inverse!r} "
                    f"is absent from the schema predicate enum"
                )

    if failures:
        print("PREDICATE SYNC FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1

    print(
        f"predicate sync ok: {len(v.PREDICATE_CONSTRAINTS)} constrained predicate(s) and "
        f"{inverse_count} live inverse name(s) all in the {len(vocab)}-term schema vocabulary"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

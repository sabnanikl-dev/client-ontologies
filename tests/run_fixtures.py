#!/usr/bin/env python3
"""Drive scripts/validate_ontology.py against invalid fixtures.

Each fixture under tests/fixtures/<case>/ is a minimal repo root that must fail
validation for a specific reason. This proves the JSON Schema enforcement (and
the unknown-kind guard) actually rejects malformed ontology files. Exits non-zero
if any fixture unexpectedly passes or fails with the wrong message.

Run from the repo root:  python3 tests/run_fixtures.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import validate_ontology as v  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

# (fixture directory, substring expected in at least one reported error)
CASES = [
    # Schema-layer (shape) rejections.
    ("missing-required-field", "missing required field 'entities'"),
    ("malformed-id", "does not match pattern"),
    ("bad-enum", "not one of"),
    ("unknown-kind", "unknown or missing kind"),
    ("unknown-field", "unknown field not permitted"),
    ("malformed-includes", "expected type object"),
    ("malformed-include-item", "expected type string"),
    ("malformed-manifest-path", "expected type string"),
    ("machine-check-bad-payload", "machine_check: matched 0 oneOf branches"),
    # Cross-reference, evidence, and secret-scan rejections.
    ("invalid-regex-policy", "invalid regex_policy pattern"),
    ("missing-evidence", "active/approved entity lacks evidence"),
    ("dangling-relationship", "references unknown object"),
    ("unknown-module", "projection references unknown module"),
    ("duplicate-id", "duplicate ID across ontology files"),
    ("secret-pattern", "possible secret pattern matched"),
]


def main() -> int:
    failures: list[str] = []
    for name, expected in CASES:
        root = FIXTURES / name
        errors = v.validate(root)
        if not errors:
            failures.append(f"{name}: expected validation to FAIL, but it passed")
        elif not any(expected in err for err in errors):
            joined = "\n    ".join(errors)
            failures.append(f"{name}: no error contained {expected!r}; got:\n    {joined}")
        else:
            print(f"ok: {name} -> error containing {expected!r}")

    if failures:
        print("\nFIXTURE TEST FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1
    print(f"\nall {len(CASES)} fixture cases failed as expected")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

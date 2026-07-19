#!/usr/bin/env python3
"""Deterministic regression tests for scripts/check_rules.py.

Two layers, both dependency-light (no test framework):

  1. Integration against the real canonical YAML in clients/ — locks the issue
     #11 acceptance behaviours (the JMD blocking case, the Femme warning case and
     its --fail-on tightening, and a clean pass).
  2. Unit checks on the pure engine functions (evaluate_rule / compute_exit) —
     covers required_terms, regex_policy, the --fail-on threshold, and the rule
     that draft/proposed rules are advisory-only and never change the exit code.
     These use synthetic rule dicts so they stay deterministic regardless of the
     live client copy.

Run from the repo root:  python3 tests/run_checks.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_rules as c  # noqa: E402
import validate_ontology as v  # noqa: E402

JMD_RULE = "jmd-menswear.website.showroom-not-ecommerce"
FEMME_RULE = "femme-events.brand.no-corporate-tone"

INVALID_REGEX_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "invalid-regex-policy"


def _rule_ids(violations):
    return {v["rule_id"] for v in violations}


def integration_cases() -> list[str]:
    """Exercise check_text against the real repo data for the acceptance cases."""
    failures: list[str] = []

    # Failing sample: an enforceable blocking rule -> reported and exit 1.
    violations, code = c.check_text(REPO_ROOT, "jmd-menswear", "Add to cart today")
    if JMD_RULE not in _rule_ids(violations):
        failures.append(f"jmd cart: expected {JMD_RULE} in violations, got {_rule_ids(violations)}")
    if code != 1:
        failures.append(f"jmd cart: expected exit 1, got {code}")

    # Passing sample: safe showroom copy -> no violations, exit 0.
    violations, code = c.check_text(REPO_ROOT, "jmd-menswear", "Recently on the floor; call or visit today")
    if violations:
        failures.append(f"jmd safe copy: expected no violations, got {_rule_ids(violations)}")
    if code != 0:
        failures.append(f"jmd safe copy: expected exit 0, got {code}")

    # Advisory/threshold: a warning rule -> reported, exit 0 by default, exit 1 under --fail-on warning.
    violations, code = c.check_text(REPO_ROOT, "femme-events", "We are a world-class luxury firm")
    if FEMME_RULE not in _rule_ids(violations):
        failures.append(f"femme luxury: expected {FEMME_RULE} in violations, got {_rule_ids(violations)}")
    if code != 0:
        failures.append(f"femme luxury: expected exit 0 (warning), got {code}")
    _, code_strict = c.check_text(REPO_ROOT, "femme-events", "We are a world-class luxury firm", fail_on="warning")
    if code_strict != 1:
        failures.append(f"femme luxury --fail-on warning: expected exit 1, got {code_strict}")

    # Projection scope: the website-build projection includes the showroom rule.
    violations, code = c.check_text(REPO_ROOT, "jmd-menswear", "Add to cart today", projection_id="jmd-menswear.website-build")
    if JMD_RULE not in _rule_ids(violations) or code != 1:
        failures.append(f"jmd projection scope: expected {JMD_RULE} and exit 1, got {_rule_ids(violations)} / {code}")

    if not failures:
        print("ok: integration acceptance cases (jmd blocking, femme warning+threshold, projection scope)")
    return failures


def unit_cases() -> list[str]:
    """Exercise the pure engine functions with synthetic rules."""
    failures: list[str] = []

    def rule(status, severity, machine_check, rid="demo.x"):
        return {"id": rid, "status": status, "severity": severity, "statement": "s", "machine_check": machine_check}

    # disallowed_terms: reports matched terms.
    v = c.evaluate_rule(rule("active", "blocking", {"type": "disallowed_terms", "disallowed_terms": ["Cart", "buy"]}), "add to CART now")
    if not v or v["matched"] != ["Cart"]:
        failures.append(f"disallowed_terms: expected matched ['Cart'], got {v}")

    # required_terms: violation lists the missing terms; present terms do not violate.
    v = c.evaluate_rule(rule("active", "blocking", {"type": "required_terms", "required_terms": ["disclaimer", "brand"]}), "this has a BRAND only")
    if not v or v["matched"] != ["disclaimer"]:
        failures.append(f"required_terms missing: expected matched ['disclaimer'], got {v}")
    v = c.evaluate_rule(rule("active", "blocking", {"type": "required_terms", "required_terms": ["brand"]}), "our BRAND")
    if v is not None:
        failures.append(f"required_terms satisfied: expected None, got {v}")

    # regex_policy deny: matching text violates; regex_policy allow: absence violates.
    v = c.evaluate_rule(rule("active", "blocking", {"type": "regex_policy", "pattern": r"\$\d+", "policy": "deny"}), "only $5 today")
    if not v or v["matched"] != ["$5"]:
        failures.append(f"regex deny: expected matched ['$5'], got {v}")
    v = c.evaluate_rule(rule("active", "blocking", {"type": "regex_policy", "pattern": r"\bapproved\b", "policy": "allow"}), "no marker here")
    if v is None:
        failures.append("regex allow: expected violation when required pattern absent")
    v = c.evaluate_rule(rule("active", "blocking", {"type": "regex_policy", "pattern": r"\bapproved\b", "policy": "allow"}), "this is approved")
    if v is not None:
        failures.append(f"regex allow satisfied: expected None, got {v}")

    # Future/unknown types are skipped, never executed.
    v = c.evaluate_rule(rule("active", "blocking", {"type": "status_transition", "from": "a", "to": "b"}), "anything")
    if v is not None:
        failures.append(f"unknown type: expected None (skipped), got {v}")

    # compute_exit matrix: enforceability x severity x --fail-on threshold.
    def viol(status, severity):
        return {"rule_id": "r", "status": status, "severity": severity, "matched": [], "statement": "s"}

    matrix = [
        # (violation, fail_on, expected_exit)
        (viol("active", "blocking"), "blocking", 1),
        (viol("approved", "blocking"), "blocking", 1),
        (viol("prohibited", "blocking"), "blocking", 1),
        (viol("active", "warning"), "blocking", 0),
        (viol("active", "warning"), "warning", 1),
        (viol("active", "info"), "warning", 0),
        # Advisory statuses never change the exit, even at blocking severity.
        (viol("draft", "blocking"), "blocking", 0),
        (viol("draft", "blocking"), "warning", 0),
        (viol("proposed", "blocking"), "warning", 0),
    ]
    for v_dict, fail_on, expected in matrix:
        got = c.compute_exit([v_dict], fail_on)
        if got != expected:
            failures.append(
                f"compute_exit({v_dict['status']}/{v_dict['severity']}, fail_on={fail_on}): expected {expected}, got {got}"
            )

    if not failures:
        print("ok: unit cases (disallowed/required/regex matching, advisory + threshold exit matrix)")
    return failures


def regex_validation_guard_cases() -> list[str]:
    """Malformed canonical regex_policy patterns must be caught by validation, so
    the CLI never has to discover them at runtime (which would exit 2 mid-check).

    Asserts the gate order: validate_ontology rejects an uncompilable pattern up
    front; evaluate_rule's runtime re.error -> CheckError is only a backstop for
    data that somehow bypassed validation.
    """
    failures: list[str] = []

    # Gate: validation rejects the uncompilable pattern before any CLI run.
    errors = v.validate(INVALID_REGEX_FIXTURE)
    if not any("invalid regex_policy pattern" in e for e in errors):
        failures.append(
            f"invalid-regex-policy: expected validation to report an invalid "
            f"regex_policy pattern, got: {errors}"
        )

    # Backstop: if bad regex data ever slips past validation, the engine raises a
    # CheckError rather than leaking a raw re.error.
    bad_rule = {
        "id": "demo.brand.no-bare-bracket",
        "status": "active",
        "severity": "blocking",
        "statement": "s",
        "machine_check": {"type": "regex_policy", "pattern": "[", "policy": "deny"},
    }
    try:
        c.evaluate_rule(bad_rule, "anything")
    except c.CheckError:
        pass
    except Exception as exc:  # noqa: BLE001
        failures.append(f"regex backstop: expected CheckError, got {type(exc).__name__}: {exc}")
    else:
        failures.append("regex backstop: expected CheckError for uncompilable pattern, got no exception")

    if not failures:
        print("ok: regex_policy validation gate (validation rejects; runtime CheckError is backstop only)")
    return failures


def main() -> int:
    failures = integration_cases() + unit_cases() + regex_validation_guard_cases()
    if failures:
        print("\nCHECK TEST FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1
    print("\nall check_rules cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

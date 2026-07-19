#!/usr/bin/env python3
"""Deterministic regression tests for scripts/check_evidence.py.

Three layers, all dependency-light (no test framework, stdlib tempfiles only):

  1. Unit checks on the pure utf8-lf-v1 helpers (parse_line_spec / to_logical_lines
     / select_span / compute_span_hash / parse_content_hash).
  2. Per-citation classification (check_citation) against real temp source files,
     covering every category the issue #23 contract requires: verified_match,
     content_drift (text changed inside the cited span AND lines inserted before
     the range), CRLF/LF equivalence (no false drift), invalid_range, source_missing,
     unresolvable_in_environment (unavailable path), unsupported_hash_version, and
     anchor_missing.
  3. An end-to-end collect_results / exit-code pass over a tiny temp repo tree,
     proving --strict fails on genuine drift but stays advisory (exit 0) for an
     unavailable external absolute path.

Run from the repo root:  python3 tests/run_evidence.py
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import check_evidence as ce  # noqa: E402


def _hash(text: str, spec: str) -> str:
    return ce.compute_span_hash(text, spec)


def unit_cases() -> list[str]:
    failures: list[str] = []

    # parse_line_spec: single, range, multi-range in written order; rejects junk.
    if ce.parse_line_spec("3") != [3]:
        failures.append("parse_line_spec single failed")
    if ce.parse_line_spec("2-4") != [2, 3, 4]:
        failures.append("parse_line_spec range failed")
    if ce.parse_line_spec("5-6,1-2") != [5, 6, 1, 2]:
        failures.append("parse_line_spec multi-range order failed")
    for bad in ["", "0", "3-1", "a", "1-", "-2", "1,,2", "1-2-3"]:
        try:
            ce.parse_line_spec(bad)
        except ValueError:
            pass
        else:
            failures.append(f"parse_line_spec({bad!r}) should have raised ValueError")

    # to_logical_lines: CRLF and CR normalize to LF.
    if ce.to_logical_lines("a\r\nb\rc\n") != ["a", "b", "c", ""]:
        failures.append("to_logical_lines newline normalization failed")

    # select_span: joins with \n, no trailing newline; out-of-range raises.
    if ce.select_span(["a", "b", "c"], [1, 3]) != "a\nc":
        failures.append("select_span join failed")
    try:
        ce.select_span(["a", "b"], [3])
    except IndexError:
        pass
    else:
        failures.append("select_span out-of-range should raise IndexError")

    # compute_span_hash: stable, versioned, and independent of line-ending style.
    h_lf = _hash("l1\nl2\nl3\n", "1-2")
    h_crlf = _hash("l1\r\nl2\r\nl3\r\n", "1-2")
    if h_lf != h_crlf:
        failures.append("compute_span_hash: CRLF/LF produced different hashes")
    if not ce.CONTENT_HASH_RE.match(h_lf):
        failures.append(f"compute_span_hash produced non-conforming anchor: {h_lf}")

    # parse_content_hash / is_supported_hash.
    good = "sha256:utf8-lf-v1:" + ("a" * 64)
    if not ce.is_supported_hash(good):
        failures.append("is_supported_hash rejected a valid anchor")
    for bad in ["sha256:utf8-lf-v2:" + ("a" * 64), "md5:utf8-lf-v1:" + ("a" * 64),
                "sha256:utf8-lf-v1:" + ("a" * 63), "not-a-hash", "sha256:" + ("a" * 64)]:
        if ce.is_supported_hash(bad):
            failures.append(f"is_supported_hash accepted an invalid anchor: {bad}")

    if not failures:
        print("ok: unit cases (line spec, newline normalization, span hashing, anchor parsing)")
    return failures


def citation_cases() -> list[str]:
    """check_citation over real temp source files: one temp dir acts as repo root."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        text = "alpha\nbravo\ncharlie\ndelta\n"
        src = root / "docs" / "sot.md"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text(text, encoding="utf-8")
        source = {"id": "s", "type": "git_repo_file", "path": "docs/sot.md"}

        def check(ref):
            return ce.check_citation(ref, source, root)["category"]

        good_hash = _hash(text, "2-3")

        # verified_match: correct anchor over the cited span.
        cat = check({"source_id": "s", "lines": "2-3", "content_hash": good_hash})
        if cat != "verified_match":
            failures.append(f"verified_match: got {cat}")

        # content_drift: text changed *inside* the cited span.
        src.write_text("alpha\nBRAVO-EDITED\ncharlie\ndelta\n", encoding="utf-8")
        cat = check({"source_id": "s", "lines": "2-3", "content_hash": good_hash})
        if cat != "content_drift":
            failures.append(f"content_drift (in-span edit): got {cat}")

        # content_drift: a line inserted *before* the range shifts the cited lines.
        src.write_text("INSERTED\nalpha\nbravo\ncharlie\ndelta\n", encoding="utf-8")
        cat = check({"source_id": "s", "lines": "2-3", "content_hash": good_hash})
        if cat != "content_drift":
            failures.append(f"content_drift (insertion before range): got {cat}")

        # CRLF/LF equivalence: rewrite the original with CRLF -> still verified_match.
        src.write_bytes("alpha\r\nbravo\r\ncharlie\r\ndelta\r\n".encode("utf-8"))
        cat = check({"source_id": "s", "lines": "2-3", "content_hash": good_hash})
        if cat != "verified_match":
            failures.append(f"CRLF/LF equivalence: expected verified_match, got {cat}")

        # invalid_range: lines out of bounds for the source.
        cat = check({"source_id": "s", "lines": "99-100", "content_hash": good_hash})
        if cat != "invalid_range":
            failures.append(f"invalid_range (out of bounds): got {cat}")
        # invalid_range: content_hash present but no lines to hash.
        cat = check({"source_id": "s", "content_hash": good_hash})
        if cat != "invalid_range":
            failures.append(f"invalid_range (missing lines): got {cat}")

        # unsupported_hash_version: wrong version tag is a static defect.
        cat = check({"source_id": "s", "lines": "2-3",
                     "content_hash": "sha256:utf8-lf-v2:" + ("a" * 64)})
        if cat != "unsupported_hash_version":
            failures.append(f"unsupported_hash_version: got {cat}")

        # anchor_missing: resolvable source but no content_hash to verify.
        cat = check({"source_id": "s", "lines": "2-3"})
        if cat != "anchor_missing":
            failures.append(f"anchor_missing: got {cat}")

        # source_missing: a repo-relative path that does not exist.
        missing_source = {"id": "m", "type": "git_repo_file", "path": "docs/nope.md"}
        cat = ce.check_citation({"source_id": "m", "lines": "1-1", "content_hash": good_hash},
                                missing_source, root)["category"]
        if cat != "source_missing":
            failures.append(f"source_missing (repo path absent): got {cat}")

        # unresolvable_in_environment: an unavailable external absolute path.
        ext_source = {"id": "e", "type": "local_project_doc",
                      "path": "/nonexistent-machine-only/Users/creator/x/sot.md"}
        cat = ce.check_citation({"source_id": "e", "lines": "1-1", "content_hash": good_hash},
                                ext_source, root)["category"]
        if cat != "unresolvable_in_environment":
            failures.append(f"unresolvable_in_environment (external absent): got {cat}")

        # An unreachable external source is never reported as verified, even with a
        # (would-be-correct) anchor.
        if cat == "verified_match":
            failures.append("unreachable external source was reported verified")

    if not failures:
        print("ok: citation cases (match, in-span + pre-range drift, CRLF eq, invalid, "
              "missing, unresolvable, unsupported, anchor_missing)")
    return failures


def integration_cases() -> list[str]:
    """collect_results + --strict exit behavior over a tiny temp repo tree."""
    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        module_dir = root / "clients" / "demo" / "modules"
        module_dir.mkdir(parents=True, exist_ok=True)
        # A repo-relative source file that a module cites with a correct anchor.
        source_text = "one\ntwo\nthree\nfour\n"
        (root / "clients" / "demo" / "sources").mkdir(parents=True, exist_ok=True)
        src_rel = "clients/demo/sources/note.md"
        (root / src_rel).write_text(source_text, encoding="utf-8")
        good = _hash(source_text, "2-3")

        # Minimal YAML the shared loader can parse (Ruby stdlib YAML).
        (root / "clients" / "demo" / "client.yaml").write_text(
            "kind: client\nid: demo\nsource_registry: []\n", encoding="utf-8")
        (root / "clients" / "demo" / "ontology.yaml").write_text(
            "kind: ontology\nid: demo\nclient_id: demo\nmodules: []\nprojections: []\n",
            encoding="utf-8")

        def write_module(anchor_hash: str) -> None:
            (module_dir / "core.yaml").write_text(
                "kind: ontology_module\n"
                "id: demo.core\n"
                "client_id: demo\n"
                "evidence_sources:\n"
                "  - id: note\n"
                "    type: git_repo_file\n"
                f"    path: \"{src_rel}\"\n"
                "  - id: external\n"
                "    type: local_project_doc\n"
                "    path: \"/nonexistent-machine-only/x/private.md\"\n"
                "entities:\n"
                "  - id: demo.core.thing\n"
                "    evidence:\n"
                "      - source_id: note\n"
                "        lines: \"2-3\"\n"
                f"        content_hash: \"{anchor_hash}\"\n"
                "      - source_id: external\n"
                "        lines: \"1-1\"\n"
                f"        content_hash: \"{anchor_hash}\"\n",
                encoding="utf-8")

        # Clean anchor: repo source verifies; external is advisory -> strict exit 0.
        write_module(good)
        results = ce.collect_results(root, client_id="demo")
        cats = sorted(r["category"] for r in results)
        if cats != ["unresolvable_in_environment", "verified_match"]:
            failures.append(f"integration clean: unexpected categories {cats}")
        if ce.compute_exit(results, strict=True) != 0:
            failures.append("integration clean: --strict should exit 0 (external is advisory)")
        if ce.compute_exit(results, strict=False) != 0:
            failures.append("integration clean: non-strict should exit 0")

        # Drift the repo source: strict must now fail, but non-strict stays 0.
        (root / src_rel).write_text("one\nTWO-CHANGED\nthree\nfour\n", encoding="utf-8")
        results = ce.collect_results(root, client_id="demo")
        drift = [r for r in results if r["category"] == "content_drift"]
        if not drift:
            failures.append("integration drift: expected a content_drift result")
        if ce.compute_exit(results, strict=True) != 1:
            failures.append("integration drift: --strict should exit 1 on genuine drift")
        if ce.compute_exit(results, strict=False) != 0:
            failures.append("integration drift: non-strict must never gate (exit 0)")

        # The external unavailable citation stays unresolvable even amid drift.
        if not any(r["category"] == "unresolvable_in_environment" for r in results):
            failures.append("integration drift: external citation should remain unresolvable")

    if not failures:
        print("ok: integration cases (collect_results, strict fails on drift, external advisory)")
    return failures


def main() -> int:
    failures = unit_cases() + citation_cases() + integration_cases()
    if failures:
        print("\nEVIDENCE CHECK TEST FAILURES:", file=sys.stderr)
        for failure in failures:
            print(" - " + failure, file=sys.stderr)
        return 1
    print("\nall check_evidence cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

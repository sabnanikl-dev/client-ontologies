#!/usr/bin/env python3
"""Installed-console runtime smoke test (issue #40).

Proves the *installed* runtime consumer surface (`ontology` / `ontology-mcp`
console scripts registered by `pyproject.toml`) honors its contract from a
foreign working directory with **no Ruby on PATH** — the exact shape a pinned,
Ruby-free consumer relies on. It exercises the real console executables, not the
source-tree modules, so it belongs to the Python 3.10 installed-runtime CI lane
rather than the in-process `tests/run_cli.py` suite.

Usage:

    python3 tests/installed_smoke.py --bindir <venv/bin> --sqlite-path <snapshot.sqlite>

`--bindir` is the virtualenv scripts directory holding the installed `ontology`
and `ontology-mcp` executables (and that venv's `python`). The snapshot must be
built *before* calling this (e.g. `export_sqlite.py`, which needs Ruby); this
helper then runs each console script with a deliberately restricted `PATH`
containing only `--bindir` — so `ruby` is unresolvable — from a throwaway temp
cwd that has no `clients/` checkout.

Assertions (each stage prints a legible PASS/FAIL line so a CI log distinguishes
package-build, entry-point, Ruby-isolation, and runtime-contract failures):

  1. Both console scripts resolve as executables under `--bindir`.
  2. `ruby` is NOT resolvable under the restricted PATH (Ruby-isolation proof).
  3. `ontology check-copy --source sqlite` on the pinned snapshot flags the
     blocking `jmd-menswear.website.showroom-not-ecommerce` rule for
     "Add to cart today", exits 1, and stamps `_meta.read_mode == "sqlite"` and
     `_meta.repo_commit == null` (no consumer-repo git leakage).
  4. `ontology-mcp` fails closed: deterministic structured `{"error": ...}` on
     stderr and exit 2 (the MCP adapter is a later PR; a partial success surface
     must never appear).

Exit 0 only if every stage passes; exit 1 (with a FAIL summary) otherwise.
Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BLOCKING_RULE = "jmd-menswear.website.showroom-not-ecommerce"
COPY = "Add to cart today"

# The exact deterministic placeholder payload emitted by the `ontology-mcp`
# console entry point (`scripts/ontology_cli.py::mcp_entrypoint`). The MCP stdio
# adapter is a later PR; until then the entry point must fail closed with THIS
# stable notice — not merely *some* object carrying an "error" key. Pinning the
# value here (and asserting stderr is byte-identical across repeated invocations)
# is what makes a nondeterministic/partial MCP surface a hard CI failure.
MCP_EXPECTED_ERROR = "ontology-mcp is not yet implemented"


class SmokeError(AssertionError):
    pass


def _log_pass(msg: str) -> None:
    print(f"  PASS: {msg}")


def _restricted_env(bindir: Path) -> dict[str, str]:
    """A minimal environment whose PATH is ONLY the venv scripts dir — so the
    console scripts and their venv Python resolve, but `ruby` does not."""
    env = {k: v for k, v in os.environ.items() if k not in {"PATH", "PYTHONPATH"}}
    env["PATH"] = str(bindir)
    return env


def _check_scripts_present(bindir: Path) -> tuple[Path, Path]:
    ontology = bindir / "ontology"
    ontology_mcp = bindir / "ontology-mcp"
    for exe in (ontology, ontology_mcp):
        if not exe.is_file() or not os.access(exe, os.X_OK):
            raise SmokeError(f"installed console script missing/not executable: {exe}")
    _log_pass(f"console scripts resolve under {bindir}: ontology, ontology-mcp")
    return ontology, ontology_mcp


def _check_ruby_isolated(bindir: Path) -> None:
    if shutil.which("ruby", path=str(bindir)) is not None:
        raise SmokeError(f"ruby unexpectedly resolvable under restricted PATH={bindir}")
    _log_pass("ruby is NOT on the restricted PATH (Ruby-free consumer path proven)")


def _check_sqlite_enforcement(ontology: Path, sqlite_path: Path, env: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory() as foreign_cwd:
        proc = subprocess.run(
            [
                str(ontology), "check-copy", "--client", "jmd-menswear",
                "--source", "sqlite", "--sqlite-path", str(sqlite_path),
                "--text", COPY,
            ],
            cwd=foreign_cwd, capture_output=True, text=True, env=env,
        )
    if proc.returncode != 1:
        raise SmokeError(
            f"installed sqlite check-copy must exit 1 for a blocking violation, "
            f"got {proc.returncode} (stderr={proc.stderr!r})"
        )
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"check-copy stdout was not JSON: {exc} (stdout={proc.stdout!r})")
    ids = [v.get("rule_id") for v in payload.get("violations", [])]
    if BLOCKING_RULE not in ids:
        raise SmokeError(f"blocking rule {BLOCKING_RULE} not flagged; violations={ids}")
    meta = payload.get("_meta", {})
    if meta.get("read_mode") != "sqlite":
        raise SmokeError(f'_meta.read_mode must be "sqlite", got {meta.get("read_mode")!r}')
    if meta.get("repo_commit") is not None:
        raise SmokeError(
            f"_meta.repo_commit must be null for a SQLite snapshot (no consumer-repo "
            f"git leakage), got {meta.get('repo_commit')!r}"
        )
    _log_pass(
        f"installed sqlite check-copy flagged {BLOCKING_RULE}, exit 1, "
        f'_meta.read_mode="sqlite", _meta.repo_commit=null'
    )


def _run_mcp(ontology_mcp: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as foreign_cwd:
        return subprocess.run(
            [str(ontology_mcp)],
            cwd=foreign_cwd, capture_output=True, text=True, env=env,
        )


def _check_mcp_fails_closed(ontology_mcp: Path, env: dict[str, str]) -> None:
    # Invoke twice: a compliant placeholder is deterministic, so its stderr must be
    # byte-identical run to run. A nondeterministic/partial MCP surface (e.g. a
    # payload carrying a fresh random value each call) then fails here instead of
    # false-passing on a bare "error"-key presence check.
    first = _run_mcp(ontology_mcp, env)
    second = _run_mcp(ontology_mcp, env)
    for proc in (first, second):
        if proc.returncode != 2:
            raise SmokeError(
                f"ontology-mcp placeholder must fail closed with exit 2, got {proc.returncode} "
                f"(stdout={proc.stdout!r}, stderr={proc.stderr!r})"
            )
        if proc.stdout.strip():
            raise SmokeError(
                f"ontology-mcp must not expose a partial success surface on stdout, "
                f"got {proc.stdout!r}"
            )
    if first.stderr != second.stderr:
        raise SmokeError(
            "ontology-mcp placeholder stderr must be deterministic (byte-identical across "
            f"invocations); got {first.stderr!r} then {second.stderr!r}"
        )
    try:
        parsed = json.loads(first.stderr)
    except json.JSONDecodeError as exc:
        raise SmokeError(f"ontology-mcp stderr was not structured JSON: {exc} (stderr={first.stderr!r})")
    if not isinstance(parsed, dict) or "error" not in parsed:
        raise SmokeError(f'ontology-mcp must emit a structured {{"error": ...}}, got {parsed!r}')
    if parsed.get("error") != MCP_EXPECTED_ERROR:
        raise SmokeError(
            f'ontology-mcp must emit the exact placeholder error {MCP_EXPECTED_ERROR!r}, '
            f"got {parsed.get('error')!r} (full payload={parsed!r})"
        )
    _log_pass(
        'ontology-mcp fails closed: exact structured {"error": '
        f'"{MCP_EXPECTED_ERROR}"}} on stderr, exit 2, byte-identical across invocations'
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bindir", required=True, help="Virtualenv scripts dir with the installed console scripts")
    parser.add_argument("--sqlite-path", required=True, help="Pre-built ontology SQLite snapshot")
    args = parser.parse_args(argv)

    bindir = Path(args.bindir).resolve()
    sqlite_path = Path(args.sqlite_path).resolve()
    if not sqlite_path.is_file():
        print(f"FAIL: SQLite snapshot not found: {sqlite_path}", file=sys.stderr)
        return 1

    print(f"installed-console smoke: bindir={bindir} snapshot={sqlite_path}")
    env = _restricted_env(bindir)
    try:
        ontology, ontology_mcp = _check_scripts_present(bindir)
        _check_ruby_isolated(bindir)
        _check_sqlite_enforcement(ontology, sqlite_path, env)
        _check_mcp_fails_closed(ontology_mcp, env)
    except SmokeError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    print("\nall installed-console runtime smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

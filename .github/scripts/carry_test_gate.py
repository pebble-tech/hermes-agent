#!/usr/bin/env python3
"""Fork-vs-upstream gate for the integration build.

Runs the gate's test scope on the candidate tree (upstream main + ops-overlay + the carried
branches) and fails only on what the carries broke: a test that fails on the candidate but not
on plain upstream at the same base, or an undefined name (ruff F821/F823) the candidate adds.

  1. Each test file in scope runs in its own pytest process on the candidate.
  2. Each file with a failure runs again on the upstream base tree. A failure that also happens
     there is upstream's, not ours.
  3. Each remaining (fork-only) failure runs again alone on the candidate. One that passes alone
     is order-dependent noise (batched telegram tests are, even on upstream): reported, not fatal.
  4. ruff F821/F823 on both trees; findings are compared by file, code and message, so a line
     shift is not a new finding.

Scope: every test_*.py under each --scope directory, plus every test file the candidate adds or
changes against the base (the carries' own tests, wherever they live).

usage: carry_test_gate.py --candidate DIR --base DIR [--python PY] [--ruff CMD] [--jobs N] [--report FILE]

Exit 0 when there is no fork-only failure and no fork-only ruff finding, 1 otherwise, 2 on a
usage or setup error. Stdlib only: it runs from a copy outside the trees it checks.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DEFAULT_SCOPE = ("tests/gateway", "tests/agent")
FILE_TIMEOUT = 600
# Fork-only failures above this are not re-run one by one: that many is a real break, not noise.
ISOLATION_CAP = 300
RUFF_SELECT = "F821,F823"


def git(tree: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(tree), *args], check=True, capture_output=True, text=True, timeout=120,
    ).stdout


def test_env(tree: Path) -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "PYTHONPATH": str(tree), "TZ": "UTC", "LANG": "C.UTF-8", "PYTHONHASHSEED": "0",
        "PYTHONDONTWRITEBYTECODE": "1",
        "OPENROUTER_API_KEY": "", "OPENAI_API_KEY": "", "NOUS_API_KEY": "", "ANTHROPIC_API_KEY": "",
    })
    return env


def discover(tree: Path, scope: list[str], extra: list[str]) -> list[str]:
    files: set[str] = set()
    for root in scope:
        files.update(str(p.relative_to(tree)) for p in (tree / root).rglob("test_*.py"))
    files.update(f for f in extra if (tree / f).is_file())
    return sorted(files)


def carry_test_files(candidate: Path, base_sha: str) -> list[str]:
    out = git(candidate, "diff", "--name-only", "--diff-filter=AMR", base_sha, "HEAD", "--", "tests")
    return sorted(f for f in out.split() if Path(f).name.startswith("test_") and f.endswith(".py"))


def _node_id(file: str, classname: str, name: str) -> str:
    module = file[:-3].replace("/", ".")
    if classname == module or not classname:
        return f"{file}::{name}"
    if classname.startswith(module + "."):
        return f"{file}::{classname[len(module) + 1:].replace('.', '::')}::{name}"
    return f"{file}::{name}"


def run_file(python: str, tree: Path, target: str, workdir: Path) -> set[str]:
    """Run one test file (or node id) in its own process; return its failing node ids."""
    file = target.split("::", 1)[0]
    xml = workdir / f"{uuid.uuid4().hex}.xml"
    cmd = [python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--no-header", f"--junitxml={xml}", target]
    try:
        proc = subprocess.run(cmd, cwd=tree, env=test_env(tree), capture_output=True, text=True,
                              timeout=FILE_TIMEOUT, check=False)
    except subprocess.TimeoutExpired:
        return {f"{file}::<timeout {FILE_TIMEOUT}s>"}
    failed: set[str] = set()
    if xml.is_file():
        try:
            for case in ET.parse(xml).getroot().iter("testcase"):
                if case.find("failure") is not None or case.find("error") is not None:
                    classname, name = case.get("classname", ""), case.get("name", "")
                    # A collection error has no class and is named after the module.
                    failed.add(file if not classname else _node_id(file, classname, name))
        except ET.ParseError:
            failed.add(f"{file}::<unreadable junit>")
        xml.unlink(missing_ok=True)
    # 0 passed, 5 nothing collected (an all-skipped file); anything else with no failing case
    # is a crash, an internal error or a usage error, which is a failure of the whole file.
    if proc.returncode not in (0, 5) and not failed:
        failed.add(f"{file}::<exit {proc.returncode}>")
    return failed


def run_many(python: str, tree: Path, targets: list[str], jobs: int, workdir: Path) -> dict[str, set[str]]:
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = pool.map(lambda t: (t, run_file(python, tree, t, workdir)), targets)
        return dict(results)


def ruff_findings(ruff: list[str], tree: Path) -> collections.Counter:
    proc = subprocess.run(
        [*ruff, "check", "--isolated", "--select", RUFF_SELECT, "--output-format",
         "json", "--exit-zero", "."],
        cwd=tree, capture_output=True, text=True, timeout=600, check=False,
    )
    if proc.returncode != 0:
        raise SystemExit(f"carry-test-gate: ruff failed in {tree}: {proc.stderr.strip()}")
    found: collections.Counter = collections.Counter()
    for item in json.loads(proc.stdout or "[]"):
        rel = os.path.relpath(item["filename"], tree)
        found[(rel, item["code"], item["message"])] += 1
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--candidate", required=True, type=Path, help="integration build checkout")
    ap.add_argument("--base", required=True, type=Path, help="upstream main checkout the build is based on")
    ap.add_argument("--python", default=sys.executable, help="interpreter with the test dependencies")
    ap.add_argument("--scope", action="append", help=f"test directory (repeatable; default {' '.join(DEFAULT_SCOPE)})")
    ap.add_argument("--ruff", help="ruff command (default: <python> -m ruff)")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--report", type=Path, help="write the JSON result here")
    args = ap.parse_args()
    candidate, base = args.candidate.resolve(), args.base.resolve()
    scope = args.scope or list(DEFAULT_SCOPE)
    started = time.monotonic()

    base_sha = git(base, "rev-parse", "HEAD").strip()
    candidate_sha = git(candidate, "rev-parse", "HEAD").strip()
    if subprocess.run(["git", "-C", str(candidate), "merge-base", "--is-ancestor", base_sha, "HEAD"],
                      timeout=60, check=False).returncode != 0:
        print(f"carry-test-gate: base {base_sha} is not an ancestor of the candidate {candidate_sha}")
        return 2
    carried = carry_test_files(candidate, base_sha)
    files = discover(candidate, scope, carried)
    print(f"carry-test-gate: candidate {candidate_sha} on base {base_sha}")
    print(f"carry-test-gate: {len(files)} test files ({', '.join(scope)} + {len(carried)} carried), {args.jobs} jobs")

    with tempfile.TemporaryDirectory(prefix="carry-gate-") as tmp:
        workdir = Path(tmp)
        cand = run_many(args.python, candidate, files, args.jobs, workdir)
        cand_failed = {node for nodes in cand.values() for node in nodes}
        t_candidate = time.monotonic() - started
        print(f"carry-test-gate: candidate run {t_candidate:.0f}s, {len(cand_failed)} failing in "
              f"{sum(1 for n in cand.values() if n)} files")

        # Upstream's verdict on the same files. A file the base does not have is a carry's own file.
        on_base = [f for f, nodes in cand.items() if nodes and (base / f).is_file()]
        base_runs = run_many(args.python, base, on_base, args.jobs, workdir)
        base_failed = {node for nodes in base_runs.values() for node in nodes}
        fork_only = sorted(n for n in cand_failed
                           if n not in base_failed and n.split("::", 1)[0] not in base_failed)

        noise: list[str] = []
        broken: list[str] = fork_only
        if 0 < len(fork_only) <= ISOLATION_CAP:
            # Re-run each one alone. A file-level failure re-runs its whole file.
            alone = run_many(args.python, candidate, fork_only, args.jobs, workdir)
            noise = sorted(n for n, again in alone.items() if not again)
            broken = sorted(n for n, again in alone.items() if again)

    ruff = args.ruff.split() if args.ruff else [args.python, "-m", "ruff"]
    ruff_cand, ruff_base = ruff_findings(ruff, candidate), ruff_findings(ruff, base)
    ruff_new = sorted((ruff_cand - ruff_base).elements())

    elapsed = time.monotonic() - started
    result = {
        "candidate": candidate_sha, "base": base_sha, "scope": scope, "carried_test_files": carried,
        "files": len(files), "candidate_failures": len(cand_failed), "upstream_too": len(cand_failed) - len(fork_only),
        "fork_only": broken, "order_dependent": noise, "ruff_fork_only": [list(r) for r in ruff_new],
        "seconds": round(elapsed), "passed": not broken and not ruff_new,
    }
    lines = [
        f"## Carry test gate: {'passed' if result['passed'] else 'FAILED'}",
        f"Candidate `{candidate_sha[:12]}` on upstream `{base_sha[:12]}`: {len(files)} test files, {elapsed:.0f}s.",
        f"- failing on the candidate: {len(cand_failed)}; also failing on upstream: {result['upstream_too']}",
        f"- fork-only, failing alone too: {len(broken)}",
        f"- fork-only in the batch, passing alone (order-dependent, not fatal): {len(noise)}",
        f"- fork-only undefined names (ruff {RUFF_SELECT}): {len(ruff_new)}",
    ]
    lines += [f"  - FAIL `{n}`" for n in broken]
    lines += [f"  - noise `{n}`" for n in noise]
    lines += [f"  - RUFF `{f}` {code} {msg}" for f, code, msg in ruff_new]
    text = "\n".join(lines)
    print(text)
    for node in broken:
        print(f"::error title=carry-test-gate::fork-only test failure {node}")
    for f, code, msg in ruff_new:
        print(f"::error file={f},title=carry-test-gate::fork-only {code} {msg}")
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    if args.report:
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())

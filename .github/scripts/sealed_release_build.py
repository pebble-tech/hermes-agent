"""Build a sealed, read-only Hermes release for one pinned commit.

The recipe is upstream's native payload (scripts/bundles/native.py:
prepare_native + finish_native) run from a checkout of the pinned commit,
with three deviations:

1. extras ``all`` and ``messaging`` instead of ``all_extras=True``;
2. no shipped uv cache;
3. ``scripts/whatsapp-bridge`` is kept, with prebuilt ``node_modules`` and a
   matching ``node_modules/.hermes-pkg-hash``;
4. the optional local-inference and computer-use tools (``llamacpp-*``,
   ``cua-driver``) are left out of the tool selection, so they are never
   staged, recorded in ``facts.json`` or digested.

After assembly the script writes ``install-stamp.json`` (external updates,
explicit commit), ``release.json``, removes build lock files, makes every
file world-readable and fails if the tree mentions a build-host path.

The output must be the final install path; relocation is not relied on.

Usage (host Python 3.14, run from anywhere):

    python sealed_release_build.py --source /path/to/checkout-at-sha \\
        --sha <sha40> --out /opt/hermes/releases/<sha40> --cache /path/to/uv-cache
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

EXTRAS = ("all", "messaging")
BRIDGE = "scripts/whatsapp-bridge"
# Tools the release must carry for the gateway; the native recipe may stage more.
REQUIRED_TOOLS = ("python", "node", "npm", "uv", "ripgrep", "ffmpeg")
# Optional tools left out of the native selection (deviation 4).
EXCLUDED_TOOL_PREFIXES = ("llamacpp-",)
EXCLUDED_TOOLS = ("cua-driver",)


# Upstream call sites the inner build replaces; each must run exactly once.
WRAPPERS = ("extras", "uv-cache", "tool-selection")


def excluded_tool(name: str) -> bool:
    return name in EXCLUDED_TOOLS or name.startswith(EXCLUDED_TOOL_PREFIXES)
# Build-host variables that would leak branch/commit identity into the stamp.
STAMP_ENV_DROP = ("GITHUB_SHA", "GITHUB_REF_NAME", "GITHUB_HEAD_REF",
                  "HERMES_BUILD_COMMIT", "HERMES_PAYLOAD_TAG", "HERMES_DESKTOP_VARIANT")


def log(message: str) -> None:
    print(f"[sealed-release] {message}", flush=True)


def git(source: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=source, text=True).strip()


def require_clean_source(source: Path, sha: str) -> None:
    head = git(source, "rev-parse", "HEAD")
    if head != sha:
        raise SystemExit(f"source checkout is at {head}, expected {sha}")
    status = git(source, "status", "--porcelain", "--untracked-files=no")
    if status:
        raise SystemExit(f"source checkout has tracked changes:\n{status}")


# --- outer: validate, isolate, re-exec (mirrors native.stage_native) ---------

def outer(args) -> int:
    source, out, cache = args.source.resolve(), Path(args.out), args.cache.resolve()
    sha = args.sha
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise SystemExit("--sha must be a full 40-character commit")
    if not out.is_absolute() or out != out.resolve() or out.name != sha:
        raise SystemExit("--out must be an absolute, unsymlinked path ending in the sha")
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} is not empty; releases are built once into a fresh path")
    if source.is_relative_to(out) or out.is_relative_to(source):
        raise SystemExit("source and output must be separate trees")
    require_clean_source(source, sha)
    out.mkdir(parents=True, exist_ok=True)
    cache.mkdir(parents=True, exist_ok=True)

    base_env = dict(os.environ)
    for key, directory in (("CARGO_HOME", ".cargo"), ("RUSTUP_HOME", ".rustup")):
        base_env.setdefault(key, str(Path.home() / directory))
    # Same isolation as native.stage_native: throwaway HOME and PM state inside
    # the output, removed before packaging; tools land in the payload store.
    with tempfile.TemporaryDirectory(prefix=".build-", dir=out) as work:
        env = {**base_env, "HOME": work, "USERPROFILE": work,
               "HERMES_HOME": str(Path(work) / ".hermes"),
               "HERMES_RUNTIME_DIR": str(out / "tools"),
               "HERMES_PYTHON_SRC_ROOT": str(source),
               "XDG_CACHE_HOME": str(Path(work) / "cache"),
               "XDG_CONFIG_HOME": str(Path(work) / "config"),
               "UV_CACHE_DIR": str(cache),
               "npm_config_cache": str(cache.parent / "npm-cache"),
               "PYTHONPATH": str(source)}
        command = [sys.executable, "-B", str(Path(__file__).resolve()), "--inner",
                   "--source", str(source), "--sha", sha, "--out", str(out), "--cache", str(cache)]
        status = subprocess.run(command, cwd=source, env=env).returncode
        work_path = work
    if status:
        return status
    for leftover in out.glob(".build-*"):
        shutil.rmtree(leftover)

    check_installed_extras(out)
    write_stamp(source, out, sha)
    write_release(out, sha)
    discard_lock_files(out)
    share_tree(out)
    # Paths this build read from or wrote to. Third-party artifacts may carry
    # their own builders' paths (wheel SBOMs, man pages); those are checked
    # against their published hashes by the verify job, not here.
    check_no_host_paths(out, [str(source.parent), str(cache.parent), work_path,
                              str(Path(sys.prefix).resolve()), str(Path.home() / ".cache")])
    require_clean_source(source, sha)
    log(f"release ready at {out}")
    return 0


# Runs on the release interpreter, so marker evaluation uses the target's own
# environment. argv: declared export, all-extras export, venv site-packages.
_EXTRAS_CHECK = r"""
import sys
from pathlib import Path
from email.parser import HeaderParser
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

def locked(path):
    names = set()
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if not line or line[0] in "#-./":
            continue
        req = Requirement(line)
        if req.marker is None or req.marker.evaluate({"extra": ""}):
            names.add(canonicalize_name(req.name))
    return names

declared, everything = locked(sys.argv[1]), locked(sys.argv[2])
installed = {canonicalize_name(HeaderParser().parse(open(meta, encoding="utf-8"))["Name"])
             for meta in Path(sys.argv[3]).glob("*.dist-info/METADATA")}
project = {canonicalize_name("hermes-agent")}
missing, extra = sorted(declared - installed), sorted(installed - declared - project)
undeclared_only = everything - declared
leaked = sorted(installed & undeclared_only)
print(f"venv {len(installed)} dists; lock({sys.argv[4]}) {len(declared)} + hermes-agent; "
      f"missing {len(missing)}, extra {len(extra)}; undeclared-extra-only packages {len(undeclared_only)}, installed {len(leaked)}; "
      f"mautrix (matrix extra) {'installed' if 'mautrix' in installed else 'absent'}")
if missing or extra or leaked or not project <= installed:
    sys.exit(f"venv does not match the declared extras: missing {missing}, extra {extra}, "
             f"undeclared-extra packages {leaked}")
"""


def check_installed_extras(out: Path) -> None:
    """The venv must hold exactly the lock's closure for the declared extras.

    uv exports the frozen lock twice (declared extras, all extras). The release
    interpreter evaluates markers for its own platform and compares both sets
    with the dist-info names in the venv, so a build that used other extras
    fails here instead of shipping under a wrong release.json.
    """
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    python = out / manifest["runtime"]["storePython"]
    site = out / manifest["runtime"]["sitePackages"]
    pm_sites = list((out / "pm-runtime").glob("lib/python*/site-packages"))
    uv_fact = json.loads((out / "tools/facts.json").read_text(encoding="utf-8"))["packages"]["uv"]
    uv = next(path for path in (out / "tools" / uv_fact["entry"]).rglob("uv") if path.is_file())
    with tempfile.TemporaryDirectory() as temp:
        env = {**os.environ, "UV_CACHE_DIR": str(Path(temp) / "cache"), "UV_OFFLINE": "1",
               "UV_NO_CONFIG": "1", "UV_PYTHON_DOWNLOADS": "never"}
        exports = {}
        for label, selection in (("declared", [arg for extra in EXTRAS for arg in ("--extra", extra)]),
                                 ("all", ["--all-extras"])):
            exports[label] = Path(temp) / f"{label}.txt"
            subprocess.run([str(uv), "export", "--frozen", "--no-hashes", "--no-header", "--no-annotate",
                            "--no-emit-project", "--format", "requirements.txt",
                            "--project", str(out / "hermes-agent"), *selection,
                            "--output-file", str(exports[label])],
                           env=env, check=True, stdout=subprocess.DEVNULL)
        result = subprocess.run(
            [str(python), "-I", "-B", "-c",
             f"import sys; sys.path[:0] = {[str(path) for path in pm_sites]!r}\n" + _EXTRAS_CHECK,
             str(exports["declared"]), str(exports["all"]), str(site), ",".join(EXTRAS)],
            capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"installed extras check failed: {result.stdout}{result.stderr}")
    log(f"extras check: {result.stdout.strip()}")


def write_stamp(source: Path, out: Path, sha: str) -> None:
    """Upstream's stamp writer; the commit is explicit, never $GITHUB_SHA."""
    stamp = out / "hermes-agent" / "install-stamp.json"
    env = {key: value for key, value in os.environ.items() if key not in STAMP_ENV_DROP}
    subprocess.run([sys.executable, "-B", str(source / "scripts/write_install_stamp.py"),
                    "--output", str(stamp), "--commit", sha, "--source", "ci",
                    "--update-mechanism", "external", "--display-version", f"git.{sha}"],
                   cwd=source, env=env, check=True)
    data = json.loads(stamp.read_text(encoding="utf-8"))
    if data["commit"] != sha or data["updateMechanism"] != "external" or data["dirty"]:
        raise SystemExit(f"unexpected install stamp: {data}")


def write_release(out: Path, sha: str) -> None:
    facts = json.loads((out / "tools/facts.json").read_text(encoding="utf-8"))
    packages = facts.get("packages", facts)
    tools = sorted(
        ({"name": name, "version": fact["version"], "entry": fact["entry"]}
         for name, fact in packages.items() if isinstance(fact, dict) and "entry" in fact),
        key=lambda tool: tool["name"])
    names = {tool["name"] for tool in tools}
    missing = [name for name in REQUIRED_TOOLS if name not in names]
    if missing:
        raise SystemExit(f"release store is missing required tools: {missing}")
    staged = sorted(name for name in names if excluded_tool(name))
    if staged:
        raise SystemExit(f"release store carries excluded tools: {staged}")
    python = next(tool["version"] for tool in tools if tool["name"] == "python")
    release = {"schema": 1, "sha": sha, "built_at": datetime.now(timezone.utc).isoformat(),
               "target": "linux-x64", "recipe": "scripts/bundles/native.py",
               "extras": list(EXTRAS), "python": python, "tools": tools}
    (out / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    log(f"release.json: python {python}, tools {sorted(names)}")


def discard_lock_files(out: Path) -> None:
    """Build-time PM lock files; a read-only tree must not carry them."""
    for path in sorted(out.rglob("*")):
        if path.is_file() and not path.is_symlink() and (path.name == ".lock" or
                                                          (path.name.startswith(".") and path.name.endswith(".lock"))):
            log(f"removing build lock {path.relative_to(out)}")
            path.unlink()


def share_tree(out: Path) -> None:
    """Every file readable and every directory traversable; nothing group/other-writable."""
    for directory, dirs, files in os.walk(out):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink():
                continue
            mode = path.stat().st_mode & 0o7777
            new = mode | 0o444
            if path.is_dir() or mode & 0o111:
                new |= 0o111
            new &= ~0o022
            if new != mode:
                path.chmod(new)
    out.chmod(0o755)


def check_no_host_paths(out: Path, needles: list[str]) -> None:
    args = ["grep", "-rIl", "-F"]
    for needle in needles:
        args += ["-e", needle]
    found = subprocess.run([*args, str(out)], capture_output=True, text=True)
    if found.returncode == 0:
        raise SystemExit(f"build-host paths leaked into the release:\n{found.stdout}")
    if found.returncode != 1:
        raise SystemExit(f"host-path scan failed: {found.stderr}")
    log(f"no build-host path in text files ({', '.join(needles)})")
    generic = subprocess.run(["grep", "-rIl", "/home/runner", str(out)], capture_output=True, text=True)
    for line in generic.stdout.splitlines():
        log(f"third-party text mentioning /home/runner: {Path(line).relative_to(out)}")


# --- inner: upstream native recipe with the three deviations ----------------

def inner(args) -> int:
    source, out, cache, sha = args.source, Path(args.out), args.cache, args.sha
    import pm
    from scripts.bundles import native

    # Each deviation replaces one upstream call site. If upstream stops calling
    # it (renamed, inlined, moved), the recipe would silently run unmodified,
    # so every wrapper must run exactly once before assembly is accepted.
    calls = dict.fromkeys(WRAPPERS, 0)

    # Deviation 1: extras all + messaging. native._prepare_native resolves
    # build_environment through the pm facade at call time.
    upstream_build_environment = pm.build_environment

    def build_environment(**kwargs):
        calls["extras"] += 1
        if not kwargs.pop("all_extras", False) or kwargs.get("extras"):
            raise RuntimeError("native recipe no longer requests all extras; review the extras deviation")
        log(f"venv extras: {', '.join(EXTRAS)} (instead of all extras)")
        return upstream_build_environment(**kwargs, extras=list(EXTRAS), all_extras=False)

    pm.build_environment = build_environment

    # Deviation 2: no shipped uv cache. The prepared-input contract still
    # expects the directory, so stage it empty and delete it after assembly.
    def stage_empty_uv_cache(_source: Path, destination: Path) -> None:
        calls["uv-cache"] += 1
        destination.mkdir(parents=True)

    native.stage_uv_cache = stage_empty_uv_cache
    native.prune_uv_cache_to_lock = lambda *_args, **_kwargs: 0

    # Deviation 4: trim the tool selection before anything is fetched, so the
    # store, facts.json and PM digests only ever describe the kept tools.
    from pm.registry import get_package, walk

    upstream_bundle_names = native._bundle_package_names

    def bundle_package_names() -> list[str]:
        calls["tool-selection"] += 1
        names = upstream_bundle_names()
        dropped = [name for name in names if excluded_tool(name)]
        kept = [name for name in names if not excluded_tool(name)]
        required = [name for name in dropped if not get_package(name).optional]
        if required:
            raise RuntimeError(f"refusing to drop non-optional tools: {required}")
        pulled_back = [package.name for package in walk(kept) if excluded_tool(package.name)]
        if pulled_back:
            raise RuntimeError(f"kept tools depend on excluded tools: {pulled_back}")
        log(f"tools: excluding {', '.join(sorted(dropped))}")
        return kept

    native._bundle_package_names = bundle_package_names

    prepared = native.prepare_native(out=out, ref=sha, source=source, cache=cache)
    require_each_wrapper_once(calls)
    status = native.finish_native(prepared, {})
    if status:
        return status
    shutil.rmtree(out / "uv-cache")
    log("uv-cache: not shipped")

    # Deviation 3: the WhatsApp bridge, which the snapshot excludes with scripts/.
    stage_bridge(source, out, sha)
    return 0


def require_each_wrapper_once(calls: dict[str, int]) -> None:
    wrong = {name: count for name, count in calls.items() if count != 1}
    if wrong:
        raise SystemExit(f"upstream recipe drift: deviation wrappers must each run once, got {wrong}")
    log(f"deviation wrappers ran once each: {', '.join(calls)}")


def stage_bridge(source: Path, out: Path, sha: str) -> None:
    from pm import env_for

    bridge = out / "hermes-agent" / BRIDGE
    if bridge.exists():
        raise SystemExit(f"{bridge} already exists; the snapshot exclusions changed")
    bridge.mkdir(parents=True)
    archive = subprocess.run(["git", "archive", "--format=tar", sha, "--", BRIDGE],
                             cwd=source, check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "--strip-components=2", "-C", str(bridge)],
                   input=archive, check=True)
    env = env_for("node", "npm", base_env=dict(os.environ))
    npm = shutil.which("npm", path=env.get("PATH"))
    if npm is None or not Path(npm).resolve().is_relative_to(out):
        raise SystemExit(f"npm must come from the release store, got {npm}")
    log(f"bridge: npm ci with {npm}")
    subprocess.run([npm, "ci", "--no-audit", "--no-fund", "--omit=dev"],
                   cwd=bridge, env=env, check=True)
    # Same rule as the WhatsApp adapter's _ensure_bridge_deps: sha256(package.json)[:16].
    digest = hashlib.sha256((bridge / "package.json").read_bytes()).hexdigest()[:16]
    (bridge / "node_modules" / ".hermes-pkg-hash").write_text(digest, encoding="utf-8")
    log(f"bridge: node_modules/.hermes-pkg-hash = {digest}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", type=Path, required=True, help="clean checkout at --sha")
    parser.add_argument("--sha", required=True)
    parser.add_argument("--out", required=True, help="final install path, ending in the sha")
    parser.add_argument("--cache", type=Path, required=True, help="uv build cache (not shipped)")
    parser.add_argument("--inner", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    return inner(args) if args.inner else outer(args)


if __name__ == "__main__":
    raise SystemExit(main())

---
name: recover-fork-sync
description: >-
  Recover pebble-tech/hermes-agent when the daily upstream sync workflow fails.
  Use when a sync-failure GitHub issue is opened, a Cursor automation webhook
  fires with event sync-failure, or the user asks to fix fork sync / rebase
  FEATURE_BRANCHES / ops-overlay. Read FORK.md first. Self-contained in this
  repo — do not depend on nudge-hermes-agents.
---

# Recover fork upstream sync

Fix **source branches** so the scheduled `sync-upstream.yml` workflow can rebuild
integration `main`. Never patch `main` directly — sync force-rebuilds it from
upstream + cherry-picks.

The sync rebases `FEATURE_BRANCHES` only inside the runner and never pushes
them, so `origin/carry/<name>` can sit on an older upstream base. Your fix push
(B, C or D below) is the only time a carried branch moves.

## Push scope (hard limit)

You may push **only** `carry/*` and `ops-overlay`. Never push `main`, `pr/*`,
or the head branch of an open upstream PR (for example
`fix/busy-path-pre-gateway-dispatch-upstream`). PR branches are human-only; a
ruleset blocks automation from pushing them. Check every push target first:

```bash
case "$target" in carry/*|ops-overlay) ;; *) echo "refusing to push $target"; exit 1 ;; esac
```

Push nothing, stop, and alert Kong when either holds:

- the carry tests (focused tests, the carry's own test files, then the carry
  test gate) fail after your fix;
- the conflict is semantic: upstream refactored the area the carry touches, or
  merged an equivalent fix.

Alert Kong by commenting on the `sync-failure` issue (open one if there is
none) mentioning `@vKongv`, with the conflict diff (`git diff` of the
conflicted files, or `git range-diff`) and the test output. Leave the issue
open. Do not push a partial fix.

## Read first

1. [FORK.md](../../../FORK.md) — branch model and recovery overview
2. [FORK_SYNC_AUTOMATION.md](../../../FORK_SYNC_AUTOMATION.md) — webhook + secrets setup
3. Payload / issue body — machine-readable `sync-failure` block (below)

## Remotes

| Remote | Repo | Use |
|--------|------|-----|
| `origin` | `pebble-tech/hermes-agent` | Push fixes here |
| `upstream` | `NousResearch/hermes-agent` | Fetch only; rebase onto `upstream/main` |

After clone: `git fetch --multiple --prune origin upstream`

**Local `main` must track `origin/main`**, not `upstream/main`:

```bash
git branch -u origin/main main
```

## Inputs (webhook or issue)

Parse the `sync-failure` YAML block or webhook JSON:

| Field | Meaning |
|-------|---------|
| `upstream_sha` | NousResearch `main` at failure time |
| `failed_step` | `rebase-ops-overlay`, `rebase-feature-branch`, `cherry-pick-main`, `focused-tests`, `carry-test-gate`, `plugin-smoke-test`, `push`, `record-gate` |
| `failed_branch` | Source branch to fix (`ops-overlay` or a `FEATURE_BRANCHES` entry, always `carry/<name>`) |
| `conflict_files` | Paths from rebase/cherry-pick |
| `workflow_run_url` | Actions log |
| `issue_number` | Open `sync-failure` issue (if any) |

If `failed_branch` is missing, read the workflow log URL for the failing step.

## Decision tree

### A. Upstream PR closed / change absorbed upstream

Symptoms: rebase conflict on a `FEATURE_BRANCHES` entry; upstream already has
equivalent code; NousResearch PR closed without merge or merged elsewhere.

This is a semantic conflict (see Push scope). Retiring a carry is Kong's
decision: push nothing, delete nothing, and do not edit `FEATURE_BRANCHES`.

1. Confirm on `upstream/main` (grep / log / compare).
2. Alert Kong on the `sync-failure` issue (mentioning `@vKongv`) with the
   evidence: the upstream commit or PR that covers the carry, the diff between
   the carry and upstream's version, the conflict, and any test output.
3. Propose the retirement for Kong to do: remove `carry/<name>` from
   `FEATURE_BRANCHES` and the `FORK.md` PR table via `ops-overlay`
   (`ops(fork): drop #NNNN <name> from sync`), then delete `carry/<name>` and
   the PR branch.
4. Do **not** rebase the obsolete carry.

Example: #17165 WhatsApp text batch superseded by upstream `b0ce47daa` → Kong
dropped `feature/whatsapp-inbound-text-batch` from `FEATURE_BRANCHES`.

### B. Rebase conflict — patch still needed

`failed_branch` is a carry (`carry/<name>`) or `ops-overlay`
(`failed_step: rebase-ops-overlay`). Use that name for `<branch>` below.

1. `git checkout -B <branch> origin/<branch>`
2. `git rebase upstream/main` — resolve conflicts. If upstream refactored the
   area or merged an equivalent fix, stop (semantic conflict, see Push scope).
3. Run focused tests and the carry's own test files (`git diff --name-only
   upstream/main..HEAD -- tests/`), then the carry test gate (below)
   (for `ops-overlay`, the focused tests and the gate)
4. Only if all pass: `git push --force-with-lease origin <branch>`

Fork-only tweaks go on `ops-overlay`, or as commits titled `[fork-only] ...`
at the tip of `carry/<name>`. Never on a PR branch.

### C. Cherry-pick conflict rebuilding `main`

Two feature branches touch the same lines. Fix the **later** branch in
`FEATURE_BRANCHES` order (as in B). If one carry looks redundant or the order
needs to change, do not reorder or drop it: alert Kong (as in A).

### D. Test or plugin smoke failure

Fix on the branch that introduced the regression (usually the feature branch
whose commits fail tests after rebase). Run the same focused tests locally.

### E. Carry test gate failure (`carry-test-gate`)

The run summary lists each fork-only failing test (`FAIL`) and each fork-only
undefined name (`RUFF`) and each fork-only local import that does not resolve
(`IMPORT`). Upstream passes those tests at the same base, so a
carry broke them, usually through a rebase that took one side of a conflict.
`MISSING` names an upstream test file the rebuilt `main` lacks: a carry's
rebase deleted it. Restore it on that carry; do not allowlist it to pass.

1. Find the carry: `git log upstream/main..origin/main -- <file>` on the files
   in the traceback, then that carry's own rebase commits.
2. Fix it on that `carry/<name>` branch (as in B) so upstream's test passes. Do not edit
   or delete upstream's test to make it pass. The one exception is a test that
   pins behaviour the carry changes on purpose (its commit message says what it
   changes); then update only that expectation, on the carry, and say so in the
   commit message.
3. Rebuild `main` locally the way the sync does and run the gate on it (below).
4. Push `carry/<name>` only if the gate passed, then trigger sync.

`record-gate`: the push succeeded but writing the `carry-test-gate` status
failed. Re-run the sync; nothing is wrong with the branches.

## Focused tests (match CI)

```bash
uv python install 3.14
uv sync --locked --python 3.14 --extra all --group dev
source .venv/bin/activate
sudo apt-get update && sudo apt-get install -y ripgrep   # Linux CI only
python -m pytest \
  tests/gateway/test_pre_gateway_dispatch.py \
  tests/gateway/test_session.py \
  tests/gateway/test_telegram_send_reply_markup.py \
  tests/hermes_cli/test_plugins.py \
  -q --tb=short
```

## Carry test gate (match CI)

On a local rebuild of `main` (ops-overlay and every `FEATURE_BRANCHES` entry
rebased onto `upstream/main`, then cherry-picked in order, as
`sync-upstream.yml` does):

```bash
git worktree add --detach ../upstream-base upstream/main
python .github/scripts/carry_test_gate.py --candidate . --base ../upstream-base
```

It must print `Carry test gate: passed`. It takes most of a CI run's time, so
run it once, after the focused tests pass.

## After fix — trigger sync

**Default:** after the carry tests pass, push the fixed source branch
(`carry/<name>` or `ops-overlay`, nothing else) directly:

```bash
git push --force-with-lease origin <failed_branch>
```

Do **not** open a pull request when that push succeeds. Skip orphan
`cursor/hermes-agent-upstream-sync-recovery-*` branches that only duplicate the
updated source tip.

Open a PR **only if** force-push is blocked or human review is required before
updating the source branch. If you open a PR:

- base = the **source branch** (`ops-overlay` or `carry/*`)
- never base = integration `main`

Then trigger sync:

```bash
gh workflow run sync-upstream.yml --repo pebble-tech/hermes-agent --ref main
gh run list --repo pebble-tech/hermes-agent --workflow sync-upstream.yml --limit 1
# wait for success
```

Verify:

```bash
git fetch --multiple origin upstream
git rev-list --count origin/main..upstream/main   # should be 0 after successful sync
```

## Closeout

1. Confirm sync workflow **success**
2. Comment on the `sync-failure` issue with what changed
3. Close the issue

## Hard rules

- Do **not** commit recovery fixes to integration `main`
- Do **not** leave `main` tracking `upstream/main`
- Do **not** add obsolete branches back to `FEATURE_BRANCHES`
- Do **not** change the carry test gate (`carry_test_gate.py`, its scope or its
  steps in `sync-upstream.yml`) or the release's `carry-test-gate` check to get
  a sync through. A gate change is a reviewed `ops-overlay` PR, never a recovery.
- When resolving a conflict on a carry, do not take upstream's side of a hunk
  the carry needs without checking every caller of what the carry moved or
  renamed (lazy imports inside functions, `mock.patch` targets, docs)
- Do **not** push anything but `carry/*` and `ops-overlay` (see Push scope).
  Never push `main`, `pr/*` or an open upstream PR's head branch
- Do **not** push when the carry tests fail or the conflict is semantic; alert
  Kong instead
- Before merging a change to `sync-upstream.yml` or the carry test gate, cancel
  any queued sync run (`gh run list -R pebble-tech/hermes-agent -w
  sync-upstream.yml -s queued`, then `gh run cancel <id>`): a queued run keeps
  the old workflow file
- Do **not** open a recovery PR against `main` when the source branch is already updated
- Minimize scope — fork ops only, no unrelated Hermes core changes

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
them, so `origin/<branch>` can sit on an older upstream base. Your fix push
(B, C or D below) is the only time a carried branch moves.

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
| `failed_branch` | Source branch to fix (`ops-overlay` or a `FEATURE_BRANCHES` entry) |
| `conflict_files` | Paths from rebase/cherry-pick |
| `workflow_run_url` | Actions log |
| `issue_number` | Open `sync-failure` issue (if any) |

If `failed_branch` is missing, read the workflow log URL for the failing step.

## Decision tree

### A. Upstream PR closed / change absorbed upstream

Symptoms: rebase conflict on a `FEATURE_BRANCHES` entry; upstream already has
equivalent code; NousResearch PR closed without merge or merged elsewhere.

1. Confirm on `upstream/main` (grep / log / compare).
2. **PR or push to `ops-overlay` only:**
   - Remove branch from `FEATURE_BRANCHES` in `.github/workflows/sync-upstream.yml`
   - Update `FORK.md` upstream PR table
   - Short commit message: `ops(fork): drop #NNNN <branch> from sync`
3. Delete dead branch from origin: `git push origin --delete <branch>`
4. Do **not** rebase the obsolete feature branch.

Example: #17165 WhatsApp text batch superseded by upstream `b0ce47daa` → drop
`feature/whatsapp-inbound-text-batch` from `FEATURE_BRANCHES`.

### B. Rebase conflict — patch still needed

1. `git checkout -B <failed_branch> origin/<failed_branch>`
2. `git rebase upstream/main` — resolve conflicts
3. Run focused tests (see below)
4. `git push --force-with-lease origin <failed_branch>`

### C. Cherry-pick conflict rebuilding `main`

Two feature branches touch the same lines. Fix the **later** branch in
`FEATURE_BRANCHES` order, or reorder/drop one via ops-overlay if redundant.

### D. Test or plugin smoke failure

Fix on the branch that introduced the regression (usually the feature branch
whose commits fail tests after rebase). Run the same focused tests locally.

### E. Carry test gate failure (`carry-test-gate`)

The run summary lists each fork-only failing test (`FAIL`) and each fork-only
undefined name (`RUFF`). Upstream passes those tests at the same base, so a
carry broke them, usually through a rebase that took one side of a conflict.

1. Find the carry: `git log upstream/main..origin/main -- <file>` on the files
   in the traceback, then that carry's own rebase commits.
2. Fix it on that carry branch (as in B) so upstream's test passes. Do not edit
   or delete upstream's test to make it pass. The one exception is a test that
   pins behaviour the carry changes on purpose (its commit message says what it
   changes); then update only that expectation, on the carry, and say so in the
   commit message.
3. Rebuild `main` locally the way the sync does and run the gate on it (below).
4. Push the carry branch, then trigger sync.

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

**Default:** push the fixed source branch directly:

```bash
git push --force-with-lease origin <failed_branch>
```

Do **not** open a pull request when that push succeeds. Skip orphan
`cursor/hermes-agent-upstream-sync-recovery-*` branches that only duplicate the
updated source tip.

Open a PR **only if** force-push is blocked or human review is required before
updating the source branch. If you open a PR:

- base = the **source branch** (`ops-overlay` or `feature/*` / `fix/*`)
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
- Do **not** open a recovery PR against `main` when the source branch is already updated
- Minimize scope — fork ops only, no unrelated Hermes core changes

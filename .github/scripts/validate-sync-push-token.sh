#!/usr/bin/env bash
# Validate SYNC_PUSH_TOKEN before expensive rebase/test work or push.
set -euo pipefail

repo="${1:?repository (owner/name)}"
token_raw="${SYNC_PUSH_TOKEN:-}"
# Secrets pasted with trailing newlines/spaces are a common misconfiguration.
token="$(printf '%s' "$token_raw" | tr -d '[:space:]')"

if [ -z "$token" ]; then
  echo "::error::SYNC_PUSH_TOKEN is required to push rebased branches. Configure a classic PAT with repo + workflow scopes in repo secrets (see FORK_SYNC_AUTOMATION.md)."
  exit 1
fi

if ! git ls-remote "https://x-access-token:${token}@github.com/${repo}.git" HEAD >/dev/null 2>&1; then
  echo "::error::SYNC_PUSH_TOKEN authentication failed (expired, revoked, or missing workflow scope). Rotate the classic PAT in repo secrets — repo + workflow scopes — then re-run sync-upstream.yml."
  exit 1
fi

echo "SYNC_PUSH_TOKEN validated"

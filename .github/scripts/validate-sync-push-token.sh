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

case "$token" in
  ghs_*|ghu_*)
    echo "::error::SYNC_PUSH_TOKEN looks like a GitHub App token (ghs_/ghu_). Sync push requires a classic PAT with repo + workflow scopes — App tokens cannot update .github/workflows/* on push."
    exit 1
    ;;
esac

if ! git ls-remote "https://x-access-token:${token}@github.com/${repo}.git" HEAD >/dev/null 2>&1; then
  echo "::error::SYNC_PUSH_TOKEN authentication failed (expired, revoked, or missing repo scope). Rotate the classic PAT in repo secrets — repo + workflow scopes — then re-run sync-upstream.yml."
  exit 1
fi

# Classic PATs expose granted scopes on the API root response.
scopes="$(
  curl -fsSI \
    -H "Authorization: token ${token}" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/" \
    | tr -d '\r' \
    | awk -F': ' 'tolower($1) == "x-oauth-scopes" { print $2 }'
)"
if [ -n "$scopes" ] && ! printf '%s' "$scopes" | tr ',' '\n' | grep -qx 'workflow'; then
  echo "::error::SYNC_PUSH_TOKEN is missing the workflow scope (has: ${scopes}). Rebase pushes include upstream .github/workflows/* changes — use a classic PAT with repo + workflow scopes."
  exit 1
fi

echo "SYNC_PUSH_TOKEN validated"

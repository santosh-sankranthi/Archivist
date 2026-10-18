#!/usr/bin/env bash
# ci/deploy.sh — deploy helper for the self-hosted GitHub Actions runner.
#
# The workflow (.github/workflows/deploy.yml) runs, ON THIS BOX:
#   deploy.sh pull <sha>   fast-forward the canonical checkout to <sha>
#   deploy.sh test         test gates (pytest + console vitest)
#   deploy.sh apply        rebuild what changed, restart the console service, smoke test
#   deploy.sh report       print the live URL, refresh README's live link (best effort)
#
# The canonical checkout is the box's own clone — the runner never deploys from
# its workspace copy. Remotes: `origin` = public https (fetch, no auth needed),
# `github` = ssh (push, uses ~/.ssh/archivist_deploy).
set -euo pipefail

REPO="/home/openclaw/projects/archivist"
STATE="${HOME}/.cache/archivist-ci"
mkdir -p "$STATE"
cd "$REPO"

# bundled node/npm + local bins on PATH (same as the services use)
export PATH="/home/openclaw/.local/bin:/home/openclaw/.openclaw/tools/node-v24.19.0/bin:$PATH"

log() { echo "[deploy] $*"; }

pull() {
  local target="${1:?usage: deploy.sh pull <sha>}"
  git fetch --prune origin main
  if [ -n "$(git status --porcelain)" ]; then
    echo "::error::refusing to deploy — working tree has uncommitted changes:"
    git status --short
    exit 1
  fi
  git checkout -q main
  if ! git merge-base --is-ancestor HEAD "$target"; then
    echo "::error::${target} is not a descendant of $(git rev-parse --short HEAD) — not a fast-forward; refusing."
    exit 1
  fi
  local old new
  old="$(git rev-parse HEAD)"
  git merge --ff-only -q "$target"
  new="$(git rev-parse HEAD)"
  git diff --name-only "$old" "$new" > "$STATE/changed_files"
  echo "$new" > "$STATE/deployed_sha"
  log "updated ${old:0:9} -> ${new:0:9}: $(git log -1 --pretty=%s)"
  if [ "$old" != "$new" ]; then
    log "changed files:"; sed 's/^/  /' "$STATE/changed_files" | head -40
  fi
}

test_gates() {
  log "pytest"
  .venv/bin/python -m pytest -q
  log "console tests"
  (cd console && npm test)
}

apply() {
  local changed=""
  [ -f "$STATE/changed_files" ] && changed="$(cat "$STATE/changed_files")"
  if echo "$changed" | grep -qE '^(requirements|pyproject)'; then
    log "python deps changed -> pip install"
    .venv/bin/python -m pip install -q -r requirements.txt
  fi
  if echo "$changed" | grep -qE '^console/' || [ ! -f console/dist/index.html ]; then
    log "console changed -> rebuild"
    (cd console && npm ci --no-audit --no-fund && npm run build)
  else
    log "no console changes"
  fi
  log "restart archivist-console.service"
  systemctl --user restart archivist-console.service
  local code="" i
  for i in $(seq 1 20); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 3 http://127.0.0.1:8787/console || true)"
    [ "$code" = "200" ] && break
    sleep 1
  done
  log "smoke :8787/console -> HTTP $code"
  [ "$code" = "200" ] || { echo "::error::console smoke test failed"; exit 1; }
}

report() {
  local url
  url="$(journalctl --user -u archivist-tunnel --no-pager -o cat 2>/dev/null | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)"
  log "live: ${url:-<unknown>}/console"
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    {
      echo "### Archivist deploy"
      echo "- sha: \`$(git rev-parse --short HEAD)\`"
      [ -n "$url" ] && echo "- live: <${url}/console>"
      echo "- service: $(systemctl --user is-active archivist-console.service)"
    } >> "$GITHUB_STEP_SUMMARY"
  fi
  if [ -n "$url" ] && grep -q 'trycloudflare\.com' README.md 2>/dev/null && ! grep -q "$url" README.md; then
    sed -i -E "s#https://[a-z0-9-]+\.trycloudflare\.com#${url}#g" README.md
    if [ -n "$(git status --porcelain README.md)" ]; then
      git add README.md
      git commit -q -m "ci: refresh live demo URL [skip ci]"
      if git push -q github HEAD:main; then
        log "README live URL refreshed + pushed"
      else
        log "note: README refresh could not be pushed (no write access?) — reverting local commit"
        git reset -q --hard HEAD~1
      fi
    fi
  fi
}

case "${1:-}" in
  pull) shift; pull "$@" ;;
  test) test_gates ;;
  apply) apply ;;
  report) report ;;
  *) echo "usage: deploy.sh pull <sha> | test | apply | report"; exit 2 ;;
esac

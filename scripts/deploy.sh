#!/usr/bin/env bash
# Deploy instasaver by building the image ON the target server.
#
# Why build on the server instead of `docker compose pull`?
# The server's outbound traffic goes through a VPN whose exit node throttles
# GitHub infrastructure (ghcr.io, github.com, *.githubusercontent.com) and
# deb.debian.org down to ~45 KB/s, which makes pulling the ~1.3 GB image from
# GHCR hang. Docker Hub, PyPI/Fastly and cloudfront.debian.net are NOT
# throttled, so building the image on the server works fine — it never pulls
# the finished image from GHCR.
#
# A shallow `git clone` of the repo is small enough to slip past the throttle
# (a few hundred KB), so we fetch source, build, then run.
#
# Usage:  ./scripts/deploy.sh                 # deploy origin/main
#         ./scripts/deploy.sh <ref>           # deploy a branch/tag/sha
#         ./scripts/deploy.sh <ref> --force   # deploy a ref that is behind main
#
# Safety: refuses to deploy a ref that does NOT contain origin/main's HEAD
# (i.e. a ref that would roll production back). Override with --force.
#
# Idempotent and safe to re-run. Requires SSH access to the target host.
set -euo pipefail

SSH_HOST="${DEPLOY_SSH_HOST:-st-dad}"
REPO_URL="${DEPLOY_REPO_URL:-https://github.com/starod00m/instasaver.git}"
REF="${1:-main}"
FORCE="${2:-}"
IMAGE="ghcr.io/starod00m/instasaver"
BUILD_DIR="/tmp/instasaver-build"
PROD_DIR="/srv/bots/instasaver"

# --- Guard: don't silently roll production back ---------------------------
# The ref we deploy must contain origin/main's HEAD. Otherwise we'd build from
# code older than main and quietly downgrade prod (which is exactly how a
# previous deploy reverted an Instagram fix). Run from a clone that has an
# `origin` remote; if we can't resolve refs locally, warn and continue.
if git rev-parse --git-dir >/dev/null 2>&1; then
    git fetch -q origin main 2>/dev/null || true
    if git rev-parse -q --verify origin/main >/dev/null 2>&1 \
       && git rev-parse -q --verify "${REF}" >/dev/null 2>&1; then
        if ! git merge-base --is-ancestor origin/main "${REF}"; then
            echo "!! Ref '${REF}' does NOT contain origin/main — deploying it would"
            echo "!! roll production back. Rebase onto main, or pass --force to override."
            [ "${FORCE}" = "--force" ] || exit 1
            echo ">> --force given: deploying a ref behind main anyway."
        fi
    else
        echo ">> (skipping ancestry check: origin/main or '${REF}' not found locally)"
    fi
else
    echo ">> (not in a git repo: skipping ancestry check)"
fi

echo ">> Deploying instasaver (ref=${REF}) to ${SSH_HOST}"

# Args are passed positionally to the remote bash: $1=REF $2=REPO_URL
# $3=IMAGE $4=BUILD_DIR $5=PROD_DIR. Heredoc is quoted so nothing expands locally.
ssh "$SSH_HOST" 'bash -euo pipefail -s' -- \
    "$REF" "$REPO_URL" "$IMAGE" "$BUILD_DIR" "$PROD_DIR" <<'REMOTE'
REF="$1"; REPO_URL="$2"; IMAGE="$3"; BUILD_DIR="$4"; PROD_DIR="$5"

echo ">> [server] Fetching source (${REF})"
if [ -d "${BUILD_DIR}/.git" ]; then
    git -C "${BUILD_DIR}" fetch --depth 1 origin "${REF}"
    git -C "${BUILD_DIR}" checkout -f FETCH_HEAD
else
    rm -rf "${BUILD_DIR}"
    # Try shallow branch/tag clone; fall back to full clone if REF is a bare sha.
    if ! git clone --depth 1 --branch "${REF}" "${REPO_URL}" "${BUILD_DIR}" 2>/dev/null; then
        git clone "${REPO_URL}" "${BUILD_DIR}"
        git -C "${BUILD_DIR}" checkout -f "${REF}"
    fi
fi

SHA="$(git -C "${BUILD_DIR}" rev-parse --short HEAD)"
SUBJECT="$(git -C "${BUILD_DIR}" log -1 --pretty=%s)"
TAG="${IMAGE}:sha-${SHA}"
echo ">> [server] Building ${TAG}  (${SUBJECT})"
# deb.debian.org is throttled by the VPN exit; cloudfront.debian.net (the
# official Debian CDN) is not, so point apt at it for the runtime-stage install.
DOCKER_BUILDKIT=1 docker build \
    --build-arg APT_MIRROR="http://cloudfront.debian.net" \
    -t "${TAG}" "${BUILD_DIR}"

echo ">> [server] Pointing prod compose at ${TAG}"
cd "${PROD_DIR}"
cp -f compose.yml "compose.yml.bak"
# Rewrite the image: line to the freshly built tag.
sed -i -E "s#^([[:space:]]*image:[[:space:]]*).*#\1${TAG}#" compose.yml
grep -E "image:" compose.yml

echo ">> [server] Restarting container"
docker compose up -d --no-build

echo ">> [server] Waiting for health"
status=missing
for _ in $(seq 1 20); do
    status="$(docker inspect --format '{{.State.Health.Status}}' instasaver 2>/dev/null || echo missing)"
    echo "   health=${status}"
    [ "${status}" = "healthy" ] && break
    sleep 3
done
if [ "${status}" != "healthy" ]; then
    echo "!! container not healthy — rolling back to previous compose"
    mv -f compose.yml.bak compose.yml
    docker compose up -d --no-build || true
    docker compose logs --tail 30
    exit 1
fi

echo ">> [server] Pruning old dangling images"
docker image prune -f >/dev/null

echo ">> [server] Done: ${TAG} deployed and healthy"
REMOTE

echo ">> Deploy finished."

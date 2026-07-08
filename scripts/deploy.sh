#!/usr/bin/env bash
# Deploy instasaver to st-dad by building the image ON the server.
#
# Why build on the server instead of `docker compose pull`?
# The server's outbound traffic goes through a VPN whose exit node throttles
# GitHub infrastructure (ghcr.io, github.com, *.githubusercontent.com) down to
# ~40-55 KB/s, which makes pulling the ~1.3 GB image from GHCR hang. Docker Hub,
# PyPI/Fastly and Debian mirrors are NOT throttled, so building the image
# locally on the server works fine — it never pulls the finished image from GHCR.
#
# A shallow `git clone` of the repo is small enough to slip past the throttle
# (a few hundred KB), so we fetch source, build, then run.
#
# Usage:  ./scripts/deploy.sh            # deploy current main
#         ./scripts/deploy.sh <ref>      # deploy a specific branch/tag/sha
#
# Idempotent and safe to re-run. Requires SSH access to `st-dad`.
set -euo pipefail

SSH_HOST="${DEPLOY_SSH_HOST:-st-dad}"
REPO_URL="${DEPLOY_REPO_URL:-https://github.com/starod00m/instasaver.git}"
REF="${1:-main}"
IMAGE="ghcr.io/starod00m/instasaver"
BUILD_DIR="/tmp/instasaver-build"
PROD_DIR="/srv/bots/instasaver"

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
TAG="${IMAGE}:sha-${SHA}"
echo ">> [server] Building ${TAG}"
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
    echo "!! container not healthy — showing logs"
    docker compose logs --tail 30
    exit 1
fi

echo ">> [server] Pruning old dangling images"
docker image prune -f >/dev/null

echo ">> [server] Done: ${TAG} deployed and healthy"
REMOTE

echo ">> Deploy finished."

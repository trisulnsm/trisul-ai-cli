#!/bin/bash
# Build and push the public Docker Hub image.
# Usage: ./docker/publish.sh [dockerhub-namespace/trisul-ai-cli] [version]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

IMAGE="${1:-unleashnetworks/trisul-ai-cli}"
VERSION="${2:-1.0.2}"

if ! docker info >/dev/null 2>&1; then
    echo "Docker is not running. Start the Docker engine, then run this script again." >&2
    exit 1
fi

echo "Building ${IMAGE}:${VERSION} and ${IMAGE}:latest"
docker build \
    --pull \
    -t "${IMAGE}:${VERSION}" \
    -t "${IMAGE}:latest" \
    .

echo "Pushing ${IMAGE}:${VERSION} and ${IMAGE}:latest"
echo "The Docker Hub repository must already exist and be set to Public."
docker push "${IMAGE}:${VERSION}"
docker push "${IMAGE}:latest"

echo "Published:"
echo "  docker pull ${IMAGE}:${VERSION}"
echo "  docker pull ${IMAGE}:latest"

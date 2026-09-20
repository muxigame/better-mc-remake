#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 2 ]]; then
  echo "usage: deploy.sh <image-tag> <registry-username>" >&2
  exit 2
fi

image_tag="$1"
registry_username="$2"
registry="crpi-igfh7ap28r7cepq6.cn-shanghai.personal.cr.aliyuncs.com"
project_dir="/opt/better-mc-remake"

if [[ ! "$image_tag" =~ ^[A-Za-z0-9_.-]+$ ]]; then
  echo "invalid image tag" >&2
  exit 2
fi

IFS= read -r registry_password
if [[ -z "$registry_password" ]]; then
  echo "registry password was not provided on stdin" >&2
  exit 2
fi

cleanup() {
  docker logout "$registry" >/dev/null 2>&1 || true
}
trap cleanup EXIT

printf '%s' "$registry_password" | docker login --username "$registry_username" --password-stdin "$registry" >/dev/null
unset registry_password

cd "$project_dir"
export BMC_IMAGE_TAG="$image_tag"

docker compose -f compose.yaml pull
docker compose -f compose.yaml up -d --remove-orphans

for _ in $(seq 1 45); do
  if curl --fail --silent http://127.0.0.1:8099/healthz >/dev/null; then
    printf 'deployment %s is healthy\n' "$image_tag"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

docker compose -f compose.yaml ps
docker compose -f compose.yaml logs --tail=120
exit 1

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

check_guest_navigation() {
  local path result
  for path in '/api/v1/auth/entry?return_to=%2Faccount.html' '/account.html'; do
    result="$(curl --silent --show-error --max-time 10 --output /dev/null \
      --write-out '%{http_code} %{redirect_url}' "http://127.0.0.1:8099$path")" || return 1
    case "$result" in
      '303 https://account.muxigame.com/oauth/authorize?'*) ;;
      *) printf 'guest OAuth navigation check failed: %s\n' "$path" >&2; return 1 ;;
    esac
  done
  result="$(curl --silent --show-error --max-time 10 --output /dev/null \
    --write-out '%{http_code}' 'http://127.0.0.1:8099/__protected/account.html')" || return 1
  [[ "$result" == '404' ]]
}

for _ in $(seq 1 45); do
  if curl --fail --silent http://127.0.0.1:8099/healthz >/dev/null && check_guest_navigation; then
    printf 'deployment %s is healthy; guest OAuth navigation verified\n' "$image_tag"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

docker compose -f compose.yaml ps
docker compose -f compose.yaml logs --tail=120
exit 1

#!/bin/sh
set -eu

bin/dev-environment.sh

token=${GH_TOKEN:-${GITHUB_TOKEN:-}}
if [ -z "$token" ] && command -v gh >/dev/null 2>&1; then
  token=$(gh auth token 2>/dev/null) || token=
fi
RUBIO_BUILD_GITHUB_TOKEN=$token
export RUBIO_BUILD_GITHUB_TOKEN

base=$(awk '$1 == "FROM" && $3 == "AS" && $4 == "dev" { print $2; exit }' Dockerfile)
if [ -z "$base" ]; then
  echo 'Cannot find the dev base image in Dockerfile.' >&2
  exit 1
fi

# Build through bake rather than compose so the image is exported uncompressed. The dev image is
# built to be used here and never pushed, so compressing its layers only to unpack them again is
# work no one reads. Compose always gzips; bake can be told not to. Bake takes the build
# definition from compose.yaml but assigns no tag of its own, so name the image exactly as
# compose would, or just would not find it.
# Compose reads COMPOSE_PROJECT_NAME from .env, which dev-environment.sh just wrote, but bake
# reads only the environment, so take it from the same .env when the caller did not export it.
project=${COMPOSE_PROJECT_NAME:-}
if [ -z "$project" ]; then
  project=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env)
fi
if [ -z "$project" ]; then
  echo 'Cannot find COMPOSE_PROJECT_NAME in the environment or .env.' >&2
  exit 1
fi

build() {
  docker buildx bake -f compose.yaml dev \
    --set "dev.tags=$project-dev" \
    --set dev.output=type=docker,compression=uncompressed \
    "$@"
}

# The build reads the base with Buildx's registry credential, which can differ from docker pull's.
if refusal=$(docker buildx imagetools inspect "$base" 2>&1 >/dev/null); then
  build --pull
elif docker image inspect "$base" >/dev/null 2>&1; then
  printf '%s\n' "$refusal" >&2
  echo "Cannot pull $base; building with the base image already present locally." >&2
  build
else
  printf '%s\n' "$refusal" >&2
  echo "Cannot pull $base and no local base image is available. Authenticate to the registry and run just build-dev again." >&2
  exit 1
fi

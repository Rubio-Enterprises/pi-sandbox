set positional-arguments
set shell := ["bash", "-euo", "pipefail", "-c"]

export RUBIO_BUILD_GITHUB_TOKEN := env('GH_TOKEN', env('GITHUB_TOKEN', ''))
export REPO_DIR := justfile_directory()
export COMPOSE_PROJECT_NAME := "repo-" + sha256(justfile_directory())
unexport GIT_COMMON_DIR
export RUBIO_GIT_COMMON_DIR := `env -u GIT_COMMON_DIR git rev-parse --path-format=absolute --git-common-dir`

_run := if path_exists('/opt/standards') == 'true' { '' } else { 'docker compose run --rm -T dev' }
interactive := if path_exists('/opt/standards') == 'true' { '' } else { 'docker compose run --rm dev' }

# Show available recipes
_default:
    @just --list

# Build the dev image
build-dev:
    @if [ ! -d /opt/standards ]; then bin/build-dev.sh; fi

# Build the dev image, sync dependencies into volumes, and install git hooks
setup: build-dev
    {{_run}} bin/setup.sh </dev/null
    {{_run}} /opt/standards/bin/install-hooks </dev/null

# Run standards checks inside the container
check:
    {{_run}} standards check </dev/null

# Run formatters and fixers inside the container
fmt:
    {{_run}} standards fmt </dev/null

# Run pull-request conventions check inside the container
check-pr:
    {{_run}} standards check-pr </dev/null

# Regenerate the mise lock and every generated file
generate *args:
    @if [ -d /opt/standards ]; then standards lock; else docker run --rm -v "$PWD:$PWD" -w "$PWD" -e MISE_GITHUB_TOKEN -e GH_TOKEN "$(awk '/^FROM ghcr.io\/rubio-enterprises\/base:/ { print $2; exit }' Dockerfile)" standards lock </dev/null; fi
    @if [ ! -d /opt/standards ]; then bin/build-dev.sh; fi
    {{_run}} standards generate "$@" </dev/null

# Open interactive shell inside the dev container
shell:
    {{interactive}} bash

# Execute arbitrary command inside the dev container
exec *args:
    {{_run}} "$@" </dev/null

# Run a command that reads its request from a pipe or heredoc
pipe *args:
    {{_run}} "$@"

test *args:
    {{_run}} bin/test.sh "$@" </dev/null

typecheck *args:
    {{_run}} bin/typecheck.sh "$@" </dev/null

setup-deps *args:
    {{_run}} bin/setup-deps.sh "$@" </dev/null

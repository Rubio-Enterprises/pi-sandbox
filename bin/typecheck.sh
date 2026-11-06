#!/bin/sh
set -eu
uv run --frozen pyright "$@"

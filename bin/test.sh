#!/bin/sh
set -eu
uv run --frozen pytest -m 'not live' --junitxml=reports/test/junit.xml "$@"

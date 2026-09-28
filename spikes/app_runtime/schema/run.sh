#!/usr/bin/env bash
# Experiment 6: schema diffing, offline.
#   bash schema/run.sh    -> results/sqldef.{json,log}, results/pydiff.{json,log}
# Downloads sqlite3def once (pinned version + sha256) into .tools/; everything
# else runs in python:3.12-slim with --network none.
set -euo pipefail
spike="$(cd "$(dirname "$0")/.." && pwd)"
cd "$spike"
VERSION=v3.11.24
SHA256=736aa432a015c049b708e877cafe1c0959908b1a1450f2553dcd0d8387c0758f
mkdir -p .tools results
if [ ! -x .tools/sqlite3def ]; then
  curl -fsSL -o .tools/sqlite3def_linux_amd64.tar.gz "https://github.com/sqldef/sqldef/releases/download/$VERSION/sqlite3def_linux_amd64.tar.gz"
  echo "$SHA256  .tools/sqlite3def_linux_amd64.tar.gz" | sha256sum -c -
  tar -xzf .tools/sqlite3def_linux_amd64.tar.gz -C .tools sqlite3def
fi
run() { docker run --rm --network none --user "$(id -u):$(id -g)" -v "$spike":/spike:ro -v "$spike/results":/out python:3.12-slim "$@"; }
run python /spike/schema/sqldef_matrix.py /spike/.tools/sqlite3def /out | tee results/sqldef.log
run python /spike/schema/pydiff_matrix.py /out | tee results/pydiff.log

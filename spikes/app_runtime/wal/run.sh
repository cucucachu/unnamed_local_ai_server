#!/usr/bin/env bash
# Experiment 7: WAL-mode SQLite read from a :ro bind mount in another container.
#   bash wal/run.sh            -> results/wal.json (reader results) + results/wal.log (full output incl. writer stats)
# Phases:
#   concurrent   writer (rw mount) runs 15 s; reader (ro mount, exec-style hardening) reads the whole time
#   after-clean  writer exited cleanly (WAL checkpointed and removed); reader runs 3 s
#   after-crash  writer killed mid-stream (-wal/-shm left behind); reader runs 3 s
set -euo pipefail
exec > >(tee "$(cd "$(dirname "$0")/.." && pwd)/results/wal.log") 2>&1
here="$(cd "$(dirname "$0")" && pwd)"
spike="$(dirname "$here")"
img=python:3.12-slim
uid="$(id -u):$(id -g)"
out="$spike/results/wal.json"
mkdir -p "$spike/results"
reader_flags=(--rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges --user 20001:30001 --tmpfs /tmp)
: > "$out.tmp"

run_phase() { # name writer_seconds reader_seconds writer_exit
  local name=$1 wsec=$2 rsec=$3 wexit=$4 data
  data="$(mktemp -d /tmp/homeai-wal-XXXXXX)"
  chmod 0755 "$data"
  if [ "$name" = concurrent ]; then
    docker run -d --name "wal-writer-$$" --network none --user "$uid" -e WRITER_EXIT="$wexit" \
      -v "$here":/wal:ro -v "$data":/data "$img" python /wal/writer.py /data "$wsec" >/dev/null
    sleep 1.5
    docker run "${reader_flags[@]}" -e PHASE="$name" -v "$here":/wal:ro -v "$data":/data:ro "$img" python /wal/reader.py /data "$rsec" >> "$out.tmp"
    docker wait "wal-writer-$$" >/dev/null
    docker logs "wal-writer-$$" | sed 's/^/# /'
    docker rm "wal-writer-$$" >/dev/null
  else
    docker run --rm --network none --user "$uid" -e WRITER_EXIT="$wexit" -v "$here":/wal:ro -v "$data":/data "$img" \
      python /wal/writer.py /data "$wsec" | sed 's/^/# /' || true  # WRITER_EXIT=crash exits 9 on purpose
    echo "# files after writer ($wexit): $(ls -A "$data" | tr '\n' ' ')"
    docker run "${reader_flags[@]}" -e PHASE="$name" -v "$here":/wal:ro -v "$data":/data:ro "$img" python /wal/reader.py /data "$rsec" >> "$out.tmp"
  fi
  rm -rf "$data"
}

run_phase concurrent 18 15 clean
# Abuse case: a read-only reader holds a read transaction open; does the WAL stop being reset?
pin_phase() {
  local data; data="$(mktemp -d /tmp/homeai-wal-XXXXXX)"; chmod 0755 "$data"
  for pin in 0 1; do
    rm -rf "${data:?}"/*
    docker run -d --name "wal-writer-$$" --network none --user "$uid" -v "$here":/wal:ro -v "$data":/data "$img" python /wal/writer.py /data 12 >/dev/null
    sleep 1.5
    if [ "$pin" = 1 ]; then
      docker run "${reader_flags[@]}" -v "$here":/wal:ro -v "$data":/data:ro "$img" python /wal/pin_reader.py /data 9 | sed 's/^/# /'
    fi
    docker wait "wal-writer-$$" >/dev/null
    echo "# pin=$pin $(docker logs "wal-writer-$$")"
    docker rm "wal-writer-$$" >/dev/null
  done
  rm -rf "$data"
}
pin_phase
run_phase after-clean 3 3 clean
run_phase after-crash 3 3 crash
python3 -c "import json,sys; print(json.dumps([json.loads(l) for l in open(sys.argv[1]) if l.strip()], indent=2))" "$out.tmp" > "$out"
rm "$out.tmp"
python3 - "$out" <<'EOF'
import json, sys
for p in json.load(open(sys.argv[1])):
    print(f"== {p['phase']}  (sqlite {p['sqlite']}, files: {p['files']})")
    for m, s in p["modes"].items():
        print(f"  {m:38s} ok={s['ok']:5d} violations={s['invariant_violations']:4d} lag p50/p95={s['lag_ms_p50']}/{s['lag_ms_p95']} ms errors={s['errors']}")
EOF

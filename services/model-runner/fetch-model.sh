#!/usr/bin/env bash
set -euo pipefail

# Fetches the pinned Gemma 4 GGUF quant into services/model-runner/models/
# using the huggingface_hub CLI via `uvx` (no global install required).
#
# --- Deviation from the original ticket assumption (read before editing) ---
# The Conventions & Contracts reference issue (and this ticket) assumed the
# default quant would be `Q4_K_M` (filename gemma-4-26B-A4B-it-Q4_K_M.gguf,
# ~16.8 GB). Verified against the *live* HF repo tree
# (https://huggingface.co/api/models/ggml-org/gemma-4-26B-A4B-it-GGUF/tree/main,
# checked 2026-08-29): that file does not exist. This repo is auto-converted
# (its README says "automatically converted using
# https://github.com/ggml-org/convert") and only ships legacy quant types,
# not K-quants:
#
#   gemma-4-26B-A4B-it-Q4_0.gguf   ~14.6 GB  (single file, not sharded)
#   gemma-4-26B-A4B-it-Q8_0.gguf   ~26.9 GB  (single file, not sharded)
#   gemma-4-26B-A4B-it-BF16.gguf   ~50.5 GB  (single file, not sharded)
#
# ...plus siblings we must NOT touch: `mmproj-gemma-4-26B-A4B-it-*.gguf`
# (vision adapter — explicitly out of scope for this text-only v1) and
# `dflash-*` / `mtp-*` (speculative-decoding draft/MTP-head files, unrelated
# to the main text quant). None of the three main-weight files above are
# sharded (no `-00001-of-000NN` suffix in the repo listing), so a single
# `hf download <repo> <file>` call per quant is sufficient — no glob/prefix
# matching is needed here.
#
# Given `Q4_K_M` doesn't exist, this script defaults to `Q4_0` (the smallest
# available real quant) instead. Pass `Q8_0` or `BF16` as the [quant] arg to
# fetch a bigger one.

REPO="ggml-org/gemma-4-26B-A4B-it-GGUF"
DEFAULT_QUANT="Q4_0"

QUANT="${DEFAULT_QUANT}"
FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --force)
      FORCE=1
      ;;
    -*)
      echo "Unknown flag: ${arg}" >&2
      exit 1
      ;;
    *)
      QUANT="${arg}"
      ;;
  esac
done

FILENAME="gemma-4-26B-A4B-it-${QUANT}.gguf"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${SCRIPT_DIR}/models"
TARGET="${MODELS_DIR}/${FILENAME}"

mkdir -p "${MODELS_DIR}"

if [[ "${FORCE}" -eq 0 && -s "${TARGET}" ]]; then
  SIZE="$(du -h "${TARGET}" | cut -f1)"
  echo "Already present, skipping download: ${TARGET} (${SIZE})"
  echo "Pass --force to re-download."
  exit 0
fi

echo "Fetching ${FILENAME} from ${REPO} into ${MODELS_DIR}/ ..."
uvx --from huggingface_hub hf download "${REPO}" "${FILENAME}" --local-dir "${MODELS_DIR}"

SIZE="$(du -h "${TARGET}" | cut -f1)"
echo "Done: ${TARGET} (${SIZE})"

if [[ "${QUANT}" != "${DEFAULT_QUANT}" ]]; then
  echo "NOTE: fetched a non-default quant (${QUANT})."
  echo "Set MODEL_FILE=${FILENAME} in .env before running 'docker compose up -d model-runner'."
fi

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
#
# --model qwen3.8-27b (M16-01) fetches the Qwen3.8-27B candidate instead
# (ggml-org/Qwen3.8-27B-GGUF: dense, Apache-2.0, default Q4_K_M; `mtp`
# fetches its speculative-decoding head). Those files are sha256-pinned
# below and verified after download.
#
#   fetch-model.sh [--model gemma|qwen3.8-27b] [quant|mtp] [--force]

MODEL="gemma"
QUANT=""
FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --force)
      FORCE=1
      ;;
    --model=*)
      MODEL="${arg#--model=}"
      ;;
    --model)
      MODEL="__next__"
      ;;
    -*)
      echo "Unknown flag: ${arg}" >&2
      exit 1
      ;;
    *)
      if [[ "${MODEL}" == "__next__" ]]; then
        MODEL="${arg}"
      else
        QUANT="${arg}"
      fi
      ;;
  esac
done

declare -A SHA256=()
case "${MODEL}" in
  gemma)
    REPO="ggml-org/gemma-4-26B-A4B-it-GGUF"
    DEFAULT_QUANT="Q4_0"
    QUANT="${QUANT:-${DEFAULT_QUANT}}"
    FILENAME="gemma-4-26B-A4B-it-${QUANT}.gguf"
    ;;
  qwen3.8-27b)
    REPO="ggml-org/Qwen3.8-27B-GGUF"
    DEFAULT_QUANT="Q4_K_M"
    QUANT="${QUANT:-${DEFAULT_QUANT}}"
    SHA256=(
      [Qwen3.8-27B-Q4_K_M.gguf]=c600de0300ae8a0eb3a6c0b8b5561b8b96f16bd2c863c2a66c42de29d391a747
      [Qwen3.8-27B-Q8_0.gguf]=aab65c67ef0dad127960efef9247f1832bca105faa1c7a052cc039b223cf86a1
      [mtp-Qwen3.8-27B-Q8_0.gguf]=6447a4e9d29fa6eba89ed508b2127b0803f91bac3ec6cec725fa209f2c3d309d
    )
    if [[ "${QUANT}" == "mtp" ]]; then
      FILENAME="mtp-Qwen3.8-27B-Q8_0.gguf"
    else
      FILENAME="Qwen3.8-27B-${QUANT}.gguf"
    fi
    if [[ -z "${SHA256[${FILENAME}]:-}" ]]; then
      echo "No pinned checksum for ${FILENAME}; known: ${!SHA256[*]}" >&2
      exit 1
    fi
    ;;
  *)
    echo "Unknown --model: ${MODEL} (gemma, qwen3.8-27b)" >&2
    exit 1
    ;;
esac

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

if [[ -n "${SHA256[${FILENAME}]:-}" ]]; then
  echo "Verifying sha256 ..."
  echo "${SHA256[${FILENAME}]}  ${TARGET}" | sha256sum -c -
fi

SIZE="$(du -h "${TARGET}" | cut -f1)"
echo "Done: ${TARGET} (${SIZE})"

if [[ "${MODEL}" == "gemma" && "${QUANT}" != "${DEFAULT_QUANT}" ]]; then
  echo "NOTE: fetched a non-default quant (${QUANT})."
  echo "Set MODEL_FILE=${FILENAME} in .env before running 'docker compose up -d model-runner'."
elif [[ "${MODEL}" != "gemma" ]]; then
  echo "Candidate model: set CANDIDATE_MODEL_FILE=${FILENAME} (if not the default) and run"
  echo "'docker compose --profile candidate up -d model-candidate'."
fi

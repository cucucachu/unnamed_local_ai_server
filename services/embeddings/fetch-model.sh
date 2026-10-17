#!/usr/bin/env bash
set -euo pipefail

# Fetches a pinned embedding GGUF into services/embeddings/models/ using the
# huggingface_hub CLI via `uvx` (no global install required), and checks its
# sha256. The default is the live model (M21-02); the others are the
# candidates compared in #389, kept so the comparison can be re-run.
#
#   fetch-model.sh [embeddinggemma|nomic|qwen3|bge-small] [--force]

MODEL="embeddinggemma"
FORCE=0
for arg in "$@"; do
  case "${arg}" in
    --force) FORCE=1 ;;
    -*)
      echo "Unknown flag: ${arg}" >&2
      exit 1
      ;;
    *) MODEL="${arg}" ;;
  esac
done

case "${MODEL}" in
  embeddinggemma)
    REPO="ggml-org/embeddinggemma-300M-GGUF"
    FILENAME="embeddinggemma-300M-Q8_0.gguf"
    SHA256="b5ce9d77a3fc4b3b39ccb5643c36777911cc4eb46a66962eadfa3f5f60490d63"
    ;;
  nomic)
    REPO="nomic-ai/nomic-embed-text-v1.5-GGUF"
    FILENAME="nomic-embed-text-v1.5.Q8_0.gguf"
    SHA256="3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7"
    ;;
  qwen3)
    REPO="Qwen/Qwen3-Embedding-0.6B-GGUF"
    FILENAME="Qwen3-Embedding-0.6B-Q8_0.gguf"
    SHA256="06507c7b42688469c4e7298b0a1e16deff06caf291cf0a5b278c308249c3e439"
    ;;
  bge-small)
    REPO="CompendiumLabs/bge-small-en-v1.5-gguf"
    FILENAME="bge-small-en-v1.5-q8_0.gguf"
    SHA256="ec38e8da142596baa913124ae50550de284b6916bf59577ef2f0cb9660c2f514"
    ;;
  *)
    echo "Unknown model: ${MODEL} (embeddinggemma, nomic, qwen3, bge-small)" >&2
    exit 1
    ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODELS_DIR="${SCRIPT_DIR}/models"
TARGET="${MODELS_DIR}/${FILENAME}"
mkdir -p "${MODELS_DIR}"

if [[ "${FORCE}" -eq 0 && -s "${TARGET}" ]]; then
  echo "Already present, skipping download: ${TARGET}"
else
  echo "Fetching ${FILENAME} from ${REPO} into ${MODELS_DIR}/ ..."
  uvx --from huggingface_hub hf download "${REPO}" "${FILENAME}" --local-dir "${MODELS_DIR}"
fi

echo "Verifying sha256 ..."
echo "${SHA256}  ${TARGET}" | sha256sum -c -
echo "Done: ${TARGET} ($(du -h "${TARGET}" | cut -f1))"

if [[ "${MODEL}" != "embeddinggemma" ]]; then
  echo "Not the default model: set EMBEDDINGS_MODEL_FILE=${FILENAME} (see .env.example)."
fi

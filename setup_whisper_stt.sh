#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

MODELS_DIR="$(pwd)/data/models"
WHISPER_DIR="${MODELS_DIR}/whisper.cpp"
WHISPER_MODEL_NAME="${1:-base}"
WHISPER_MODEL_FILE="ggml-${WHISPER_MODEL_NAME}.bin"
WHISPER_MODEL_PATH_PRIMARY="${MODELS_DIR}/${WHISPER_MODEL_FILE}"
WHISPER_MODEL_PATH_FALLBACK="${WHISPER_DIR}/models/${WHISPER_MODEL_FILE}"
WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH_PRIMARY}"
WHISPER_BIN_PATH="${WHISPER_DIR}/build/bin/whisper-cli"

echo "Preparing offline STT dependencies in: ${MODELS_DIR}"
mkdir -p "${MODELS_DIR}"

if [ -d "${WHISPER_DIR}/.git" ]; then
  echo "whisper.cpp already present; pulling latest..."
  git -C "${WHISPER_DIR}" pull --ff-only
elif [ -d "${WHISPER_DIR}" ]; then
  if [ -f "${WHISPER_DIR}/CMakeLists.txt" ] && [ -d "${WHISPER_DIR}/models" ]; then
    echo "whisper.cpp directory exists (non-git); reusing local sources..."
  else
    echo "whisper.cpp directory exists but looks invalid; recreating..."
    rm -rf "${WHISPER_DIR}"
    git clone --depth 1 https://github.com/ggerganov/whisper.cpp "${WHISPER_DIR}"
  fi
else
  echo "Cloning whisper.cpp..."
  git clone --depth 1 https://github.com/ggerganov/whisper.cpp "${WHISPER_DIR}"
fi

echo "Building whisper.cpp (whisper-cli)..."
cmake -S "${WHISPER_DIR}" -B "${WHISPER_DIR}/build"
cmake --build "${WHISPER_DIR}/build" -j"$(nproc)"

echo "Downloading model: ${WHISPER_MODEL_NAME}"
bash "${WHISPER_DIR}/models/download-ggml-model.sh" "${WHISPER_MODEL_NAME}"

if [ -f "${WHISPER_MODEL_PATH_PRIMARY}" ]; then
  WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH_PRIMARY}"
elif [ -f "${WHISPER_MODEL_PATH_FALLBACK}" ]; then
  WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH_FALLBACK}"
else
  echo "Expected model not found at ${WHISPER_MODEL_PATH_PRIMARY} or ${WHISPER_MODEL_PATH_FALLBACK}"
  exit 1
fi
if [ ! -x "${WHISPER_BIN_PATH}" ]; then
  echo "Expected binary not found at ${WHISPER_BIN_PATH}"
  exit 1
fi

cat <<EOF

Done. Whisper STT is installed.

Use these environment variables:
  export YUKTRA_WHISPER_CPP_BIN="${WHISPER_BIN_PATH}"
  export YUKTRA_WHISPER_MODEL_PATH="${WHISPER_MODEL_PATH}"

You can now restart the app and use mic STT fully offline.
EOF

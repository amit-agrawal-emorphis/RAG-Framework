#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PIPER_DIR="$(pwd)/data/models/piper"
VOICE_NAME="${1:-en_US-lessac-medium}"
PYTHON_BIN="${PYTHON_BIN:-python}"

VOICE_BASE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/lessac/medium"
VOICE_ONNX_URL="${VOICE_BASE_URL}/${VOICE_NAME}.onnx"
VOICE_JSON_URL="${VOICE_BASE_URL}/${VOICE_NAME}.onnx.json"

echo "Installing Piper TTS to: ${PIPER_DIR}"
mkdir -p "${PIPER_DIR}"

echo "Installing Piper runtime via pip (piper-tts)..."
"${PYTHON_BIN}" -m pip install --upgrade piper-tts
PIPER_BIN_FROM_PATH="$("${PYTHON_BIN}" -c 'import shutil; print(shutil.which("piper") or "")')"
if [ -z "${PIPER_BIN_FROM_PATH}" ] || [ ! -x "${PIPER_BIN_FROM_PATH}" ]; then
  echo "Could not find piper binary after pip install."
  echo "Try: ${PYTHON_BIN} -m pip install piper-tts"
  exit 1
fi
cp -f "${PIPER_BIN_FROM_PATH}" "${PIPER_DIR}/piper"
chmod +x "${PIPER_DIR}/piper"

echo "Downloading voice model: ${VOICE_NAME}"
curl -fL "${VOICE_ONNX_URL}" -o "${PIPER_DIR}/${VOICE_NAME}.onnx"
curl -fL "${VOICE_JSON_URL}" -o "${PIPER_DIR}/${VOICE_NAME}.onnx.json"

if [ ! -f "${PIPER_DIR}/${VOICE_NAME}.onnx" ]; then
  echo "Voice model missing at ${PIPER_DIR}/${VOICE_NAME}.onnx"
  exit 1
fi

cat <<EOF

Piper TTS setup complete.

Recommended env vars:
  export YUKTRA_PIPER_BIN="${PIPER_DIR}/piper"
  export YUKTRA_PIPER_MODEL_PATH="${PIPER_DIR}/${VOICE_NAME}.onnx"

Now restart:
  ./run_chatbot.sh
EOF

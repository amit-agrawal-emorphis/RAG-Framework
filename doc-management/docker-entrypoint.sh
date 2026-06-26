#!/usr/bin/env bash
set -e

DATA_DIR="${DATA_DIR:-/app/data}"
MODELS_DIR="${DATA_DIR}/models"

echo "============================================================"
echo "Yuktra IPC container starting"
echo "  DATA_DIR   = ${DATA_DIR}"
echo "  MODELS_DIR = ${MODELS_DIR}"
echo "============================================================"

missing=0

if [ ! -d "${MODELS_DIR}" ] || [ -z "$(ls -A "${MODELS_DIR}" 2>/dev/null)" ]; then
  echo ""
  echo "############################################################"
  echo "# ERROR: ${MODELS_DIR} is missing or empty."
  echo "#"
  echo "# Ingestion (PDF/video) WILL FAIL silently in the background"
  echo "# because whisper / piper / llama-cpp model files are not"
  echo "# present inside the container."
  echo "#"
  echo "# Fix: stop this container and re-run with the host data"
  echo "# directory mounted, e.g.:"
  echo "#"
  echo "#   docker run -d --name ima -p 8001:8001 \\"
  echo "#     -v \$(pwd)/data:/app/data ima"
  echo "#"
  echo "############################################################"
  missing=1
fi

for sub in models/whisper.cpp models/piper; do
  if [ ! -e "${DATA_DIR}/${sub}" ]; then
    echo "WARN: ${DATA_DIR}/${sub} not found — features depending on it will fail."
  fi
done

mkdir -p "${DATA_DIR}/logs" "${DATA_DIR}/docs" "${DATA_DIR}/Ingested"

if [ "${missing}" = "1" ] && [ "${YUKTRA_ALLOW_MISSING_MODELS}" != "1" ]; then
  echo ""
  echo "Refusing to start. Set YUKTRA_ALLOW_MISSING_MODELS=1 to start anyway."
  exit 1
fi

echo "Startup checks complete — launching uvicorn."
echo "============================================================"

exec "${@:-/app/server/server_main.bin}"

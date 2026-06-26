#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

# Load .env (repo root) – existing shell vars take precedence
if [ -f ".env" ]; then
    set -a
    # shellcheck source=/dev/null
    source .env
    set +a
fi
THREAD_LIMIT="${YUKTRA_LLM_N_THREADS:-2}"
export YUKTRA_LLM_N_THREADS="${THREAD_LIMIT}"
export OMP_NUM_THREADS="${OMP_NUM_THREADS:-${THREAD_LIMIT}}"
export OPENBLAS_NUM_THREADS="${OPENBLAS_NUM_THREADS:-${THREAD_LIMIT}}"
export MKL_NUM_THREADS="${MKL_NUM_THREADS:-${THREAD_LIMIT}}"
export NUMEXPR_NUM_THREADS="${NUMEXPR_NUM_THREADS:-${THREAD_LIMIT}}"
CPUSET="${YUKTRA_CPUSET:-0-$((THREAD_LIMIT - 1))}"
RUN_PREFIX=()
if command -v taskset >/dev/null 2>&1; then
  RUN_PREFIX=(taskset -c "${CPUSET}")
  echo "CPU affinity enabled: cores ${CPUSET}; thread limit ${THREAD_LIMIT}."
else
  echo "CPU affinity tool 'taskset' not found; thread limit ${THREAD_LIMIT} still applied."
fi
export PYTHONPATH="$(pwd)/doc-qna/backend:$(pwd)/doc-management/backend${PYTHONPATH:+:$PYTHONPATH}"
API_HOST="${YUKTRA_QNA_API_HOST:-0.0.0.0}"
API_PORT="${YUKTRA_QNA_API_PORT:-8008}"
export YUKTRA_QNA_API_BASE="${YUKTRA_QNA_API_BASE:-http://${API_HOST}:${API_PORT}}"
DEFAULT_WHISPER_BIN="$(pwd)/data/models/whisper.cpp/build/bin/whisper-cli"
DEFAULT_WHISPER_MODEL_PRIMARY="$(pwd)/data/models/ggml-base.bin"
DEFAULT_WHISPER_MODEL_FALLBACK="$(pwd)/data/models/whisper.cpp/models/ggml-base.bin"
if [ -f "${DEFAULT_WHISPER_MODEL_PRIMARY}" ]; then
  DEFAULT_WHISPER_MODEL="${DEFAULT_WHISPER_MODEL_PRIMARY}"
else
  DEFAULT_WHISPER_MODEL="${DEFAULT_WHISPER_MODEL_FALLBACK}"
fi
if [ -x "${DEFAULT_WHISPER_BIN}" ] && [ -f "${DEFAULT_WHISPER_MODEL}" ]; then
  export YUKTRA_WHISPER_CPP_BIN="${YUKTRA_WHISPER_CPP_BIN:-${DEFAULT_WHISPER_BIN}}"
  export YUKTRA_WHISPER_MODEL_PATH="${YUKTRA_WHISPER_MODEL_PATH:-${DEFAULT_WHISPER_MODEL}}"
  export YUKTRA_WHISPER_LANG="${YUKTRA_WHISPER_LANG:-auto}"
  echo "Offline STT enabled (whisper.cpp)."
else
  echo "Offline STT not found yet. Run ./setup_whisper_stt.sh to install it."
fi

DEFAULT_PIPER_BIN="$(pwd)/data/models/piper/piper"
DEFAULT_PIPER_MODEL="$(pwd)/data/models/piper/en_IN-medium.onnx"
if [ -x "${DEFAULT_PIPER_BIN}" ] && [ -f "${DEFAULT_PIPER_MODEL}" ]; then
  export YUKTRA_PIPER_BIN="${YUKTRA_PIPER_BIN:-${DEFAULT_PIPER_BIN}}"
  export YUKTRA_PIPER_MODEL_PATH="${YUKTRA_PIPER_MODEL_PATH:-${DEFAULT_PIPER_MODEL}}"
  echo "Offline TTS enabled (piper)."
else
  echo "Offline TTS not found yet. Set YUKTRA_PIPER_BIN and YUKTRA_PIPER_MODEL_PATH after installing piper."
fi

echo "Starting QnA backend API on ${API_HOST}:${API_PORT}…"
"${RUN_PREFIX[@]}" uvicorn api:app --host "${API_HOST}" --port "${API_PORT}" >/tmp/yuktra_qna_api.log 2>&1 &
API_PID=$!
cleanup() {
  kill "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "Starting Streamlit Q&A (doc-qna/frontend)…"
echo "The app opens immediately; the UI shows “Loading models…” until ${API_HOST}:${API_PORT} is ready."
echo "Vector store default: data/common/document_text (override with RAG_STORE_TENANT / RAG_STORE_INDEX_NAME)"
# Do not `exec` here: the shell must survive so EXIT runs cleanup and stops Uvicorn.
"${RUN_PREFIX[@]}" streamlit run doc-qna/frontend/streamlit_app.py "$@"

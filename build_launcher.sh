#!/usr/bin/env bash
# Build the single Yuktra desktop binary (backend + Streamlit frontend) with Nuitka.
#
# Notes:
# - Streamlit UI assets live under streamlit/static (wheel package data):
#   --include-package=streamlit is NOT enough; you also need --include-package-data=streamlit.
# - Streamlit runs streamlit_app.py FROM SOURCE, so streamlit_app.py / streamlit_theme.py
#   are shipped as data files and the frontend dir modules are force-included.
# - faster-whisper pulls compiled backends (ctranslate2 / av / onnxruntime / tokenizers).
# - This builds for the OS it runs on. Linux -> yukt.bin (use inside Docker).
#   For a Windows .exe you must run Nuitka on Windows (see build_*.ps1).
#
# Fast build:  FAST=1 ./build_launcher.sh   (adds --lto=no --jobs=N --no-progressbar)
set -euo pipefail
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"

FAST_FLAGS=()
if [ "${FAST:-0}" = "1" ]; then
  FAST_FLAGS=(--lto=no --jobs="$(nproc)" --no-progressbar)
fi

# Ensure pdf.js is downloaded before packaging so it gets bundled by Nuitka.
PYTHONPATH="doc-qna/backend:doc-management/backend" "$PYTHON" -c \
  "import sys; sys.path.insert(0,'doc-qna/backend'); from api import _ensure_pdfjs; _ensure_pdfjs()"

# Make the backend/frontend source modules importable so --include-module can resolve them.
export PYTHONPATH="doc-qna/backend:doc-qna/frontend:doc-management/backend${PYTHONPATH:+:$PYTHONPATH}"

exec "$PYTHON" -m nuitka \
  --standalone \
  "${FAST_FLAGS[@]}" \
  --assume-yes-for-downloads \
  --disable-plugin=anti-bloat \
  --include-package=streamlit            --include-package-data=streamlit \
  --include-distribution-metadata=streamlit \
  --include-package=streamlit_pdf        --include-package-data=streamlit_pdf \
  --include-package=streamlit_pdf_viewer --include-package-data=streamlit_pdf_viewer \
  --include-package=llama_cpp            --include-package-data=llama_cpp \
  --include-package=faiss                --include-package-data=faiss \
  --include-package=faster_whisper       --include-package-data=faster_whisper \
  --include-package=ctranslate2          --include-package-data=ctranslate2 \
  --include-package=onnxruntime          --include-package-data=onnxruntime \
  --include-package=av                   --include-package-data=av \
  --include-package=tokenizers \
  --include-package=huggingface_hub \
  --include-package=lingua               --include-package-data=lingua \
  --include-package=uvicorn \
  --include-package=fastapi \
  --include-package=starlette \
  --include-package=pydantic \
  --include-package=anyio \
  --include-package=webview \
  --include-package=pyarrow              --include-package-data=pyarrow \
  --include-package=pandas \
  --include-package=altair               --include-package-data=altair \
  --include-module=python_multipart \
  --include-module=multipart \
  --include-module=numpy \
  --include-module=pypdf \
  --include-module=pypdfium2 \
  --include-module=PIL \
  --include-module=markdown \
  --include-module=tqdm \
  --include-module=api \
  --include-module=chat_history_db \
  --include-module=logger \
  --include-module=model_registry \
  --include-module=prompts \
  --include-module=qna_service \
  --include-module=rag_utils \
  --include-module=store_runtime_config \
  --include-module=stt_service \
  --include-module=tts_service \
  --include-module=streamlit_app \
  --include-module=streamlit_theme \
  --include-module=launcher_config \
  --include-data-dir=data=data \
  --include-data-dir=doc-qna/backend/pdfjs=doc-qna/backend/pdfjs \
  --include-data-file=doc-qna/frontend/streamlit_app.py=doc-qna/frontend/streamlit_app.py \
  --include-data-file=doc-qna/frontend/streamlit_theme.py=doc-qna/frontend/streamlit_theme.py \
  --include-data-file=doc-qna/frontend/launcher.py=doc-qna/frontend/launcher.py \
  --include-data-file=doc-qna/frontend/launcher_config.py=doc-qna/frontend/launcher_config.py \
  --include-data-file=doc-qna/backend/launcher.py=doc-qna/backend/launcher.py \
  --include-data-file=doc-qna/backend/chat_history_db.py=doc-qna/backend/chat_history_db.py \
  --output-filename=yukt.bin \
  launcher.py

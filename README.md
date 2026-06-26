# Offline RAG (Portable Embeddings ZIP) - llama.cpp

This project builds a small offline Q&A system for a fixed set of documents:
- You (server/your side) ingest documents once and precompute embeddings.
- You ship the exported vector store (as a ZIP) to the client laptop.
- The client runs local retrieval + local LLM answering fully offline.

## Folder layout

Top-level folders use hyphens (**`doc-qna`**, **`doc-management`**). Python modules live **directly** under each app’s **`backend/`** (add those paths to **`PYTHONPATH`**). Ingestion uses **Markdown**-style chunking via **`markdown_ingest.py`** and Docling (**`docling_loader.py`**).

```
yuktra-ipc/
  doc-qna/
    frontend/           # Streamlit UI only (HTTP client → QnA API)
      launcher.py       # Streamlit + webview (requires API already running, unless you use root launcher.py)
      launcher_config.py  # Nuitka / binary launch mode helpers (YUKTRA_IPC_LAUNCH_MODE, …)
      streamlit_app.py
      requirements.txt
    backend/            # api.py, qna_service.py, rag_utils.py, chat_history_db.py, …
      launcher.py       # uvicorn API only (same PYTHONPATH as run_chatbot.sh)
      requirements.txt
  doc-management/
    frontend/           # Optional ingestion UI (Streamlit)
      launcher.py       # Streamlit + webview (ingestion is CLI-only; see backend/launcher.py)
      launcher_config.py  # Nuitka / binary launch mode (YUKTRA_IPC_LAUNCH_MODE, …)
      streamlit_app.py  # Placeholder UI shell
      requirements.txt
    backend/            # ingest_and_export.py, docling_loader.py, markdown_ingest.py
      launcher.py       # Thin wrapper → ingest_and_export (sets PYTHONPATH)
      requirements.txt
  data/
    docs/
    <tenant>/<index_name>/   # e.g. common/document_text/
    models/
    logs/
    chat_history_db/
  launcher.py           # Starts QnA API if port free, then doc-qna/frontend/launcher.py (Streamlit child mode)
  run_chatbot.sh        # Dev: uvicorn (API) + streamlit (frontend), same shell
  requirements.txt      # Installs both apps (+ Nuitka for builds)
```

## Architecture: doc-qna frontend vs backend

The Q&A experience is split so the **UI** and **model/RAG runtime** can run as separate processes (and so the UI could be replaced without touching inference code).

| Layer | Location | Role |
|--------|-----------|------|
| **Frontend** | `doc-qna/frontend/streamlit_app.py` | Layout, chat history sidebar, PDF/source display. Talks to the backend **only over HTTP** (`urllib`): health check, chat, and session APIs. Adds `doc-qna/backend` to `sys.path` mainly so it can reuse **`logger`** for consistent file logging under `data/logs/`. |
| **Backend** | `doc-qna/backend/` | **FastAPI** app (`api.py`): JSON REST + **SSE** streaming for answers. **RAG and llama.cpp** run inside `qna_service` / related modules on a **single-worker thread pool** (one LLM call at a time, matching the old all-in-Streamlit behavior). **SQLite** chat history is opened from `data/chat_history_db/` (override with `CHAT_DB_PATH`). Model warmup runs at API startup unless `YUKTRA_QNA_SKIP_WARMUP` is set. |

**Default wiring:** the frontend expects the API at **`YUKTRA_QNA_API_BASE`** (default `http://127.0.0.1:8008`). `./run_chatbot.sh` sets `PYTHONPATH`, starts **`uvicorn api:app`** (with **`doc-qna/backend`** on the path), then **`streamlit run doc-qna/frontend/streamlit_app.py`**. The UI polls **`GET /health`** until the backend is ready (first load can be slow while models and the index load).

**Desktop:** **`python doc-qna/backend/launcher.py`** runs **only** the API; **`python doc-qna/frontend/launcher.py`** runs **only** Streamlit + **`pywebview`** (or the browser) and **requires** the API to be up first—it waits for **`GET /health`** (timeout **`YUKTRA_QNA_LAUNCHER_WAIT_API_SEC`**, default `360`; set to `0` to skip that preflight and rely on the in-app wait). Repo root **`launcher.py`** starts the backend via **`doc-qna/backend/launcher.py`** when **`YUKTRA_QNA_API_HOST`/`PORT`** is not already accepting connections, then runs the frontend launcher; API logs go to **`YUKTRA_QNA_API_LOG`** or **`yuktra_qna_api.log`** in the temp directory. If the API is already running, root **`launcher.py`** does not start a second one.

### QnA HTTP API (FastAPI)

All paths are relative to `YUKTRA_QNA_API_BASE`. Request/response bodies are JSON unless noted.

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/health` | Returns `{ "ok": true }` when the server is up (used for startup gating). |
| `POST` | `/chat/ask` | Body: `{ "question": "..." }`. Response: `{ "answer": "...", "sources": [ ... ] }`. |
| `POST` | `/chat/ask/stream` | Same body; **Server-Sent Events** (`text/event-stream`). Each event is a JSON object; types include `delta` (token text), `done` (final answer + sources), `error`. |
| `POST` | `/sessions` | Create a chat session; response includes `session_id`. |
| `GET` | `/sessions/latest` | Latest `session_id` (if any). |
| `GET` | `/sessions?limit=100` | List session summaries. |
| `GET` | `/sessions/{session_id}/messages` | Load messages for a session. |
| `POST` | `/sessions/{session_id}/messages` | Append a message; body: `role`, `content`, optional `sources`. |

Useful environment variables: **`YUKTRA_QNA_API_BASE`** / **`YUKTRA_QNA_API_HOST`** / **`YUKTRA_QNA_API_PORT`** (see `run_chatbot.sh`), **`YUKTRA_QNA_LAUNCHER_WAIT_API_SEC`** (how long **`doc-qna/frontend/launcher.py`** waits for **`/health`** before exit; `0` skips), **`YUKTRA_QNA_CHAT_ASK_TIMEOUT_SEC`** (frontend read timeout for ask/stream), **`YUKTRA_QNA_BACKEND_WAIT_MAX`** (how long the Streamlit UI waits for `/health`), **`CHAT_DB_PATH`**, **`YUKTRA_QNA_SKIP_WARMUP`**.

Exported files inside each vector-store directory:
- `index.faiss`, `metadata.json`, `config.json` (and optional `vectors.npy` in older stores)

## Requirements (Python)

Full install:
```bash
pip install -r requirements.txt
```

**Linux (recommended):** `docling` pulls in PyTorch. Default PyPI `torch` on Linux is the CUDA build (~1 GB+ of NVIDIA wheels). To avoid large downloads and network timeouts, pre-install CPU PyTorch first (same approach as `doc-management/Dockerfile`):

```bash
pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt
```

Verify: `python -c "import torch; print(torch.__version__, torch.cuda.is_available())"` should show `False` for CUDA.

### Windows — GPU auto-detect (iGPU + NVIDIA, CPU fallback)

`pip install` alone installs a **CPU-only** `llama-cpp-python` wheel. For automatic GPU use on Windows:

1. Install the [LunarG Vulkan SDK](https://vulkan.lunarg.com/sdk/home#windows) (once per build machine).
2. After `pip install -r requirements.txt`, run:

```powershell
.\setup_llama_gpu_windows.ps1
```

3. Dev run:

```powershell
.\run_yuktra_dev.ps1
```

4. Release `.exe` build (Vulkan llama-cpp is built automatically when the SDK is present):

```powershell
.\doc-qna\build.ps1 -Setup
.\doc-qna\build.ps1
.\build_installer.ps1
```

At runtime the backend calls `llama_supports_gpu_offload()` — **True** on machines with an iGPU or NVIDIA GPU (Vulkan), **False** on CPU-only PCs. No env vars required. Optional: `YUKTRA_LLM_N_GPU_LAYERS=0` forces CPU; `YUKTRA_WHISPER_DEVICE=auto` (default) uses NVIDIA CUDA for STT when available.

Q&A only or ingestion only:
```bash
pip install -r doc-qna/frontend/requirements.txt -r doc-qna/backend/requirements.txt
pip install -r doc-management/frontend/requirements.txt -r doc-management/backend/requirements.txt
```

## 1) Ingest + Export (run once by you)

Put documents in `data/docs/`. From the **repo root**, either:

**Option A — module (recommended):** from repo root, **`python3 -m ingest_and_export`** works without **`PYTHONPATH`** (see root `ingest_and_export.py` shim), or set paths explicitly:
```bash
python3 -m ingest_and_export \
  --docs_dir data/docs \
  --out_dir data \
  --zip_out data/common/document_text.zip \
  --enable_multitenancy \
  --tenant_name common \
  --embedding_model data/models/embeddinggemma-300M-Q8_0.gguf \
  --llm_model data/models/gemma-3-4b-it-Q4_K_M.gguf \
  --llm_device cpu
```

Ingestion is **Markdown-only**: **PDF/DOCX** go through Docling → Markdown (tables, `[Image]` placeholders, captions); **`.md` / `.txt`** use the same heading-based chunker on file contents. For PDFs, **`pdftotext`** (or **pypdf**) decides OCR vs embedded text (see **`--docling_force_backend_text`**, **`--docling_ocr_always`**). Optional **`--docling_embed_markdown_images`** embeds figures as base64 (large chunks).

**Option B — doc-management backend launcher:**
```bash
python3 doc-management/backend/launcher.py \
  --docs_dir data/docs \
  --out_dir data \
  ...
```

Notes:
- `--embedding_model` must be a local GGUF path; use the **same** file at query time.
- Ingestion imports **doc-qna/backend** modules (`rag_utils`, `logger`, etc.) for embeddings, FAISS export, and chunking helpers.
- Optional doc-management Streamlit desktop shell: **`python doc-management/frontend/launcher.py`**. There is no ingestion API process—run **`python doc-management/backend/launcher.py …`** when you need to ingest.

## 2) Ship / unzip the vector store (client)

```bash
unzip data/common/document_text.zip -d .
```

Copy GGUF files into `data/models/` on the client if needed.

## Streamlit UI + API (quick test)

Recommended (one terminal, API + Streamlit):

```bash
./run_chatbot.sh
```

Manual two-terminal setup from repo root:

```bash
# Terminal 1 — either:
python doc-qna/backend/launcher.py
# or:
export PYTHONPATH="doc-qna/backend:doc-management/backend${PYTHONPATH:+:$PYTHONPATH}"
uvicorn api:app --host 127.0.0.1 --port 8008
# Terminal 2:
streamlit run doc-qna/frontend/streamlit_app.py
```

See [Architecture: doc-qna frontend vs backend](#architecture-doc-qna-frontend-vs-backend) for how the UI calls the API. The RAG backend reads the vector store under `data/<tenant>/<index_name>/` (defaults: `common` / `document_text`).

## Important model detail

1. The client must have both GGUF files locally for a fully offline run.
2. Keep embedding and generation settings aligned with your chosen GGUF models.

---

## Deploying doc-management

doc-management is a containerised FastAPI application served by **Gunicorn + UvicornWorker**.  
It exposes port **8001** inside the container (mapped to host port **8000** by default).

### Prerequisites

- Docker 24+
- Access to `registry.emorphis.com` (credentials in GitLab CI variables / `.env`)
- SSH access to the deploy server (for manual deploy)

### Step 1 — Build the Docker image

The Dockerfile handles everything internally via multi-stage build (React build, Nuitka compilation, runtime packaging). No separate compile step is needed.

```bash
docker build \
  -f doc-management/Dockerfile \
  -t registry.emorphis.com/plantpilot/yuktra-ipc/doc-management:latest \
  .
```

The final image contains only compiled binaries and React static files — no Python source code.

### Step 3 — Push to registry

```bash
docker push registry.emorphis.com/plantpilot/yuktra-ipc/doc-management:latest
```

### Step 4 — Local smoke test

```bash
docker run --rm \
  -p 8000:8001 \
  -v $(pwd)/data:/app/data \
  -e YUKTRA_ALLOW_MISSING_MODELS=1 \
  registry.emorphis.com/plantpilot/yuktra-ipc/doc-management:latest
```

- Open `http://localhost:8000` → React UI
- `curl http://localhost:8000/api/health` → `{"status":"ok"}`
- Container logs show `[gunicorn] Booting worker with pid: …` (multiple workers)

### Step 5 — Deploy via docker-compose

```bash
# On the deploy server, from the deployment directory:
IMAGE_TAG=latest HOST_DATA_PATH=/path/to/data docker compose up -d doc-management
```

### Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `IMAGE_TAG` | `latest` | Docker image tag to deploy |
| `HOST_DATA_PATH` | `./data` | Host path mounted as `/app/data` in the container |
| `DOC_MANAGEMENT_PORT` | `8000` | Host port that maps to container port 8001 |
| `YUKTRA_DM_WORKERS` | `2×CPU+1` | Gunicorn worker count (empty = auto-detect) |
| `YUKTRA_DM_API_HOST` | `0.0.0.0` | Bind address inside the container |
| `YUKTRA_DM_API_PORT` | `8001` | Bind port inside the container |
| `YUKTRA_ALLOW_MISSING_MODELS` | `1` | Set to `0` to block startup when models are absent |

### GPU support

The image ships llama-cpp-python with CUDA + Vulkan backends plus whisper CUDA + CPU. Runtime auto-detects via `llama_supports_gpu_offload()` and falls back to CPU when no GPU is present.

| Hardware | Ingest embeddings (llama.cpp) | Whisper STT |
|----------|------------------------------|-------------|
| NVIDIA GPU | CUDA (Vulkan also available) | CUDA (when `nvidia-smi` works) |
| Intel/AMD iGPU | Vulkan on GPU (needs `/dev/dri`) | CPU |
| No GPU | CPU | CPU |

The Docker image builds llama-cpp-python with **both CUDA and Vulkan** backends; runtime picks the available GPU and falls back to CPU.

`docker-compose.yml` enables **both** NVIDIA and iGPU passthrough for `doc-management`:

- `gpus: all` + `NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics` — NVIDIA Vulkan ICD
- `devices: /dev/dri` — Intel/AMD integrated GPU
- `YUKTRA_DM_USE_GPU=auto` — runtime auto-detect + CPU fallback

```bash
docker compose up -d doc-management
```

**CPU-only host** (no NVIDIA toolkit, or `/dev/dri` missing):

```bash
docker compose -f docker-compose.yml -f docker-compose.cpu.yml up -d doc-management
```

GitLab `deploy_management` writes the same GPU-enabled compose to the server.

**Linux local dev:** run `./setup_llama_vulkan.sh` after `pip install` (see repo root).

**Windows:** run `./setup_llama_gpu_windows.ps1` after `pip install` (see Requirements above).

### CI/CD flow

```
master push
  └─► compile_backend      (Nuitka compilation → doc-management/dist/ artifact)
  └─► doc_management_build (docker build + push → registry)
        └─► deploy_management  [manual trigger] (SSH deploy to server)
```

The `build_installer` job (Windows .exe for doc-qna) runs independently and is unaffected.

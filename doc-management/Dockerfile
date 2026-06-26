ARG CUDA_VERSION=12.4.1
ARG CUDA_ARCHITECTURES=75;86;89

# ─── Stage 1: React UI build (unchanged) ─────────────────────────────────────
FROM node:20-bookworm-slim AS react-build

WORKDIR /app/react-app

COPY doc-management/frontend/react-app/package.json \
     doc-management/frontend/react-app/package-lock.json* ./

RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY doc-management/frontend/react-app/ ./

RUN npm run build

RUN test -f /app/react-app/dist/index.html || \
    (echo "ERROR: React build failed" && exit 1)


# ─── Stage 2: whisper.cpp CUDA build (unchanged) ─────────────────────────────
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS whisper-build-cuda

ARG CUDA_ARCHITECTURES

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake build-essential curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

RUN for i in 1 2 3; do \
        git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
        echo "git clone attempt $i failed, retrying..."; sleep 10; \
    done

WORKDIR /opt/whisper.cpp

RUN cmake -B build \
    -DGGML_CUDA=ON \
    -DGGML_NATIVE=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON \
    -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"

RUN cmake --build build --target whisper-cli -j"$(nproc)"

RUN test -f /opt/whisper.cpp/build/bin/whisper-cli || \
    (echo "ERROR: whisper-cli (CUDA) build failed" && exit 1)


# ─── Stage 3: whisper.cpp Vulkan build (unchanged) ───────────────────────────
FROM ubuntu:22.04 AS whisper-build-vk

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ca-certificates build-essential cmake ninja-build \
    software-properties-common gnupg \
    && wget -qO /etc/apt/trusted.gpg.d/lunarg.asc https://packages.lunarg.com/lunarg-signing-key-pub.asc \
    && wget -qO /etc/apt/sources.list.d/lunarg-vulkan-jammy.list https://packages.lunarg.com/vulkan/lunarg-vulkan-jammy.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    vulkan-sdk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

RUN for i in 1 2 3; do \
        git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
        echo "git clone attempt $i failed, retrying..."; sleep 10; \
    done

WORKDIR /opt/whisper.cpp

RUN bash ./models/download-ggml-model.sh base

RUN cmake -B build \
    -DGGML_VULKAN=ON \
    -DGGML_NATIVE=OFF \
    -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_AVX512=OFF \
    -DGGML_FMA=OFF -DGGML_F16C=OFF \
    -DBUILD_SHARED_LIBS=OFF \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON

RUN cmake --build build --target whisper-cli -j"$(nproc)"

RUN test -f /opt/whisper.cpp/build/bin/whisper-cli || \
    (echo "ERROR: whisper-cli (Vulkan) build failed" && exit 1)


# ─── Stage 3b: whisper.cpp CPU-only build (AVX2, no GPU required) ────────────
# Used as the no-GPU fallback; pure CPU AVX2 is 5-10x faster than the Vulkan
# build running on CPU without a physical Vulkan-capable GPU.
FROM ubuntu:22.04 AS whisper-build-cpu

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git cmake build-essential ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

RUN for i in 1 2 3; do \
        git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
        echo "git clone attempt $i failed, retrying..."; sleep 10; \
    done

WORKDIR /opt/whisper.cpp

RUN cmake -B build \
    -DGGML_NATIVE=OFF \
    -DGGML_AVX=ON \
    -DGGML_AVX2=ON \
    -DGGML_FMA=ON \
    -DBUILD_SHARED_LIBS=OFF \
    -DWHISPER_BUILD_TESTS=OFF \
    -DWHISPER_BUILD_EXAMPLES=ON

RUN cmake --build build --target whisper-cli -j"$(nproc)"

RUN test -f /opt/whisper.cpp/build/bin/whisper-cli || \
    (echo "ERROR: whisper-cli (CPU) build failed" && exit 1)


# ─── Stage 4: llama-cpp-python Vulkan wheel (unchanged) ──────────────────────
FROM ubuntu:22.04 AS llama-build

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

RUN apt-get update && apt-get install -y --no-install-recommends \
    git wget curl ca-certificates build-essential cmake ninja-build \
    software-properties-common gnupg \
    && wget -qO /etc/apt/trusted.gpg.d/lunarg.asc https://packages.lunarg.com/lunarg-signing-key-pub.asc \
    && wget -qO /etc/apt/sources.list.d/lunarg-vulkan-jammy.list https://packages.lunarg.com/vulkan/lunarg-vulkan-jammy.list \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
    vulkan-sdk \
    python3.11 python3.11-dev python3.11-venv \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python3

RUN --mount=type=cache,target=/root/.cache/pip \
    python -m ensurepip --upgrade && \
    for i in 1 2 3 4 5; do \
        python -m pip install --upgrade pip setuptools wheel ninja && break; \
        echo "pip bootstrap attempt $i failed, retrying in 15s..."; sleep 15; \
    done

ENV CMAKE_ARGS="-DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_AVX512=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF"
ENV FORCE_CMAKE=1

RUN python -m pip wheel llama-cpp-python --no-binary llama-cpp-python -w /wheels


# ─── Stage 5: Nuitka module-mode compilation ─────────────────────────────────
# Compiles ONLY project source to .so extension modules.
# Third-party packages are pip-installed normally and copied to the runtime stage.
FROM ubuntu:22.04 AS nuitka-build

ENV DEBIAN_FRONTEND=noninteractive \
    NUITKA_CACHE_DIR=/nuitka-cache \
    CCACHE_DIR=/ccache \
    PIP_DEFAULT_TIMEOUT=300 \
    PIP_RETRIES=10

RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common gpg-agent \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-dev python3.11-venv \
        gcc g++ make patchelf ccache zlib1g-dev \
        libgl1 libglib2.0-0 libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# ensurepip bootstraps pip for Python 3.11 (python3-pip is for system Python 3.10)
RUN --mount=type=cache,target=/root/.cache/pip \
    python3.11 -m ensurepip --upgrade && \
    for i in 1 2 3 4 5; do \
        python3.11 -m pip install --upgrade pip setuptools wheel && break; \
        echo "pip bootstrap attempt $i failed, retrying in 15s..."; sleep 15; \
    done

# Install the Vulkan llama-cpp-python wheel from the llama-build stage.
COPY --from=llama-build /wheels /tmp/wheels
RUN python3.11 -m pip install --no-cache-dir /tmp/wheels/llama_cpp_python*.whl \
    && rm -rf /tmp/wheels

WORKDIR /src

COPY doc-management/backend/requirements.txt /tmp/dm.txt
COPY doc-qna/backend/requirements.txt        /tmp/dq.txt
# Remove llama-cpp-python from dq.txt — already installed via the custom wheel above.
RUN grep -v '^llama-cpp-python' /tmp/dq.txt > /tmp/dq-no-llama.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python3.11 -m pip install nuitka ordered-set zstandard "gunicorn[gthread]" "uvicorn[standard]"

# Torch CPU-only (only needed so Nuitka can locate it; no GPU required at compile time).
RUN --mount=type=cache,target=/root/.cache/pip \
    python3.11 -m pip install \
    torch torchvision \
    --index-url https://download.pytorch.org/whl/cpu

RUN --mount=type=cache,target=/root/.cache/pip \
    python3.11 -m pip install -r /tmp/dm.txt

RUN --mount=type=cache,target=/root/.cache/pip \
    python3.11 -m pip install -r /tmp/dq-no-llama.txt

# Copy application source files flat into /src/ — same layout as existing Dockerfile.
COPY doc-management/frontend/api_server.py         ./
COPY doc-management/frontend/export_queue_store.py ./
COPY doc-management/server_main.py                 ./
COPY doc-management/backend/launcher.py            ./
COPY doc-management/backend/ingest_and_export.py   ./
COPY doc-management/backend/docling_loader.py      ./
COPY doc-management/backend/markdown_ingest.py     ./
COPY doc-management/backend/ground_id_grouping.py  ./
COPY doc-management/backend/vector_store_cleanup.py ./
COPY doc-management/backend/hierarchy/             ./hierarchy/
COPY doc-qna/backend/logger.py                     ./
COPY doc-qna/backend/store_runtime_config.py       ./
COPY doc-qna/backend/rag_utils.py                  ./

# Apply docling patches before compilation (patched .py files are in site-packages;
# they are copied to the runtime stage via /usr/local/lib/python3.11).
COPY doc-management/patch_docling.py /tmp/patch_docling.py
RUN python3.11 /tmp/patch_docling.py

# Add __init__.py to all source subdirectories so Nuitka recurses into packages.
# Build-time only — source repo files are not touched.
RUN find /src -type d -exec touch {}/__init__.py \;

# Compile hierarchy/ as a package (.so bundles all hierarchy sub-modules).
RUN --mount=type=cache,target=/ccache,sharing=locked \
    --mount=type=cache,target=/nuitka-cache,sharing=locked \
    python3.11 -m nuitka \
        --module \
        --include-package=hierarchy \
        --jobs=$(nproc) \
        --output-dir=dist/ \
        hierarchy/

# Compile each flat source file individually.
# Each produces its own .so; ccache skips unchanged files on rebuild.
RUN --mount=type=cache,target=/ccache,sharing=locked \
    --mount=type=cache,target=/nuitka-cache,sharing=locked \
    for f in server_main api_server export_queue_store launcher ingest_and_export docling_loader \
              markdown_ingest ground_id_grouping vector_store_cleanup \
              logger store_runtime_config rag_utils; do \
        python3.11 -m nuitka \
            --module \
            --jobs=$(nproc) \
            --output-dir=dist/ \
            ${f}.py || exit 1; \
    done

# Collect only compiled .so files — leaves intermediate .build/ dirs in the builder.
# Wildcard in find handles both x86_64 (amd64) and aarch64 (arm64).
RUN mkdir /dist_clean && \
    find /src/dist -maxdepth 1 -name "*.so" -exec cp {} /dist_clean/ \;


# ─── Stage 6: Runtime ─────────────────────────────────────────────────────────
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive \
    NVIDIA_VISIBLE_DEVICES=all \
    NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics

RUN apt-get update && apt-get install -y --no-install-recommends \
    software-properties-common gpg-agent \
    && add-apt-repository -y ppa:deadsnakes/ppa \
    && apt-get update && apt-get install -y --no-install-recommends \
        python3.11 python3.11-venv \
        curl ffmpeg poppler-utils tesseract-ocr \
        libgl1 libglib2.0-0 libgomp1 \
        ca-certificates libvulkan1 mesa-vulkan-drivers \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python3 && \
    ln -sf /usr/bin/python3.11 /usr/local/bin/python

# Copy pip-installed packages from builder — no re-download needed.
# Includes docling (patched), torch, fastapi, gunicorn, etc.
COPY --from=nuitka-build /usr/local/lib/python3.11 /usr/local/lib/python3.11

# Install llama-cpp-python Vulkan wheel into the runtime.
COPY --from=llama-build /wheels /tmp/wheels
RUN python3.11 -m pip install --no-cache-dir /tmp/wheels/llama_cpp_python*.whl \
    && rm -rf /tmp/wheels

WORKDIR /app

ENV PYTHONPATH=/app \
    DATA_DIR=/app/data \
    YUKTRA_DM_DATA_DIR=/app/data \
    YUKTRA_DM_REACT_DIR=/app/static \
    YUKTRA_DM_INGEST_BIN=/app/ingest.sh \
    YUKTRA_DM_API_HOST=0.0.0.0 \
    YUKTRA_DM_API_PORT=8001 \
    YUKTRA_DM_WORKERS="" \
    YUKTRA_LLM_N_GPU_LAYERS=-1 \
    YUKTRA_ALLOW_MISSING_MODELS=1

# Copy compiled .so extension modules. No .py source files.
# dist_clean/ contains only .so files — no intermediate Nuitka build artifacts.
COPY --from=nuitka-build /dist_clean/ /app/

# React static assets
COPY --from=react-build /app/react-app/dist /app/static/

# Whisper binary selector + all three backends
COPY --from=whisper-build-vk   /opt/whisper.cpp                           /opt/whisper.cpp
COPY --from=whisper-build-cuda /opt/whisper.cpp/build/bin/whisper-cli     /opt/whisper.cpp/build/bin/whisper-cli-cuda
COPY --from=whisper-build-cpu  /opt/whisper.cpp/build/bin/whisper-cli     /opt/whisper.cpp/build/bin/whisper-cli-cpu

# Selector: CUDA GPU → cuda build; otherwise → AVX2 CPU build (faster than Vulkan on CPU).
# Vulkan build kept at whisper-cli-vk for explicit AMD/Intel GPU use via YUKTRA_WHISPER_CPP_BIN.
RUN mv /opt/whisper.cpp/build/bin/whisper-cli /opt/whisper.cpp/build/bin/whisper-cli-vk && \
    printf '%s\n' \
'#!/bin/sh' \
'if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q libcuda.so.1; then' \
'  exec /opt/whisper.cpp/build/bin/whisper-cli-cuda "$@"' \
'fi' \
'exec /opt/whisper.cpp/build/bin/whisper-cli-cpu "$@"' \
    > /opt/whisper.cpp/build/bin/whisper-cli && \
    chmod +x /opt/whisper.cpp/build/bin/whisper-cli \
             /opt/whisper.cpp/build/bin/whisper-cli-cuda \
             /opt/whisper.cpp/build/bin/whisper-cli-vk \
             /opt/whisper.cpp/build/bin/whisper-cli-cpu

ENV WHISPER_CPP_PATH="/opt/whisper.cpp/build/bin/whisper-cli" \
    YUKTRA_WHISPER_CPP_BIN="/opt/whisper.cpp/build/bin/whisper-cli" \
    YUKTRA_WHISPER_MODEL_PATH="/opt/whisper.cpp/models/ggml-base.bin"

RUN test -x /opt/whisper.cpp/build/bin/whisper-cli \
 && test -x /opt/whisper.cpp/build/bin/whisper-cli-cuda \
 && test -x /opt/whisper.cpp/build/bin/whisper-cli-vk \
 && test -x /opt/whisper.cpp/build/bin/whisper-cli-cpu \
 || (echo "ERROR: whisper-cli selector or backend binaries missing" && exit 1)

RUN test -f /opt/whisper.cpp/models/ggml-base.bin || \
    (echo "ERROR: whisper model missing" && exit 1)

# Thin shell wrapper so api_server.py can call YUKTRA_DM_INGEST_BIN as a subprocess.
RUN printf '#!/bin/sh\nexec python3.11 -c "import launcher; launcher.main()" "$@"\n' \
    > /app/ingest.sh && chmod +x /app/ingest.sh

RUN mkdir -p \
    /app/data/docs \
    /app/data/logs \
    /app/data/models \
    /app/data/Ingested

COPY doc-management/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

VOLUME ["/app/data"]

EXPOSE 8001

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8001/api/health || exit 1

# docker-entrypoint.sh does: exec "${@:-/app/server/server_main.bin}"
# Passing python3.11 as args lets the script do its startup validation first,
# then hand off to the compiled server_main module via exec "$@".
CMD ["/usr/local/bin/docker-entrypoint.sh", \
     "python3.11", "-c", \
     "import server_main, sys; sys.exit(server_main.main())"]








######################################## This dockerfile work for nvidia-smi tested  ######################################################
# # syntax=docker/dockerfile:1
# ARG CUDA_VERSION=12.4.1
# ARG CUDA_ARCHITECTURES=75;86;89

# # ==========================================
# # Stage 1 - React Build
# # ==========================================
# FROM node:20-alpine AS frontend-builder
# WORKDIR /frontend
# COPY doc-management/frontend/react-app/package*.json ./
# RUN --mount=type=cache,target=/root/.npm \
#     npm ci
# COPY doc-management/frontend/react-app/ .
# RUN npm run build


# # ==========================================
# # Stage 2 - whisper.cpp CUDA build
# # ==========================================
# FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS whisper-cuda
# ARG CUDA_ARCHITECTURES
# ENV DEBIAN_FRONTEND=noninteractive

# RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
#     --mount=type=cache,target=/var/lib/apt,sharing=locked \
#     apt-get update && apt-get install -y --no-install-recommends \
#     git cmake build-essential ca-certificates

# WORKDIR /opt
# RUN for i in 1 2 3; do \
#         git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
#         sleep 10; \
#     done

# WORKDIR /opt/whisper.cpp
# RUN cmake -B build \
#     -DGGML_CUDA=ON -DGGML_NATIVE=OFF \
#     -DBUILD_SHARED_LIBS=OFF \
#     -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON \
#     -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"
# RUN cmake --build build --target whisper-cli -j"$(nproc)"


# # ==========================================
# # Stage 3 - whisper.cpp CPU build + model
# # ==========================================
# FROM ubuntu:22.04 AS whisper-cpu
# ENV DEBIAN_FRONTEND=noninteractive

# RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
#     --mount=type=cache,target=/var/lib/apt,sharing=locked \
#     apt-get update && apt-get install -y --no-install-recommends \
#     git cmake build-essential ca-certificates curl

# WORKDIR /opt
# RUN for i in 1 2 3; do \
#         git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
#         sleep 10; \
#     done

# WORKDIR /opt/whisper.cpp
# RUN bash ./models/download-ggml-model.sh base
# RUN cmake -B build \
#     -DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON \
#     -DBUILD_SHARED_LIBS=OFF \
#     -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON
# RUN cmake --build build --target whisper-cli -j"$(nproc)"


# # ==========================================
# # Stage 4 - llama-cpp-python CUDA wheel
# # (GPU offload for embedding / LLM models)
# # ==========================================
# FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS llama-builder
# ARG CUDA_ARCHITECTURES
# ENV DEBIAN_FRONTEND=noninteractive

# # Set up deadsnakes repo for Python 3.11
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     software-properties-common gnupg ca-certificates \
#     && add-apt-repository -y ppa:deadsnakes/ppa \
#     && rm -rf /var/lib/apt/lists/*

# RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
#     --mount=type=cache,target=/var/lib/apt,sharing=locked \
#     apt-get update && apt-get install -y --no-install-recommends \
#     build-essential cmake ninja-build \
#     python3.11 python3.11-dev python3.11-venv

# RUN python3.11 -m ensurepip \
#     && python3.11 -m pip install --upgrade pip setuptools wheel ninja

# ENV CMAKE_ARGS="-DGGML_CUDA=ON -DGGML_NATIVE=OFF -DCMAKE_CUDA_ARCHITECTURES=${CUDA_ARCHITECTURES}" \
#     FORCE_CMAKE=1

# RUN --mount=type=cache,target=/root/.cache/pip \
#     python3.11 -m pip wheel llama-cpp-python --no-binary llama-cpp-python -w /wheels


# # ==========================================
# # Stage 5 - Nuitka Build
# # (module mode: project .py → .so only)
# # ==========================================
# FROM ubuntu:22.04 AS nuitka-builder
# ENV DEBIAN_FRONTEND=noninteractive \
#     PYTHONUNBUFFERED=1 \
#     PYTHONDONTWRITEBYTECODE=1 \
#     CCACHE_DIR=/root/.ccache

# # Set up deadsnakes repo (writes to image layer)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     software-properties-common gpg-agent \
#     && add-apt-repository -y ppa:deadsnakes/ppa \
#     && rm -rf /var/lib/apt/lists/*

# RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
#     --mount=type=cache,target=/var/lib/apt,sharing=locked \
#     apt-get update && apt-get install -y --no-install-recommends \
#     python3.11 python3.11-dev python3.11-venv \
#     gcc g++ make patchelf ccache zlib1g-dev \
#     libgl1 libglib2.0-0 libgomp1

# WORKDIR /build

# RUN python3.11 -m ensurepip && python3.11 -m pip install --upgrade pip

# # Install Vulkan llama-cpp-python wheel (GPU-capable)
# COPY --from=llama-builder /wheels /tmp/wheels
# RUN python3.11 -m pip install /tmp/wheels/llama_cpp_python*.whl \
#     && rm -rf /tmp/wheels

# # Install all runtime dependencies
# COPY doc-management/backend/requirements.txt /tmp/dm-requirements.txt
# COPY doc-qna/backend/requirements.txt /tmp/dq-requirements.txt
# RUN grep -v '^llama-cpp-python' /tmp/dq-requirements.txt > /tmp/dq-no-llama.txt

# RUN --mount=type=cache,target=/root/.cache/pip \
#     python3.11 -m pip install \
#     nuitka ordered-set zstandard \
#     "gunicorn[gthread]" "uvicorn[standard]" \
#     -r /tmp/dm-requirements.txt \
#     -r /tmp/dq-no-llama.txt

# # Torch CPU-only — needed so Nuitka can locate imports at compile time
# RUN --mount=type=cache,target=/root/.cache/pip \
#     python3.11 -m pip install \
#     torch torchvision --index-url https://download.pytorch.org/whl/cpu

# # Copy all application source files
# COPY doc-management/server_main.py ./
# COPY doc-management/frontend/api_server.py ./
# COPY doc-management/frontend/export_queue_store.py ./
# COPY doc-management/backend/launcher.py ./
# COPY doc-management/backend/ingest_and_export.py ./
# COPY doc-management/backend/docling_loader.py ./
# COPY doc-management/backend/markdown_ingest.py ./
# COPY doc-management/backend/ground_id_grouping.py ./
# COPY doc-management/backend/vector_store_cleanup.py ./
# COPY doc-management/backend/hierarchy/ ./hierarchy/
# COPY doc-qna/backend/logger.py ./
# COPY doc-qna/backend/store_runtime_config.py ./
# COPY doc-qna/backend/rag_utils.py ./

# # Patch docling site-packages before compilation
# COPY doc-management/patch_docling.py /tmp/patch_docling.py
# RUN python3.11 /tmp/patch_docling.py

# # Ensure all dirs have __init__.py for Nuitka package recursion
# RUN find /build -type d -exec touch {}/__init__.py \;

# # Compile hierarchy package (ccache reuses C object files on rebuild)
# RUN --mount=type=cache,target=/root/.ccache \
#     python3.11 -m nuitka --module --include-package=hierarchy \
#     --jobs=$(nproc) --output-dir=dist/ hierarchy/

# # Compile each flat source module
# RUN --mount=type=cache,target=/root/.ccache \
#     for f in server_main api_server export_queue_store launcher ingest_and_export \
#               docling_loader markdown_ingest ground_id_grouping vector_store_cleanup \
#               logger store_runtime_config rag_utils; do \
#         python3.11 -m nuitka --module --jobs=$(nproc) --output-dir=dist/ ${f}.py || exit 1; \
#     done

# # Keep only compiled .so files
# RUN mkdir /dist_clean && \
#     find /build/dist -maxdepth 1 -name "*.so" -exec cp {} /dist_clean/ \;


# # ==========================================
# # Stage 6 - Runtime
# # ==========================================
# FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu22.04
# ENV DEBIAN_FRONTEND=noninteractive \
#     PYTHONUNBUFFERED=1 \
#     PYTHONDONTWRITEBYTECODE=1 \
#     NVIDIA_VISIBLE_DEVICES=all \
#     NVIDIA_DRIVER_CAPABILITIES=compute,utility

# # Set up deadsnakes repo (writes to image layer)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     software-properties-common gpg-agent \
#     && add-apt-repository -y ppa:deadsnakes/ppa \
#     && rm -rf /var/lib/apt/lists/*

# RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
#     --mount=type=cache,target=/var/lib/apt,sharing=locked \
#     apt-get update && apt-get install -y --no-install-recommends \
#     python3.11 \
#     ffmpeg poppler-utils tesseract-ocr curl \
#     libgl1 libglib2.0-0 libgomp1 \
#     libvulkan1 mesa-vulkan-drivers \
#     && update-ca-certificates

# RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python3 && \
#     ln -sf /usr/bin/python3.11 /usr/local/bin/python

# WORKDIR /app

# # Copy pip packages from builder — no re-download needed
# COPY --from=nuitka-builder /usr/local/lib/python3.11 /usr/local/lib/python3.11

# # Remove any cu13x torch + NVIDIA cu13 packages copied from builder,
# # then install torch+cu124 (matches driver CUDA 12.4 / version code 12040)
# RUN python3.11 -m pip uninstall -y \
#     torch torchvision torchaudio \
#     nvidia-cudnn-cu13 nvidia-nccl-cu13 nvidia-cublas-cu13 \
#     nvidia-cusolver-cu13 nvidia-cusparse-cu13 nvidia-cufft-cu13 \
#     nvidia-curand-cu13 nvidia-cuda-runtime-cu13 nvidia-cuda-nvrtc-cu13 \
#     nvidia-cuda-cupti-cu13 nvidia-nvjitlink-cu13 nvidia-nvshmem-cu13 \
#     nvidia-nvtx-cu13 nvidia-cusparselt-cu13 nvidia-cufile-cu13 \
#     cuda-toolkit triton 2>/dev/null || true
# RUN --mount=type=cache,target=/root/.cache/pip \
#     python3.11 -m pip install \
#     torch torchvision --index-url https://download.pytorch.org/whl/cu124

# # Install Vulkan llama-cpp-python wheel into runtime
# COPY --from=llama-builder /wheels /tmp/wheels
# RUN python3.11 -m pip install /tmp/wheels/llama_cpp_python*.whl \
#     && rm -rf /tmp/wheels

# # Compiled .so modules (no .py source)
# COPY --from=nuitka-builder /dist_clean/ /app/

# # React static assets
# COPY --from=frontend-builder /frontend/dist /app/static/

# # Copy whisper tree (cpu binary + model) then overlay CUDA binary
# COPY --from=whisper-cpu  /opt/whisper.cpp                           /opt/whisper.cpp
# COPY --from=whisper-cuda /opt/whisper.cpp/build/bin/whisper-cli     /opt/whisper.cpp/build/bin/whisper-cli-cuda

# # Create CUDA→CPU selector
# RUN mv /opt/whisper.cpp/build/bin/whisper-cli /opt/whisper.cpp/build/bin/whisper-cli-cpu && \
#     printf '#!/bin/sh\nif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q libcuda.so.1; then\n  exec /opt/whisper.cpp/build/bin/whisper-cli-cuda "$@"\nfi\nexec /opt/whisper.cpp/build/bin/whisper-cli-cpu "$@"\n' \
#     > /opt/whisper.cpp/build/bin/whisper-cli && \
#     chmod +x /opt/whisper.cpp/build/bin/whisper-cli \
#              /opt/whisper.cpp/build/bin/whisper-cli-cuda \
#              /opt/whisper.cpp/build/bin/whisper-cli-cpu

# # Thin wrapper so api_server.py can call YUKTRA_DM_INGEST_BIN as a subprocess
# RUN printf '#!/bin/sh\nexec python3.11 -c "import launcher; launcher.main()" "$@"\n' \
#     > /app/ingest.sh && chmod +x /app/ingest.sh

# RUN mkdir -p /app/data/docs /app/data/logs /app/data/models /app/data/Ingested

# COPY doc-management/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# ENV PYTHONPATH=/app \
#     DATA_DIR=/app/data \
#     YUKTRA_DM_DATA_DIR=/app/data \
#     YUKTRA_DM_REACT_DIR=/app/static \
#     YUKTRA_DM_INGEST_BIN=/app/ingest.sh \
#     YUKTRA_DM_API_HOST=0.0.0.0 \
#     YUKTRA_DM_API_PORT=8001 \
#     YUKTRA_DM_WORKERS="" \
#     YUKTRA_LLM_N_GPU_LAYERS=-1 \
#     YUKTRA_ALLOW_MISSING_MODELS=1 \
#     WHISPER_CPP_PATH="/opt/whisper.cpp/build/bin/whisper-cli" \
#     YUKTRA_WHISPER_CPP_BIN="/opt/whisper.cpp/build/bin/whisper-cli" \
#     YUKTRA_WHISPER_MODEL_PATH="/opt/whisper.cpp/models/ggml-base.bin"

# VOLUME ["/app/data"]

# EXPOSE 8001

# HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
#     CMD curl -fsS http://127.0.0.1:8001/api/health || exit 1

# CMD ["/usr/local/bin/docker-entrypoint.sh", \
#      "python3.11", "-c", \
#      "import server_main, sys; sys.exit(server_main.main())"]





















# ARG CUDA_VERSION=12.4.1
# ARG CUDA_ARCHITECTURES=75;86;89

# FROM node:20-bookworm-slim AS react-build

# WORKDIR /app/react-app

# COPY doc-management/frontend/react-app/package.json \
#      doc-management/frontend/react-app/package-lock.json* ./

# RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

# COPY doc-management/frontend/react-app/ ./

# RUN npm run build

# RUN test -f /app/react-app/dist/index.html || \
#     (echo "ERROR: React build failed" && exit 1)



# FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu22.04 AS whisper-build-cuda

# ARG CUDA_ARCHITECTURES

# ENV DEBIAN_FRONTEND=noninteractive

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     git \
#     cmake \
#     build-essential \
#     curl \
#     ca-certificates \
#     && rm -rf /var/lib/apt/lists/*

# WORKDIR /opt

# RUN for i in 1 2 3; do \
#         git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
#         echo "git clone attempt $i failed, retrying..."; sleep 10; \
#     done

# WORKDIR /opt/whisper.cpp

# RUN cmake -B build \
#     -DGGML_CUDA=ON \
#     -DGGML_NATIVE=OFF \
#     -DBUILD_SHARED_LIBS=OFF \
#     -DWHISPER_BUILD_TESTS=OFF \
#     -DWHISPER_BUILD_EXAMPLES=ON \
#     -DCMAKE_CUDA_ARCHITECTURES="${CUDA_ARCHITECTURES}"

# RUN cmake --build build --target whisper-cli -j"$(nproc)"

# RUN test -f /opt/whisper.cpp/build/bin/whisper-cli || \
#     (echo "ERROR: whisper-cli (CUDA) build failed" && exit 1)


# FROM ubuntu:22.04 AS whisper-build-vk

# ENV DEBIAN_FRONTEND=noninteractive

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     git wget curl ca-certificates build-essential cmake ninja-build \
#     software-properties-common gnupg \
#     && wget -qO /etc/apt/trusted.gpg.d/lunarg.asc https://packages.lunarg.com/lunarg-signing-key-pub.asc \
#     && wget -qO /etc/apt/sources.list.d/lunarg-vulkan-jammy.list https://packages.lunarg.com/vulkan/lunarg-vulkan-jammy.list \
#     && apt-get update && apt-get install -y --no-install-recommends \
#     vulkan-sdk \
#     && rm -rf /var/lib/apt/lists/*

# WORKDIR /opt

# RUN for i in 1 2 3; do \
#         git clone --depth=1 --branch v1.7.4 https://github.com/ggerganov/whisper.cpp.git && break; \
#         echo "git clone attempt $i failed, retrying..."; sleep 10; \
#     done

# WORKDIR /opt/whisper.cpp

# RUN bash ./models/download-ggml-model.sh base

# RUN cmake -B build \
#     -DGGML_VULKAN=ON \
#     -DGGML_NATIVE=OFF \
#     -DGGML_AVX=OFF \
#     -DGGML_AVX2=OFF \
#     -DGGML_AVX512=OFF \
#     -DGGML_FMA=OFF \
#     -DGGML_F16C=OFF \
#     -DBUILD_SHARED_LIBS=OFF \
#     -DWHISPER_BUILD_TESTS=OFF \
#     -DWHISPER_BUILD_EXAMPLES=ON

# RUN cmake --build build --target whisper-cli -j"$(nproc)"

# RUN test -f /opt/whisper.cpp/build/bin/whisper-cli || \
#     (echo "ERROR: whisper-cli build failed" && exit 1)


# FROM ubuntu:22.04 AS llama-build

# ENV DEBIAN_FRONTEND=noninteractive

# # Build toolchain + Vulkan SDK (provides glslc shader compiler + Vulkan headers)
# RUN apt-get update && apt-get install -y --no-install-recommends \
#     git wget curl ca-certificates build-essential cmake ninja-build \
#     software-properties-common gnupg \
#     && wget -qO /etc/apt/trusted.gpg.d/lunarg.asc https://packages.lunarg.com/lunarg-signing-key-pub.asc \
#     && wget -qO /etc/apt/sources.list.d/lunarg-vulkan-jammy.list https://packages.lunarg.com/vulkan/lunarg-vulkan-jammy.list \
#     && add-apt-repository -y ppa:deadsnakes/ppa \
#     && apt-get update && apt-get install -y --no-install-recommends \
#     vulkan-sdk \
#     python3.11 python3.11-dev python3.11-venv python3-pip \
#     && rm -rf /var/lib/apt/lists/*

# RUN ln -sf /usr/bin/python3.11 /usr/local/bin/python && \
#     ln -sf /usr/bin/python3.11 /usr/local/bin/python3
# RUN python -m pip install --upgrade pip setuptools wheel ninja

# # CUDA off, Vulkan on, every native CPU SIMD off (portable, never SIGILL).
# ENV CMAKE_ARGS="-DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DGGML_AVX=OFF -DGGML_AVX2=OFF -DGGML_AVX512=OFF -DGGML_FMA=OFF -DGGML_F16C=OFF"
# ENV FORCE_CMAKE=1
# RUN python -m pip wheel llama-cpp-python --no-binary llama-cpp-python -w /wheels


# FROM ubuntu:22.04 AS nuitka-build

# ENV DEBIAN_FRONTEND=noninteractive

# RUN apt-get update && apt-get install -y --no-install-recommends \
#     software-properties-common gpg-agent \
#     && add-apt-repository -y ppa:deadsnakes/ppa \
#     && apt-get update && apt-get install -y --no-install-recommends \
#         python3.11 python3.11-dev python3.11-venv python3-pip \
#         gcc g++ make patchelf ccache zlib1g-dev \
#         libgl1 libglib2.0-0 libgomp1 \
#     && rm -rf /var/lib/apt/lists/*

# RUN python3.11 -m pip install --upgrade pip

# # Install the Vulkan llama-cpp-python wheel built in Stage 2.5
# COPY --from=llama-build /wheels /tmp/wheels
# RUN python3.11 -m pip install --no-cache-dir /tmp/wheels/llama_cpp_python*.whl \
#     && rm -rf /tmp/wheels

# WORKDIR /src

# # Install Nuitka + all application dependencies
# COPY doc-management/backend/requirements.txt /tmp/dm.txt
# COPY doc-qna/backend/requirements.txt /tmp/dq.txt
# RUN grep -v '^llama-cpp-python' /tmp/dq.txt > /tmp/dq-no-llama.txt

# RUN python3.11 -m pip install --no-cache-dir nuitka ordered-set zstandard
# RUN python3.11 -m pip install --no-cache-dir gunicorn


# RUN python3.11 -m pip install --no-cache-dir \
#     torch torchvision \
#     --index-url https://download.pytorch.org/whl/cpu

# RUN python3.11 -m pip install --no-cache-dir -r /tmp/dm.txt
# RUN python3.11 -m pip install --no-cache-dir -r /tmp/dq-no-llama.txt

# # Copy all application source files
# COPY doc-management/frontend/api_server.py        ./
# COPY doc-management/server_main.py                ./
# COPY doc-management/backend/launcher.py           ./
# COPY doc-management/backend/ingest_and_export.py  ./
# COPY doc-management/backend/docling_loader.py     ./
# COPY doc-management/backend/markdown_ingest.py    ./
# COPY doc-management/backend/ground_id_grouping.py ./
# COPY doc-management/backend/vector_store_cleanup.py ./
# COPY doc-management/backend/hierarchy/            ./hierarchy/
# COPY doc-qna/backend/logger.py                    ./
# COPY doc-qna/backend/store_runtime_config.py      ./
# COPY doc-qna/backend/rag_utils.py                 ./


# RUN python3.11 -m nuitka \
#     --standalone \
#     --jobs=$(nproc) \
#     --include-package=fastapi \
#     --include-package=uvicorn \
#     --include-package=uvicorn.workers \
#     --include-package=starlette \
#     --include-package=pydantic \
#     --include-package=pydantic_core \
#     --include-package=gunicorn \
#     --include-package=faiss \
#     --include-package=numpy \
#     --include-module=api_server \
#     --include-module=vector_store_cleanup \
#     --include-module=rag_utils \
#     --include-module=logger \
#     --include-module=store_runtime_config \
#     --output-dir=/dist \
#     server_main.py


# COPY doc-management/patch_docling.py /tmp/patch_docling.py
# RUN python3.11 /tmp/patch_docling.py

# # ── Compile 2: Ingestion pipeline (launcher.py → ingest.bin) ──────────────
# RUN python3.11 -m nuitka \
#     --standalone \
#     --jobs=$(nproc) \
#     --no-deployment-flag=excluded-module-usage \
#     --nofollow-import-to=torch \
#     --nofollow-import-to=torchvision \
#     --nofollow-import-to=transformers \
#     --nofollow-import-to=accelerate \
#     --nofollow-import-to=huggingface_hub \
#     --nofollow-import-to=safetensors \
#     --nofollow-import-to=tokenizers \
#     --nofollow-import-to=docling_ibm_models \
#     --nofollow-import-to=easyocr \
#     --nofollow-import-to=timm \
#     --nofollow-import-to=scipy \
#     --nofollow-import-to=cv2 \
#     --nofollow-import-to=PIL \
#     --nofollow-import-to=rtree \
#     --nofollow-import-to=lxml \
#     --nofollow-import-to=onnxruntime \
#     --nofollow-import-to=docling_parse \
#     --nofollow-import-to=pypdfium2 \
#     --nofollow-import-to=llama_cpp \
#     --include-package=numpy \
#     --include-package=pypdf \
#     --include-package=tqdm \
#     --include-package=faiss \
#     --include-package=docx \
#     --include-package=jinja2 \
#     --include-package=docling \
#     --include-package=docling_core \
#     --include-package-data=docling \
#     --include-package-data=docling_core \
#     --include-module=ctypes.util \
#     --include-module=ingest_and_export \
#     --include-module=docling_loader \
#     --include-module=markdown_ingest \
#     --include-module=ground_id_grouping \
#     --include-package=hierarchy \
#     --include-module=logger \
#     --include-module=store_runtime_config \
#     --include-module=rag_utils \
#     --output-dir=/dist \
#     launcher.py

# RUN mv /dist/launcher.dist/launcher.bin /dist/launcher.dist/ingest.bin

# COPY doc-management/copy_dist_pkgs.py /tmp/copy_dist_pkgs.py
# RUN python3.11 /tmp/copy_dist_pkgs.py


# RUN cp -r /usr/local/lib/python3.11/dist-packages/pypdfium2_cfg /dist/launcher.dist/ && \
#     cp -r /usr/local/lib/python3.11/dist-packages/pypdfium2_cli /dist/launcher.dist/

# RUN cp /usr/lib/python3.11/cProfile.py /dist/launcher.dist/ && \
#     cp /usr/lib/python3.11/profile.py  /dist/launcher.dist/ && \
#     cp /usr/lib/python3.11/pstats.py   /dist/launcher.dist/ && \
#     find /usr/lib/python3.11/lib-dynload -name '_lsprof*.so' \
#          -exec cp {} /dist/launcher.dist/ \;


# RUN mkdir -p /dist/launcher.dist/_libs && \
#     for d in /dist/launcher.dist/*.libs; do \
#         [ -d "$d" ] && cp -n "$d/"* /dist/launcher.dist/_libs/ 2>/dev/null || true; \
#     done && \
#     echo "_libs merged: $(ls /dist/launcher.dist/_libs/ | wc -l) files"



# FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu22.04

# ENV DEBIAN_FRONTEND=noninteractive \
#     NVIDIA_VISIBLE_DEVICES=all \
#     NVIDIA_DRIVER_CAPABILITIES=compute,utility,graphics


# RUN apt-get update && apt-get install -y --no-install-recommends \
#     curl \
#     ffmpeg \
#     poppler-utils \
#     tesseract-ocr \
#     libgl1 \
#     libglib2.0-0 \
#     libgomp1 \
#     ca-certificates \
#     libvulkan1 \
#     mesa-vulkan-drivers \
#     && update-ca-certificates \
#     && rm -rf /var/lib/apt/lists/*

# WORKDIR /app


# COPY --from=whisper-build-vk   /opt/whisper.cpp                           /opt/whisper.cpp
# COPY --from=whisper-build-cuda /opt/whisper.cpp/build/bin/whisper-cli     /opt/whisper.cpp/build/bin/whisper-cli-cuda


# RUN mv /opt/whisper.cpp/build/bin/whisper-cli /opt/whisper.cpp/build/bin/whisper-cli-vk && \
#     printf '%s\n' \
# '#!/bin/sh' \
# '# whisper-cli backend selector (generated in Dockerfile).' \
# '# CUDA if NVIDIA passthrough is usable here; else portable Vulkan/CPU build.' \
# 'if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1 && ldconfig -p 2>/dev/null | grep -q libcuda.so.1; then' \
# '  exec /opt/whisper.cpp/build/bin/whisper-cli-cuda "$@"' \
# 'fi' \
# 'exec /opt/whisper.cpp/build/bin/whisper-cli-vk "$@"' \
#     > /opt/whisper.cpp/build/bin/whisper-cli && \
#     chmod +x /opt/whisper.cpp/build/bin/whisper-cli \
#              /opt/whisper.cpp/build/bin/whisper-cli-cuda \
#              /opt/whisper.cpp/build/bin/whisper-cli-vk

# ENV WHISPER_CPP_PATH="/opt/whisper.cpp/build/bin/whisper-cli" \
#     YUKTRA_WHISPER_CPP_BIN="/opt/whisper.cpp/build/bin/whisper-cli" \
#     YUKTRA_WHISPER_MODEL_PATH="/opt/whisper.cpp/models/ggml-base.bin"

# ENV YUKTRA_LLM_N_GPU_LAYERS=-1

# RUN test -x /opt/whisper.cpp/build/bin/whisper-cli \
#  && test -x /opt/whisper.cpp/build/bin/whisper-cli-cuda \
#  && test -x /opt/whisper.cpp/build/bin/whisper-cli-vk \
#  || (echo "ERROR: whisper-cli selector or backend binaries missing/non-executable" && exit 1)

# RUN test -f /opt/whisper.cpp/models/ggml-base.bin || \
#     (echo "ERROR: whisper model missing" && exit 1)


# RUN mkdir -p \
#     /app/data/docs \
#     /app/data/logs \
#     /app/data/models \
#     /app/data/Ingested


# COPY --from=nuitka-build /dist/server_main.dist/ /app/server/
# COPY --from=nuitka-build /dist/launcher.dist/    /app/ingest/

# RUN chmod +x /app/server/server_main.bin /app/ingest/ingest.bin

# COPY --from=react-build /app/react-app/dist /app/static/

# COPY doc-management/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
# RUN chmod +x /usr/local/bin/docker-entrypoint.sh


# VOLUME ["/app/data"]

# ENV DATA_DIR=/app/data \
#     YUKTRA_DM_DATA_DIR=/app/data \
#     YUKTRA_DM_REACT_DIR=/app/static \
#     YUKTRA_DM_INGEST_BIN=/app/ingest/ingest.bin \
#     YUKTRA_DM_API_HOST=0.0.0.0 \
#     YUKTRA_DM_API_PORT=8001 \
#     YUKTRA_DM_WORKERS="" \
#     LD_LIBRARY_PATH="/app/ingest/llama_cpp/lib:/app/ingest/_libs"

# EXPOSE 8001


# HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
#   CMD curl -fsS http://127.0.0.1:8001/api/health || exit 1


# CMD ["/usr/local/bin/docker-entrypoint.sh"]

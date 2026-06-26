# Plan: Simplified CI/CD — BuildKit Docker for doc-management, Parallel Nuitka Exe for doc-qna

## Context

Two sub-projects, two deployment strategies, one branch (`master`). No code sync needed — the branch already has the latest doc-management source. Changes are: new multi-stage Dockerfile for doc-management, split Nuitka build script for doc-qna, and a new `.gitlab-ci.yml`.

**All build scripts are tested on the target machine before being wired into the pipeline.**

---

## Target Pipeline

```
stages: build → deploy

── doc-management (Ubuntu Docker runner) ──────────────────────────
build_doc_management   changes: doc-management/**/* or .gitlab-ci.yml
  docker build (BuildKit parallel stages) → push two tags

deploy_doc_management  needs: build_doc_management, when: manual
  SSH → docker compose pull + up -d

── doc-qna (Windows runner + Alpine deploy runner) ─────────────────
build_doc_qna_backend   changes: doc-qna/backend/**  → Nuitka → cache push
build_doc_qna_frontend  changes: doc-qna/frontend/** → Nuitka → cache push
  (both run in parallel when their folder changes)

package_doc_qna   changes: doc-qna/backend/** OR doc-qna/frontend/**
  needs: [build_doc_qna_backend (optional), build_doc_qna_frontend (optional)]
  cache pull (both) → Inno Setup → install.exe → Yuktra-YEQ.zip artifact

deploy_doc_qna    needs: package_doc_qna, when: on_success
  SCP zip → doc-management server data volume → unzip
```

---

## Phase 0 — Test build scripts before pipeline implementation

### A. doc-qna Windows build script (machine: 20.204.31.55)

**Connection:** SSH into the Windows machine (Azure VMs have OpenSSH enabled).  
Username: `azureuser` | Password: provided at runtime.

**Goal:** Modify `doc-qna/build.ps1` to support independent backend-only and frontend-only compile modes, then verify each produces the correct output on the real machine before encoding into CI jobs.

**Steps:**
1. SSH to `azureuser@20.204.31.55`
2. Verify prerequisites: MSVC (`cl.exe`), Python, Nuitka, existing venv
3. Run backend-only compile: `.\doc-qna\build.ps1 -BackendOnly -NoVulkan`
   - Expected output: `doc-qna\emor\yuktra-eq-backend\yuktra-eq-backend.exe` + DLLs
   - Verify: exe runs, FastAPI starts, `/health` responds
4. Run frontend-only compile: `.\doc-qna\build.ps1 -FrontendOnly -NoVulkan`
   - Expected output: `doc-qna\emor\yuktra-eq\webview-runner.exe` + `yuktra-eq.exe`
   - Verify: launcher exe starts without error
5. Run full package: `.\build_installer.ps1 -DistDir "doc-qna\emor"`
   - Expected output: `install.exe` in `doc-qna\emor\`
   - Verify: installer launches, completes a test install, uninstall.exe present post-install

**build.ps1 modifications needed:**
- Add `-BackendOnly` switch: skips frontend/launcher Nuitka phases, runs only backend compile
- Add `-FrontendOnly` switch: skips backend Nuitka phase, runs only frontend stub + launcher compile
- No switch (default): runs all phases as today (backward compatible)
- Performance standards:
  - Use `--jobs=$env:NUITKA_JOBS` (already set to 4 in CI)
  - Preserve `.nuitka-cache` between runs for incremental rebuilds
  - Fail fast on any compile error (`$ErrorActionPreference = "Stop"`)
  - Echo timing for each phase so slow steps are visible

### B. doc-management Dockerfile (Linux Docker)

**Connection:** Local `docker build` on the dev machine or the Linux deploy server.

**Goal:** Verify the new multi-stage Dockerfile builds cleanly, both stages run in parallel, and the runtime container serves the React frontend and API correctly.

**Steps:**
1. Write new `doc-management/Dockerfile` (3 stages)
2. `DOCKER_BUILDKIT=1 docker build -f doc-management/Dockerfile -t dm-test .`
3. Confirm Stage 1 (Node) and Stage 2 (Nuitka) run in parallel in build output
4. `docker run -p 8000:8000 -v $(pwd)/data:/app/data dm-test`
5. `curl http://localhost:8000/api/health` → 200
6. Open `http://localhost:8000` → React UI loads
7. Confirm image has no Python/Node binaries: `docker run dm-test which python` → not found
8. Note any missing `--include-package` flags from Nuitka errors; fix and rebuild

---

## Phase 1 — doc-management/Dockerfile (rewrite after testing)

Single Dockerfile, three stages. `DOCKER_BUILDKIT=1` makes Stage 1 and Stage 2 run in parallel.

```
Stage 1  node:20-alpine      npm ci + npm run build → /app/dist
Stage 2  python:3.11-slim    pip install + nuitka compile launcher.py → /dist/launcher.dist/
Stage 3  debian:bookworm-slim  COPY from both stages; no Node or Python at runtime
```

**Stage 2 Nuitka command** (base flags; exact `--include-package` list finalised during Phase 0 testing):
```bash
python -m nuitka --standalone --follow-imports \
  --include-package=fastapi --include-package=uvicorn \
  --include-package=docling --include-package=pydantic \
  --include-package=faiss \
  --output-dir=/dist launcher.py
```

**Stage 3:**
- `COPY --from=1 /dist/launcher.dist/ /app/`
- `COPY --from=0 /app/dist/ /app/static/`
- System packages: `libgomp1 libstdc++6` (exact list confirmed in Phase 0)
- `EXPOSE 8000` | `HEALTHCHECK: curl -f http://127.0.0.1:8000/api/health`
- `ENTRYPOINT ["/entrypoint.sh"]`

**No CUDA** — doc-management does document ingestion/chunking only; dropping CUDA reduces image size significantly.

---

## Phase 2 — doc-qna/build.ps1 (modify after testing confirms switches work)

Add `-BackendOnly` and `-FrontendOnly` switches to the existing script. Default behaviour (no switch) remains unchanged. The CI jobs invoke the script with the appropriate switch.

---

## Phase 3 — `.gitlab-ci.yml` (rewrite after both build scripts are verified)

### Global variables
```yaml
variables:
  REGISTRY_HOST: "registry.emorphis.com"
  DOCKER_BUILDKIT: "1"
  SSH_OPTS: "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o ConnectTimeout=15"
  DATA_DIR_PATH: "yuktra-ima-deploy/data"
```

### `build_doc_management`
- Runner: `linux, docker` | Image: `docker:24` + service `docker:24-dind`
- Rule: branch + `changes: [doc-management/**/*, .gitlab-ci.yml]`
- `docker build -f doc-management/Dockerfile .` → push `:$CI_COMMIT_SHORT_SHA` + `:latest`

### `deploy_doc_management`
- `needs: [build_doc_management]` | `when: manual`
- SSH → generate compose + env → `docker compose pull` + `docker compose up -d`
- Compose: single `doc-management` service, port `8000`, image tag `$CI_COMMIT_SHORT_SHA`

### `build_doc_qna_backend`
- Runner: `Windows` | Rule: branch + `changes: [doc-qna/backend/**/*, .gitlab-ci.yml]`
- `allow_failure: true`
- Runs `.\doc-qna\build.ps1 -BackendOnly -NoVulkan`
- Cache push: `key: doc-qna-backend-$CI_COMMIT_REF_SLUG` → `doc-qna/emor/yuktra-eq-backend/`

### `build_doc_qna_frontend`
- Runner: `Windows` | Rule: branch + `changes: [doc-qna/frontend/**/*, .gitlab-ci.yml]`
- `allow_failure: true`
- Runs `.\doc-qna\build.ps1 -FrontendOnly -NoVulkan`
- Cache push: `key: doc-qna-frontend-$CI_COMMIT_REF_SLUG` → `doc-qna/emor/yuktra-eq/`

### `package_doc_qna`
- Runner: `Windows`
- Rule: branch + `changes: [doc-qna/backend/**/*, doc-qna/frontend/**/*, .gitlab-ci.yml]`
- `needs: [{job: build_doc_qna_backend, optional: true}, {job: build_doc_qna_frontend, optional: true}]`
- Cache pull: both backend + frontend keys
- Runs `.\build_installer.ps1 -DistDir "doc-qna\emor"` → `install.exe`
- Creates `Yuktra-YEQ.zip` | Artifact: 2-week retention

### `deploy_doc_qna`
- Runner: `alpine:3.20` | `needs: [package_doc_qna]` | `when: on_success`
- `apk add openssh-client sshpass`
- SCP `Yuktra-YEQ.zip` → `~/$DATA_DIR_PATH/` on server
- SSH: `rm -rf Yuktra-YEQ && unzip -o Yuktra-YEQ.zip -d Yuktra-YEQ && rm Yuktra-YEQ.zip`

### Trigger matrix

| Commit touches | Jobs that run |
|----------------|---------------|
| `doc-management/**` | `build_doc_management` → `deploy_doc_management` (manual) |
| `doc-qna/backend/**` | `build_doc_qna_backend` (cache push) + `package_doc_qna` → `deploy_doc_qna` |
| `doc-qna/frontend/**` | `build_doc_qna_frontend` (cache push) + `package_doc_qna` → `deploy_doc_qna` |
| Both `doc-qna` folders | both Nuitka builds in parallel → `package_doc_qna` → `deploy_doc_qna` |
| `.gitlab-ci.yml` | all jobs |

---

## Files to create / modify

| File | Action | Phase |
|------|--------|-------|
| `doc-management/Dockerfile` | Rewrite — 3-stage BuildKit | Phase 0 test → Phase 1 finalise |
| `doc-qna/build.ps1` | Add `-BackendOnly` / `-FrontendOnly` switches | Phase 0 test → Phase 2 finalise |
| `.gitlab-ci.yml` | Rewrite — 6-job pipeline | Phase 3 (after both scripts verified) |

`build_installer.ps1`, all source files — no changes.

---

## Execution order

1. SSH to Windows machine (20.204.31.55) — test and finalise `build.ps1` switches
2. Run `docker build` locally — test and finalise `doc-management/Dockerfile`
3. Write final `.gitlab-ci.yml` using verified commands
4. Push to `master` and observe first pipeline run

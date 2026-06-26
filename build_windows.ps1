<#
.SYNOPSIS
  SELF-CONTAINED one-file build. Carry ONLY this file to the build machine
  (with the repo: doc-qna\, venv\). Builds TWO portable Nuitka apps:

    release\
      backend\   backend.exe (compiled, self-contained)
      frontend\
        frontend.exe   thin launcher (the UI is NOT compiled)
        app\           frontend SOURCE (HIDDEN) - run from here
        python\        portable Python + libraries (replaces the non-portable venv)
      data\
      "Start Yuktra.vbs"

  Frontend is NOT compiled -- it runs from source via a bundled PORTABLE Python
  (a copy of the base Python install + the venv's site-packages), so release\
  works on any Windows box WITHOUT installing Python or shipping a (non-portable) venv.

.PARAMETER Setup       Install/upgrade Nuitka + all deps, then exit (run once).
.PARAMETER Clang       Force clang.
.PARAMETER FrontendJobs  Parallel C jobs for the frontend (default 4; lower if RAM is tight).
#>
param(
    [switch]$Setup,
    [switch]$Clang,
    [switch]$Msvc,
    [int]$FrontendJobs = 4
)

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot
if (-not $env:NUITKA_CACHE_DIR) { $env:NUITKA_CACHE_DIR = Join-Path $PSScriptRoot '.nuitka-cache' }

$python = if (Test-Path '.\venv\Scripts\python.exe') { '.\venv\Scripts\python.exe' } else { 'python' }
Write-Host "Using Python: $python"

if ($Setup) {
    Write-Host "`n=== Installing / upgrading dependencies ===`n"
    & $python -m pip install --upgrade pip setuptools wheel
    & $python -m pip install -U nuitka ordered-set zstandard
    & $python -m pip install -r doc-qna\backend\requirements.txt
    & $python -m pip install -r doc-qna\frontend\requirements.txt
    & $python -m pip install pypdfium2
    Write-Host "`nSetup complete. Now run:  .\build_windows.ps1`n"
    return
}

# GPU-capable llama-cpp-python (Vulkan: iGPU + NVIDIA on Windows, CPU fallback).
if (Test-Path '.\venv\Scripts\python.exe') {
    & (Join-Path $PSScriptRoot 'scripts\Install-LlamaCppGpu.ps1') -Python $python -Root $PSScriptRoot
}

# Stop any running app instances so locked files (e.g. faiss.dll held by a running
# backend.exe) can be replaced during assembly. Otherwise Remove-Item fails with
# "Access to the path is denied".
Write-Host "Stopping any running Yuktra processes..."
foreach ($pn in 'backend', 'frontend') {
    Get-Process -Name $pn -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
}
# also stop the source-run UI (pythonw running launcher.py / streamlit)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -match 'launcher\.py|streamlit' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 800

# Delete OLD build outputs so nothing stale is left behind (fresh build every time).
Write-Host "Cleaning old build (dist\, release\)..."
foreach ($d in 'dist', 'release') {
    $p = Join-Path $PSScriptRoot $d
    if (Test-Path $p) { Remove-Item $p -Recurse -Force -ErrorAction SilentlyContinue }
}

# --- helpers ---
function Test-Mod([string]$name) {
    & $python -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$name') else 1)" 2>$null
    return ($LASTEXITCODE -eq 0)
}
function IncPkg([string]$name, [switch]$Data) {
    if (Test-Mod $name) { $r = @("--include-package=$name"); if ($Data) { $r += "--include-package-data=$name" }; return $r }
    Write-Host "  skip (not installed): $name" -ForegroundColor DarkYellow; return @()
}
function IncMod([string]$name) {
    if (Test-Mod $name) { return @("--include-module=$name") }
    Write-Host "  skip (not installed): $name" -ForegroundColor DarkYellow; return @()
}
function Get-SitePackages { return (& $python -c "import sysconfig; print(sysconfig.get_paths()['purelib'])").Trim() }
function Find-Clang {
    if (Get-Command clang-cl.exe -ErrorAction SilentlyContinue) { return $true }
    if (Get-Command clang.exe    -ErrorAction SilentlyContinue) { return $true }
    $vs = Get-ChildItem 'C:\Program Files*\Microsoft Visual Studio\*\*\VC\Tools\Llvm\*\bin\clang-cl.exe' -ErrorAction SilentlyContinue | Select-Object -First 1
    return [bool]$vs
}
function Robo($src, $dst) { robocopy $src $dst /E /NFL /NDL /NJH /NJS /NP | Out-Null }
function HidePy([string]$dir) {
    foreach ($f in 'streamlit_app.py','streamlit_theme.py','logger.py') {
        $p = Join-Path $dir $f
        if (Test-Path $p) { & attrib +h $p 2>$null }
    }
}

$cores = if ($env:NUMBER_OF_PROCESSORS) { [int]$env:NUMBER_OF_PROCESSORS } else { 4 }
$useClang = $false
if ($Clang) { $useClang = $true } elseif (-not $Msvc -and (Find-Clang)) { $useClang = $true }
$compilerFlag = @(); if ($useClang) { $compilerFlag = @('--clang') }
Write-Host ("Compiler: " + $(if ($useClang) { 'clang' } else { 'MSVC' }))

# Heavy/unused libs that crash or bloat compilation (app imports none of these).
$excludeNames = @(
    'torch','tensorflow','jax','flax','keras','transformers','accelerate','datasets',
    'sentence_transformers','safetensors','tokenizers','huggingface_hub',
    'faster_whisper','ctranslate2','av','onnxruntime',
    'scipy','sklearn','scikit_learn','sympy','numba','llvmlite',
    'altair','vega_datasets','matplotlib','plotly','bokeh','seaborn','pydeck','graphviz',
    'IPython','ipykernel','notebook','jupyter','cv2','nltk','gensim','pytest','resource'
)
$excludeML = $excludeNames | ForEach-Object { "--nofollow-import-to=$_" }

# ===========================================================================
# 1) BACKEND (self-contained)
# ===========================================================================
Write-Host "`n########## BUILDING BACKEND ##########" -ForegroundColor Cyan
foreach ($c in 'fastapi','uvicorn','pydantic','llama_cpp','faiss','numpy') {
    if (-not (Test-Mod $c)) { throw "Backend dep '$c' missing. Run:  .\build_windows.ps1 -Setup" }
}
$sp = Get-SitePackages
$env:PYTHONPATH = 'doc-qna\backend;doc-management\backend'
& $python -c "import sys; sys.path.insert(0,'doc-qna/backend'); from api import _ensure_pdfjs; _ensure_pdfjs()"

$a = @('-m','nuitka','--standalone','--assume-yes-for-downloads','--disable-plugin=anti-bloat',
       '--no-deployment-flag=excluded-module-usage','--lto=no',"--jobs=$cores",
       '--output-dir=dist\backend','--output-filename=backend.exe') + $compilerFlag + $excludeML
$a += IncPkg uvicorn; $a += IncPkg fastapi; $a += IncPkg starlette; $a += IncPkg pydantic; $a += IncPkg anyio
$a += IncPkg llama_cpp -Data
$a += '--nofollow-import-to=faiss'
$a += IncPkg lingua -Data
$a += IncPkg docx -Data
$a += IncMod PIL; $a += IncMod numpy; $a += IncMod pypdf; $a += IncMod tqdm; $a += IncMod multipart; $a += IncMod python_multipart
foreach ($m in 'api','chat_history_db','logger','model_registry','prompts','qna_service','rag_utils','store_runtime_config','stt_service','tts_service') { $a += "--include-module=$m" }
$a += '--include-data-dir=doc-qna\backend\pdfjs=pdfjs'
$a += 'doc-qna\backend\launcher.py'
& $python @a
if ($LASTEXITCODE -ne 0) { throw "Backend build failed (exit $LASTEXITCODE)." }

$beDist = Get-ChildItem 'dist\backend' -Filter '*.dist' -Directory | Select-Object -First 1
foreach ($p in @('faiss','faiss_cpu.libs')) {
    $s = Join-Path $sp $p
    if (Test-Path $s) {
        $d = Join-Path $beDist.FullName $p
        if (Test-Path $d) { Remove-Item $d -Recurse -Force }
        Copy-Item $s $d -Recurse -Force
        Write-Host "  copied uncompiled -> $p" -ForegroundColor Green
    }
}

# ===========================================================================
# 2) FRONTEND -- NOT compiled. Build a tiny stub launcher exe; the real UI runs
#    from SOURCE using a bundled portable Python (set up in the assemble step).
# ===========================================================================
Write-Host "`n########## BUILDING FRONTEND (stub launcher) ##########" -ForegroundColor Cyan
$stub = @'
import os, sys, subprocess
def _here(): return os.path.dirname(os.path.abspath(sys.argv[0]))
def _root(s):
    d = s
    for _ in range(8):
        if os.path.isdir(os.path.join(d,"doc-qna")) or os.path.isfile(os.path.join(d,"venv","Scripts","pythonw.exe")): return d
        p = os.path.dirname(d)
        if p == d: break
        d = p
    return s
def _first(*ps):
    for p in ps:
        if p and os.path.isfile(p): return p
    return None
def main():
    here = _here(); root = _root(here)
    pyw = _first(os.path.join(here,"python","pythonw.exe"),          # bundled portable python
                 os.path.join(here,"python","python.exe"),
                 os.path.join(here,"venv","Scripts","pythonw.exe"),
                 os.path.join(root,"venv","Scripts","pythonw.exe")) or "pythonw.exe"
    entry = _first(os.path.join(here,"app","launcher.py"),
                   os.path.join(root,"doc-qna","frontend","launcher.py"))
    if not entry: return 2
    env = os.environ.copy()
    if not (env.get("DATA_DIR") or "").strip():
        for c in (os.path.join(os.path.dirname(here),"data"), os.path.join(root,"data"), os.path.join(root,"dist","data")):
            if os.path.isdir(c): env["DATA_DIR"] = c; break
    env.setdefault("YUKTRA_QNA_API_HOST","127.0.0.1")
    env.setdefault("YUKTRA_QNA_API_PORT","8009")
    env.setdefault("YUKTRA_QNA_API_BASE","http://127.0.0.1:8009")
    env.setdefault("YUKTRA_QNA_SKIP_WARMUP","1")
    return int(subprocess.Popen([pyw, entry], cwd=os.path.dirname(entry), env=env).wait())
if __name__ == "__main__":
    raise SystemExit(main())
'@
Set-Content -Path (Join-Path $PSScriptRoot 'frontend_stub.py') -Value $stub -Encoding ASCII
$f = @('-m','nuitka','--standalone','--assume-yes-for-downloads','--windows-console-mode=disable',
       "--jobs=$cores",'--output-dir=dist\frontend','--output-filename=frontend.exe') + $compilerFlag + @('frontend_stub.py')
& $python @f
if ($LASTEXITCODE -ne 0) { throw "Frontend stub build failed (exit $LASTEXITCODE)." }
$feDist = Get-ChildItem 'dist\frontend' -Filter '*.dist' -Directory | Select-Object -First 1

# ===========================================================================
# 3) ASSEMBLE release\  (both folders self-contained; NO venv copied)
# ===========================================================================
Write-Host "`n########## ASSEMBLING release\ ##########" -ForegroundColor Cyan
$rel = Join-Path $PSScriptRoot 'release'
New-Item -ItemType Directory -Force -Path $rel | Out-Null

$relBe = Join-Path $rel 'backend'
if (Test-Path $relBe) { Remove-Item $relBe -Recurse -Force }
Copy-Item $beDist.FullName $relBe -Recurse
Write-Host "backend\  ready" -ForegroundColor Green

$relFe = Join-Path $rel 'frontend'
if (Test-Path $relFe) { Remove-Item $relFe -Recurse -Force }
Copy-Item $feDist.FullName $relFe -Recurse        # frontend.exe (stub) + dlls

# frontend SOURCE (run from here, NOT compiled) -> release\frontend\app\ (hidden)
$app = Join-Path $relFe 'app'
New-Item -ItemType Directory -Force -Path $app | Out-Null
Copy-Item 'doc-qna\frontend\*.py'     $app -Force
Copy-Item 'doc-qna\backend\logger.py' $app -Force
& attrib +h $app 2>$null
Write-Host "frontend\app\  ready (source, hidden)" -ForegroundColor Green

# PORTABLE Python -> release\frontend\python\  (base install + venv site-packages).
# This replaces the non-portable venv so the frontend source runs on ANY machine.
$basePrefix = (& $python -c "import sys; print(sys.base_prefix)").Trim()
$relPy = Join-Path $relFe 'python'
Write-Host "Bundling portable Python from: $basePrefix  (large, please wait)..."
Robo $basePrefix $relPy
$venvSp = Join-Path $PSScriptRoot 'venv\Lib\site-packages'
if (Test-Path $venvSp) { Robo $venvSp (Join-Path $relPy 'Lib\site-packages') }
Write-Host "frontend\python\  ready (portable - no venv/Python needed on target)" -ForegroundColor Green

if (Test-Path 'data') { Write-Host "Copying data -> release\data ..."; Robo 'data' (Join-Path $rel 'data') }
else { New-Item -ItemType Directory -Force -Path (Join-Path $rel 'data') | Out-Null }
Write-Host "data\  ready" -ForegroundColor Green

$vbs = @'
Option Explicit
Dim fso, sh, base, backend, frontend
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.Environment("PROCESS")("DATA_DIR")               = base & "\data"
sh.Environment("PROCESS")("YUKTRA_QNA_API_HOST")    = "127.0.0.1"
sh.Environment("PROCESS")("YUKTRA_QNA_API_PORT")    = "8009"
sh.Environment("PROCESS")("YUKTRA_QNA_API_BASE")    = "http://127.0.0.1:8009"
sh.Environment("PROCESS")("YUKTRA_QNA_SKIP_WARMUP") = "1"
sh.Environment("PROCESS")("YUKTRA_WHISPER_DEVICE") = "auto"
' TTS (Speak): run piper via the bundled portable Python (has piper-tts) -- no
' standalone piper.exe needed. Falls back to YUKTRA_PIPER_BIN if you ship one.
sh.Environment("PROCESS")("YUKTRA_PIPER_PYTHON")     = base & "\frontend\python\python.exe"
sh.Environment("PROCESS")("YUKTRA_PIPER_BIN")        = base & "\data\models\piper\piper.exe"
sh.Environment("PROCESS")("YUKTRA_PIPER_MODEL_PATH") = base & "\data\models\piper\en_IN-medium.onnx"
' STT (mic): run faster-whisper via the bundled portable Python (compiled backend
' can't import it). Model lives in data\models\faster-whisper-tiny.
sh.Environment("PROCESS")("YUKTRA_STT_PYTHON")       = base & "\frontend\python\python.exe"
backend  = base & "\backend\backend.exe"
frontend = base & "\frontend\frontend.exe"
If fso.FileExists(backend)  Then sh.Run """" & backend  & """", 0, False
If fso.FileExists(frontend) Then sh.Run """" & frontend & """", 0, False
'@
Set-Content -Path (Join-Path $rel 'Start Yuktra.vbs') -Value $vbs -Encoding ASCII

Write-Host "`n=== ALL DONE ===" -ForegroundColor Green
Write-Host "PORTABLE app (no Python/venv needed on target): $rel"
Write-Host "  -> copy release\ anywhere, double-click  release\Start Yuktra.vbs"

# robocopy returns non-zero exit codes (1-7) on SUCCESS, which makes the shell think
# the build failed. We reached the end without throwing, so report clean success.
exit 0

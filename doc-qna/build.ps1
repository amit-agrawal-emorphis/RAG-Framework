<#
================================================================================
  build.ps1  —  COMPILE the Yuktra-YEQ app with NUITKA, then arrange the output
  into the shipped folder structure (consumed by ..\build_installer.ps1).

  Steps:
    1. (-Setup) install Nuitka + Python deps
    2. build backend   (Nuitka, MSVC)   -> backend.exe + DLLs
    3. build frontend  (Nuitka stub)     -> webview-runner.exe + DLLs
    4. build launcher  (Nuitka)          -> yuktra-eq.exe (splash + icon)
    5. ARRANGE into <OutDir> (default doc-qna\emor):
         <OutDir>\
           yuktra-eq\            yuktra-eq.exe + webview-runner.exe + app\ + python\ + DLLs
           yuktra-eq-backend\    yuktra-eq-backend.exe + DLLs (+ faiss)
           data\
           .env
           MicrosoftEdgeWebView2RuntimeInstallerX64.exe
           yuktra.png

  After this, ..\build_installer.ps1 -DistDir <OutDir> packs it into install.exe.

  Run:  .\doc-qna\build.ps1 -Setup     (first time)
        .\doc-qna\build.ps1            (deps already installed)
================================================================================
#>
param(
    [switch]$Setup,                       # install Nuitka + deps, then build
    [switch]$Clang,                       # force clang instead of MSVC
    [switch]$Vulkan,                      # force-build llama-cpp-python WITH the Vulkan GPU backend
    [switch]$NoVulkan,                    # force CPU-only even if the Vulkan SDK is detected
    [string]$OutDir = "doc-qna\emor"      # final folder (compiled app, arranged)
)

$ErrorActionPreference = 'Stop'
# This script lives in doc-qna\ ; repo root is its parent.
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root
if (-not [System.IO.Path]::IsPathRooted($OutDir)) { $OutDir = Join-Path $root ($OutDir -replace '/', '\') }
if (-not $env:NUITKA_CACHE_DIR) { $env:NUITKA_CACHE_DIR = Join-Path $root '.nuitka-cache' }
$py = if (Test-Path '.\venv\Scripts\python.exe') { '.\venv\Scripts\python.exe' } else { 'python' }

function Robo($s, $d) { robocopy $s $d /E /NFL /NDL /NJH /NJS /NP | Out-Null }
function Test-Mod([string]$n) { & $py -c "import importlib.util,sys; sys.exit(0 if importlib.util.find_spec('$n') else 1)" 2>$null; return ($LASTEXITCODE -eq 0) }
function IncPkg([string]$n, [switch]$Data) { if (Test-Mod $n) { $r = @("--include-package=$n"); if ($Data) { $r += "--include-package-data=$n" }; return $r } else { Write-Host "  skip(not installed): $n" -ForegroundColor DarkYellow; return @() } }
function IncMod([string]$n) { if (Test-Mod $n) { return @("--include-module=$n") } else { Write-Host "  skip(not installed): $n" -ForegroundColor DarkYellow; return @() } }
function Get-SP { return (& $py -c "import sysconfig;print(sysconfig.get_paths()['purelib'])").Trim() }
function Nuitka($argList) { $ErrorActionPreference = 'Continue'; & $py @argList; $rc = $LASTEXITCODE; $ErrorActionPreference = 'Stop'; if ($rc -ne 0) { throw "Nuitka failed ($rc)" } }
$cc = if ($Clang) { @('--clang') } else { @() }

function Import-MsvcEnv {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        foreach ($p in (& $vswhere -all -prerelease -latest -products * -property installationPath 2>$null)) {
            $vc = Join-Path $p 'VC\Auxiliary\Build\vcvars64.bat'
            if (Test-Path $vc) { return $vc }
        }
    }
    $vc = Get-ChildItem 'C:\Program Files*\Microsoft Visual Studio\*\*\VC\Auxiliary\Build\vcvars64.bat' -EA SilentlyContinue | Select-Object -First 1
    if ($vc) { return $vc.FullName }
    return $null
}

# ============================================================================
if ($Setup) {
    Write-Host "`n########## SETUP: install Nuitka + dependencies ##########" -ForegroundColor Cyan
    & $py -m pip install --upgrade pip setuptools wheel
    & $py -m pip install -U nuitka ordered-set zstandard
    & $py -m pip install -r doc-qna\backend\requirements.txt
    & $py -m pip install -r doc-qna\frontend\requirements.txt
    & $py -m pip install pypdfium2
    Write-Host "Setup done; continuing with build..." -ForegroundColor Green
}

# ============================================================================
# llama-cpp-python: Vulkan build for Windows (iGPU + NVIDIA via Vulkan, CPU fallback).
# Shared with setup_llama_gpu_windows.ps1 and build_windows.ps1.
if (Test-Path '.\venv\Scripts\python.exe') {
    $llamaGpuParams = @{ Python = $py; Root = $root }
    if ($Clang) { $llamaGpuParams.Clang = $true }
    if ($Vulkan) { $llamaGpuParams.Vulkan = $true }
    if ($NoVulkan) { $llamaGpuParams.NoVulkan = $true }
    & (Join-Path $root 'scripts\Install-LlamaCppGpu.ps1') @llamaGpuParams
}

# ============================================================================
Write-Host "`n########## 0) STOP RUNNING PROCESSES ##########" -ForegroundColor Cyan
foreach ($n in 'backend','frontend','yuktra-eq','webview-runner','yuktra-eq-backend','pythonw') { Get-Process -Name $n -EA SilentlyContinue | Stop-Process -Force -EA SilentlyContinue }
Start-Sleep -Milliseconds 500

# ============================================================================
Write-Host "`n########## 0.5) CLEAN OLD OUTPUTS ##########" -ForegroundColor Cyan
foreach ($p in (Join-Path $root 'dist'), $OutDir) { if (Test-Path $p) { Remove-Item $p -Recurse -Force -EA SilentlyContinue } }

# ============================================================================
$excludeNames = @('torch','tensorflow','jax','flax','keras','transformers','accelerate','datasets','sentence_transformers','safetensors','tokenizers','huggingface_hub','faster_whisper','ctranslate2','av','onnxruntime','scipy','sklearn','scikit_learn','sympy','numba','llvmlite','altair','vega_datasets','matplotlib','plotly','bokeh','seaborn','pydeck','graphviz','IPython','ipykernel','notebook','jupyter','cv2','nltk','gensim','pytest','resource','piper_tts','piper_phonemize')
$excludeML = $excludeNames | ForEach-Object { "--nofollow-import-to=$_" }
$jobs = if ($env:NUITKA_JOBS) { [int]$env:NUITKA_JOBS } else { 2 }
$common = @('--standalone','--assume-yes-for-downloads','--disable-plugin=anti-bloat','--no-deployment-flag=excluded-module-usage','--lto=no',"--jobs=$jobs") + $cc

# Load MSVC env (Nuitka needs cl.exe) unless using clang.
if (-not $Clang) {
    if ($env:VSCMD_VER) {
        Write-Host "MSVC env already loaded (VSCMD_VER=$($env:VSCMD_VER))." -ForegroundColor DarkGray
    } else {
        $vcvars = Import-MsvcEnv
        if ($vcvars) {
            cmd /c "`"$vcvars`" >nul 2>&1 && set" | ForEach-Object { if ($_ -match '^([^=]+)=(.*)$') { Set-Item -Path ("env:" + $matches[1]) -Value $matches[2] -EA SilentlyContinue } }
            if (-not (Get-Command cl.exe -EA SilentlyContinue)) { Write-Host "WARN: cl.exe not on PATH after vcvars." -ForegroundColor Yellow }
        } else { throw "MSVC (VS Build Tools) not found. Install 'Desktop development with C++' or run with -Clang." }
    }
}

# ============================================================================
Write-Host "`n########## 2) BACKEND (Nuitka) ##########" -ForegroundColor Cyan
foreach ($c in 'fastapi','uvicorn','pydantic','llama_cpp','faiss','numpy') { if (-not (Test-Mod $c)) { throw "Backend dep '$c' missing. Run: .\doc-qna\build.ps1 -Setup" } }
$sp = Get-SP
$env:PYTHONPATH = 'doc-qna\backend;doc-management\backend'
& $py -c "import sys; sys.path.insert(0,'doc-qna/backend'); from api import _ensure_pdfjs; _ensure_pdfjs()"
$a = @('-m','nuitka','--output-dir=dist\backend','--output-filename=yuktra-eq-backend.exe') + $common + $excludeML
$a += IncPkg uvicorn; $a += IncPkg fastapi; $a += IncPkg starlette; $a += IncPkg pydantic; $a += IncPkg anyio
$a += IncPkg llama_cpp -Data; $a += '--nofollow-import-to=faiss'; $a += IncPkg lingua -Data; $a += IncPkg docx -Data
$a += IncMod PIL; $a += IncMod numpy; $a += IncMod pypdf; $a += IncMod tqdm; $a += IncMod multipart; $a += IncMod python_multipart
foreach ($m in 'api','chat_history_db','logger','model_registry','prompts','qna_service','rag_utils','store_runtime_config','stt_service','tts_service') { $a += "--include-module=$m" }
$a += '--include-data-dir=doc-qna\backend\pdfjs=pdfjs','doc-qna\backend\launcher.py'
Nuitka $a
$beDist = Get-ChildItem 'dist\backend' -Filter '*.dist' -Directory | Select-Object -First 1
foreach ($p in 'faiss','faiss_cpu.libs') { $s = Join-Path $sp $p; if (Test-Path $s) { $d = Join-Path $beDist.FullName $p; if (Test-Path $d) { Remove-Item $d -Recurse -Force }; Copy-Item $s $d -Recurse -Force } }

# Copy runtime packages excluded from Nuitka compilation:
#   faster_whisper + ctranslate2 — STT backend (CPU/GPU)
#   piper_tts + piper_phonemize  — TTS backend (espeak-ng phonemizer)
Write-Host "  copying excluded runtime packages (faster_whisper, ctranslate2, piper_tts, onnxruntime)..." -ForegroundColor DarkGray
foreach ($pkg in @('faster_whisper','ctranslate2','av','piper_tts','piper_phonemize','onnxruntime')) {
    $src = Join-Path $sp $pkg
    if (Test-Path $src) {
        $dst = Join-Path $beDist.FullName $pkg
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse -Force
        Write-Host "    $pkg -> $(Split-Path $beDist.FullName -Leaf)" -ForegroundColor DarkGray
    }
}
# ctranslate2 GPU companion libs (DirectML, CUDA, MKL DLLs)
foreach ($d in Get-ChildItem $sp -Filter 'ctranslate2*' -Directory -ErrorAction SilentlyContinue) {
    if ($d.Name -ne 'ctranslate2') {
        $dst = Join-Path $beDist.FullName $d.Name
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $d.FullName $dst -Recurse -Force
        Write-Host "    $($d.Name) -> $(Split-Path $beDist.FullName -Leaf)" -ForegroundColor DarkGray
    }
}
# espeak-ng data files (piper requires these at runtime for phonemization)
$espeakDst = Join-Path $beDist.FullName 'espeak-ng-data'
foreach ($base in @('piper_tts','piper_phonemize')) {
    foreach ($sub in @("$base\espeak-ng-data","$base\lib\espeak-ng-data")) {
        $src = Join-Path $sp $sub
        if (Test-Path $src) {
            if (Test-Path $espeakDst) { Remove-Item $espeakDst -Recurse -Force }
            Copy-Item $src $espeakDst -Recurse -Force
            Write-Host "    espeak-ng-data -> $(Split-Path $beDist.FullName -Leaf)" -ForegroundColor DarkGray
            break
        }
    }
    if (Test-Path $espeakDst) { break }
}

# ============================================================================
Write-Host "`n########## 3) FRONTEND (Nuitka stub -> webview-runner.exe) ##########" -ForegroundColor Cyan
$f = @('-m','nuitka','--windows-console-mode=disable','--windows-icon-from-ico=installer\icons\yuktra.ico','--output-dir=dist\frontend','--output-filename=webview-runner.exe') + $common + @('frontend_stub.py')
Nuitka $f
$feDist = Get-ChildItem 'dist\frontend' -Filter '*.dist' -Directory | Select-Object -First 1

# ============================================================================
Write-Host "`n########## 4) LAUNCHER (Nuitka -> yuktra-eq.exe) ##########" -ForegroundColor Cyan
$l = @('-m','nuitka','--standalone','--assume-yes-for-downloads','--windows-console-mode=disable','--enable-plugin=tk-inter','--jobs=2') + $cc + @("--windows-icon-from-ico=installer\icons\yuktra.ico","--include-data-files=installer\icons\yuktra.ico=yuktra.ico",'--output-dir=dist\launcher','--output-filename=yuktra-eq.exe','installer\launcher_app.py')
Nuitka $l
$launcherDist = Get-ChildItem 'dist\launcher' -Filter '*.dist' -Directory | Select-Object -First 1

# ============================================================================
Write-Host "`n########## 5) ARRANGE -> $OutDir (yuktra-eq\, yuktra-eq-backend\, data\, ...) ##########" -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null

# yuktra-eq-backend\  =  backend Nuitka dist (yuktra-eq-backend.exe + DLLs + faiss)
$beOut = Join-Path $OutDir 'yuktra-eq-backend'
Robo $beDist.FullName $beOut

# yuktra-eq\  =  launcher dist (yuktra-eq.exe + DLLs) + frontend (webview-runner + app + portable python)
$uiOut = Join-Path $OutDir 'yuktra-eq'
Robo $launcherDist.FullName $uiOut                       # yuktra-eq.exe + DLLs
Robo $feDist.FullName $uiOut                             # webview-runner.exe + DLLs
$app = Join-Path $uiOut 'app'; New-Item -ItemType Directory -Force -Path $app | Out-Null
Copy-Item 'doc-qna\frontend\*.py' $app -Force
Copy-Item 'doc-qna\backend\logger.py' $app -Force
$basePrefix = (& $py -c "import sys;print(sys.base_prefix)").Trim()
Robo $basePrefix (Join-Path $uiOut 'python')             # portable python (frontend runs from source)
$venvSp = Join-Path $root 'venv\Lib\site-packages'
if (Test-Path $venvSp) {
    # Verify critical frontend packages exist before bundling — missing packages
    # cause silent frontend startup failure (streamlit child crashes, no window opens).
    foreach ($pkg in @('streamlit', 'webview')) {
        if (-not (Test-Path (Join-Path $venvSp $pkg))) {
            throw "FATAL: '$pkg' not found in $venvSp. Run: .\doc-qna\build.ps1 -Setup   (installs frontend requirements including streamlit and pywebview)"
        }
    }
    Robo $venvSp (Join-Path $uiOut 'python\Lib\site-packages')
} else {
    throw "FATAL: venv\Lib\site-packages not found at $venvSp. The frontend requires packages from the venv. Run: .\doc-qna\build.ps1 -Setup   to create and populate the venv."
}

# data\  +  .env  +  WebView2 runtime  +  yuktra.png
if (Test-Path 'data') { Robo 'data' (Join-Path $OutDir 'data') } else { New-Item -ItemType Directory -Force -Path (Join-Path $OutDir 'data') | Out-Null }
if (Test-Path '.env') { Copy-Item '.env' (Join-Path $OutDir '.env') -Force }
$wv = 'pre-requisite-icon\MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
if (Test-Path $wv) { Copy-Item $wv (Join-Path $OutDir 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe') -Force }
$png = @('pre-requisite-icon\Group 10400.png','pre-requisite-icon\yuktra.png') | Where-Object { Test-Path $_ } | Select-Object -First 1
if ($png) { Copy-Item $png (Join-Path $OutDir 'yuktra.png') -Force }

# sanity
foreach ($must in (Join-Path $uiOut 'yuktra-eq.exe'), (Join-Path $beOut 'yuktra-eq-backend.exe')) {
    if (-not (Test-Path $must)) { throw "Expected output missing: $must" }
}

Write-Host "`n=== COMPILE DONE ===" -ForegroundColor Green
Write-Host "App folder ready: $OutDir"
Write-Host "  yuktra-eq\yuktra-eq.exe , yuktra-eq-backend\yuktra-eq-backend.exe , data\ , .env , WebView2 , yuktra.png"
Write-Host "Next: .\build_installer.ps1 -DistDir `"$OutDir`"   (packs -> install.exe)"

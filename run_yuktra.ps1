<#
  Yuktra application launcher (Windows, .exe build).

  Folder layout this script expects (its own folder = release root):
      <root>\backend\backend.exe     (+ bundled DLLs)
      <root>\frontend\frontend.exe   (+ bundled DLLs)
      <root>\data\                    (DATA_DIR - models, docs, logs, chat db)
      <root>\loader.ps1
      <root>\run_yuktra.ps1   (this file)

  What it does:
      1. Sets DATA_DIR + service env vars.
      2. Shows the loader splash (loader.ps1, separate hidden process).
      3. Starts backend.exe hidden, then frontend.exe hidden.
      4. The loader auto-closes once the app window appears.
      5. Cleans up both processes when the app stops.
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot

$backendExe  = Join-Path $root 'backend\backend.exe'
$frontendExe = Join-Path $root 'frontend\frontend.exe'
$loader      = Join-Path $root 'loader.ps1'

foreach ($p in @($backendExe, $frontendExe, $loader)) {
    if (-not (Test-Path $p)) { throw "Missing required file: $p" }
}

# --- Data folder (created if missing) ---
$dataDir = Join-Path $root 'data'
foreach ($d in @($dataDir, (Join-Path $dataDir 'docs'), (Join-Path $dataDir 'logs'), (Join-Path $dataDir 'models'))) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Force -Path $d | Out-Null }
}
$env:DATA_DIR = $dataDir

# --- API service config (fixed port so the loader can poll health) ---
$apiPort = '8009'
$env:YUKTRA_QNA_API_HOST   = '127.0.0.1'
$env:YUKTRA_QNA_API_PORT   = $apiPort
$env:YUKTRA_QNA_API_BASE   = "http://127.0.0.1:$apiPort"
$env:YUKTRA_QNA_SKIP_WARMUP = '1'
# STT: CTranslate2 picks CUDA on NVIDIA, else CPU
$env:YUKTRA_WHISPER_DEVICE = if ($env:YUKTRA_WHISPER_DEVICE) { $env:YUKTRA_WHISPER_DEVICE } else { 'auto' }

# Frontend re-launches ITSELF for the Streamlit child -> tell it it's a binary.
$env:YUKTRA_IPC_LAUNCH_MODE = 'bin'
$env:YUKTRA_IPC_BIN_NAME    = $frontendExe

# --- Optional offline STT/TTS, only if the binaries/models are present in data\ ---
$whisperCli   = Join-Path $dataDir 'models\whisper.cpp\build\bin\whisper-cli.exe'
$whisperModel = Join-Path $dataDir 'models\ggml-base.bin'
if ((Test-Path $whisperCli) -and (Test-Path $whisperModel)) {
    $env:YUKTRA_WHISPER_CPP_BIN   = $whisperCli
    $env:YUKTRA_WHISPER_MODEL_PATH = $whisperModel
}
$piperBin   = Join-Path $dataDir 'models\piper\piper.exe'
$piperModel = Join-Path $dataDir 'models\piper\en_US-lessac-medium.onnx'
if ((Test-Path $piperBin) -and (Test-Path $piperModel)) {
    $env:YUKTRA_PIPER_BIN        = $piperBin
    $env:YUKTRA_PIPER_MODEL_PATH = $piperModel
}

# --- 1) Loader splash (separate process so it animates while the app boots) ---
$loaderProc = Start-Process powershell -PassThru -WindowStyle Hidden -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $loader,
    '-ApiBase', $env:YUKTRA_QNA_API_BASE
)

# --- 2) Backend (hidden) ---
$backendProc = Start-Process $backendExe -PassThru -WindowStyle Hidden -WorkingDirectory (Split-Path $backendExe)

# --- 3) Frontend (hidden console; it opens the browser/window itself) ---
$frontendProc = Start-Process $frontendExe -PassThru -WindowStyle Hidden -WorkingDirectory (Split-Path $frontendExe)

# --- 4) Run until the frontend stops, then tear everything down ---
try {
    Wait-Process -Id $frontendProc.Id
}
finally {
    foreach ($p in @($loaderProc, $frontendProc, $backendProc)) {
        if ($p -and -not $p.HasExited) {
            try { Stop-Process -Id $p.Id -Force -ErrorAction SilentlyContinue } catch {}
        }
    }
}

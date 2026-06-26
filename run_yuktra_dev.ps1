<#
.SYNOPSIS
  Windows dev: install GPU-capable llama-cpp-python, then run the QnA API.

  GPU (iGPU or NVIDIA via Vulkan) is used automatically when present; otherwise CPU.

  First time:
    .\venv\Scripts\activate
    pip install -r requirements.txt
    .\setup_llama_gpu_windows.ps1

  Then:
    .\run_yuktra_dev.ps1
#>
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location $root

$py = if (Test-Path '.\venv\Scripts\python.exe') { '.\venv\Scripts\python.exe' } else { 'python' }

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'setup_llama_gpu_windows.ps1') -Python $py

$env:PYTHONPATH = "doc-qna\backend;doc-management\backend"
if (Test-Path '.env') {
    Get-Content '.env' | ForEach-Object {
        if ($_ -match '^\s*#' -or $_ -notmatch '=') { return }
        $k, $v = $_ -split '=', 2
        $k = $k.Trim(); $v = $v.Trim().Trim('"').Trim("'")
        if ($k -and -not [string]::IsNullOrWhiteSpace($k)) {
            if (-not (Get-Item -Path "Env:$k" -ErrorAction SilentlyContinue)) {
                Set-Item -Path "Env:$k" -Value $v
            }
        }
    }
}

# STT: let CTranslate2 pick CUDA on NVIDIA, else CPU
if (-not $env:YUKTRA_WHISPER_DEVICE) { $env:YUKTRA_WHISPER_DEVICE = 'auto' }

Write-Host "Starting QnA API (GPU auto-detect + CPU fallback)..." -ForegroundColor Cyan
& $py 'doc-qna\backend\launcher.py'

<#
.SYNOPSIS
  Build llama-cpp-python with the Vulkan backend for Windows.

  Covers Intel/AMD integrated GPUs and NVIDIA discrete GPUs (via Vulkan).
  At runtime the app calls llama_supports_gpu_offload() and falls back to CPU
  when no GPU is present (see qna_service._resolve_n_gpu_layers).

.PARAMETER Python
  Python executable (default: venv\Scripts\python.exe or python).

.PARAMETER Root
  Repository root (default: parent of scripts\).

.PARAMETER Vulkan
  Force Vulkan build (fail if Vulkan SDK missing).

.PARAMETER NoVulkan
  Force CPU-only AVX2 wheel (skip GPU).

.PARAMETER Clang
  Passed through for MSVC env import compatibility.
#>
param(
    [string]$Python = '',
    [string]$Root = '',
    [switch]$Vulkan,
    [switch]$NoVulkan,
    [switch]$Clang
)

$ErrorActionPreference = 'Stop'

function Find-VulkanSdk {
    if ($env:VULKAN_SDK -and (Test-Path $env:VULKAN_SDK)) {
        $glslc = Join-Path $env:VULKAN_SDK 'Bin\glslc.exe'
        if (Test-Path $glslc) { return $env:VULKAN_SDK }
    }
    $roots = @('C:\VulkanSDK', "${env:ProgramFiles}\VulkanSDK")
    foreach ($root in $roots) {
        if (-not (Test-Path $root)) { continue }
        $latest = Get-ChildItem $root -Directory -ErrorAction SilentlyContinue |
            Where-Object { Test-Path (Join-Path $_.FullName 'Bin\glslc.exe') } |
            Sort-Object { [version]($_.Name -replace '[^\d\.]', '') } -Descending |
            Select-Object -First 1
        if ($latest) { return $latest.FullName }
    }
    return $null
}

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

if (-not $Root) {
    $Root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}
if (-not $Python) {
    $Python = if (Test-Path (Join-Path $Root 'venv\Scripts\python.exe')) {
        Join-Path $Root 'venv\Scripts\python.exe'
    } else {
        'python'
    }
}

$useVulkan = $false
if ($NoVulkan) {
    $useVulkan = $false
} else {
    $sdk = Find-VulkanSdk
    if ($sdk) {
        $env:VULKAN_SDK = $sdk
        $useVulkan = $true
    } elseif ($Vulkan) {
        $useVulkan = $true
    } else {
        throw @"
Vulkan SDK not found. Windows releases need the Vulkan backend so iGPU and NVIDIA GPUs
are used automatically at runtime (CPU fallback when no GPU).

  1. Install: https://vulkan.lunarg.com/sdk/home#windows
  2. Re-open the terminal (VULKAN_SDK is usually C:\VulkanSDK\<version>)
  3. Re-run this script

For a local CPU-only wheel pass -NoVulkan.
"@
    }
}

$wantMode = if ($useVulkan) { 'vulkan' } else { 'cpu-avx2' }
$marker = Join-Path $Root 'venv\.llama_build'
$haveMode = if (Test-Path $marker) { (Get-Content $marker -Raw -EA SilentlyContinue).Trim() } else { '' }

$forceRebuild = $false
$libDir = (& $Python -c "import llama_cpp, os; print(os.path.join(os.path.dirname(llama_cpp.__file__), 'lib'))" 2>$null).Trim()
if ($libDir) {
    $hasVulkanDll = Test-Path (Join-Path $libDir 'ggml-vulkan.dll')
    $actual = if ($hasVulkanDll) { 'vulkan' } else { 'cpu-avx2' }
    if ($haveMode -and $actual -ne $haveMode) {
        Write-Host "Stale llama-cpp marker: expected '$haveMode', wheel is '$actual'. Rebuilding." -ForegroundColor Yellow
        Remove-Item $marker -Force -EA SilentlyContinue
        $haveMode = ''
        $forceRebuild = $true
    }
}

$venvPy = Join-Path $Root 'venv\Scripts\python.exe'
$shouldRebuild = $forceRebuild -or ($haveMode -ne $wantMode)
if (-not $shouldRebuild) {
    Write-Host "llama-cpp-python already installed (mode: $wantMode)." -ForegroundColor DarkGray
    return
}

if ($useVulkan -and -not (Find-VulkanSdk)) {
    throw @"
Vulkan SDK not found. Required for GPU-enabled Windows builds (iGPU + NVIDIA via Vulkan).

  1. Download and install: https://vulkan.lunarg.com/sdk/home#windows
  2. Re-open your terminal (or set VULKAN_SDK to C:\VulkanSDK\<version>)
  3. Re-run this script

For a CPU-only wheel pass -NoVulkan.
"@
}

Write-Host "`n########## Rebuilding llama-cpp-python (mode: $wantMode) ##########" -ForegroundColor Cyan
if ($useVulkan) {
    Write-Host "Vulkan SDK -> $env:VULKAN_SDK" -ForegroundColor Green
} else {
    Write-Host "CPU-only AVX2 build (no GPU offload at runtime)." -ForegroundColor Yellow
}

if (-not $Clang -and -not $env:VSCMD_VER) {
    $vc = Import-MsvcEnv
    if ($vc) {
        cmd /c "`"$vc`" >nul 2>&1 && set" | ForEach-Object {
            if ($_ -match '^([^=]+)=(.*)$') {
                Set-Item -Path ("env:" + $matches[1]) -Value $matches[2] -EA SilentlyContinue
            }
        }
    }
}

if ($useVulkan) {
    $vkBin     = Join-Path $env:VULKAN_SDK 'Bin'
    $vkInclude = Join-Path $env:VULKAN_SDK 'Include'
    $vkLib     = Join-Path $env:VULKAN_SDK 'Lib'
    $glslc     = Join-Path $vkBin 'glslc.exe'
    if (-not (Test-Path $glslc))     { throw "glslc.exe missing at $glslc" }
    if (-not (Test-Path $vkInclude)) { throw "Vulkan Include dir missing at $vkInclude" }
    if (-not (Test-Path $vkLib))     { throw "Vulkan Lib dir missing at $vkLib" }
    if ($env:PATH -notlike "*$vkBin*") { $env:PATH = "$vkBin;$env:PATH" }
    $env:CMAKE_PREFIX_PATH = if ($env:CMAKE_PREFIX_PATH) { "$env:VULKAN_SDK;$env:CMAKE_PREFIX_PATH" } else { $env:VULKAN_SDK }
    if (-not $env:VK_SDK_PATH) { $env:VK_SDK_PATH = $env:VULKAN_SDK }
    $env:LIB     = if ($env:LIB)     { "$vkLib;$env:LIB"         } else { $vkLib }
    $env:INCLUDE = if ($env:INCLUDE) { "$vkInclude;$env:INCLUDE" } else { $vkInclude }
}

& $Python -m pip install --upgrade cmake ninja
$cmakeArgs = "-DGGML_NATIVE=OFF -DGGML_AVX=ON -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_F16C=ON -DGGML_AVX512=OFF"
if ($useVulkan) {
    $vulkanLib = Join-Path $vkLib 'vulkan-1.lib'
    $cmakeArgs = "$cmakeArgs -DGGML_VULKAN=ON ""-DVulkan_INCLUDE_DIR=$vkInclude"" ""-DVulkan_LIBRARY=$vulkanLib"" ""-DVulkan_GLSLC_EXECUTABLE=$glslc"""
}
$env:CMAKE_ARGS = $cmakeArgs
$env:FORCE_CMAKE = '1'
try {
    & $Python -m pip install -v --upgrade --force-reinstall --no-cache-dir --no-binary llama-cpp-python llama-cpp-python
    if ($LASTEXITCODE -ne 0) { throw "llama-cpp-python build failed ($LASTEXITCODE)" }
} finally {
    Remove-Item Env:\CMAKE_ARGS, Env:\FORCE_CMAKE -ErrorAction SilentlyContinue
}

$libDir = (& $Python -c "import llama_cpp, os; print(os.path.join(os.path.dirname(llama_cpp.__file__), 'lib'))" 2>$null).Trim()
$vulkanDll = if ($libDir) { Join-Path $libDir 'ggml-vulkan.dll' } else { '' }
$hasVulkanDll = ($vulkanDll -and (Test-Path $vulkanDll))
$runtimeGpu = (& $Python -c "from llama_cpp import llama_supports_gpu_offload; print('yes' if llama_supports_gpu_offload() else 'no')" 2>$null).Trim()

if ($useVulkan -and -not $hasVulkanDll) {
    if (Test-Path $marker) { Remove-Item $marker -Force -EA SilentlyContinue }
    throw "VERIFY FAIL: ggml-vulkan.dll missing from $libDir"
}

Set-Content -Path $marker -Value $wantMode -Force
Write-Host "VERIFY: mode=$wantMode ggml-vulkan.dll=$(if ($hasVulkanDll) {'present'} else {'absent'}) runtime_gpu=$runtimeGpu" -ForegroundColor Green
if ($useVulkan -and $runtimeGpu -eq 'no') {
    Write-Host "NOTE: No GPU on this build machine is normal; end-user PCs with iGPU/NVIDIA will offload at runtime." -ForegroundColor DarkGray
}

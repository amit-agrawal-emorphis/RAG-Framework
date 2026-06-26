<#
.SYNOPSIS
  Build llama-cpp-python with Vulkan on Windows (iGPU + NVIDIA auto, CPU fallback).

  Requires the LunarG Vulkan SDK: https://vulkan.lunarg.com/sdk/home#windows

  Usage:
    .\setup_llama_gpu_windows.ps1
    .\setup_llama_gpu_windows.ps1 -NoVulkan    # CPU-only wheel
#>
param(
    [string]$Python = '',
    [switch]$NoVulkan,
    [switch]$Vulkan
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
& (Join-Path $root 'scripts\Install-LlamaCppGpu.ps1') @PSBoundParameters -Root $root

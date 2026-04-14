# run.ps1 — Kill any existing Ollama, start a fresh server, then run agent.py

$ErrorActionPreference = "Stop"

Write-Host "=== LocalLLM Launcher ===" -ForegroundColor Cyan

# ── 1. Kill any existing Ollama processes ──────────────────────────────────────
Write-Host "`n[1/4] Checking for existing Ollama processes..." -ForegroundColor Yellow
$ollamaProcs = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
if ($ollamaProcs) {
    Write-Host "  Found $($ollamaProcs.Count) Ollama process(es). Stopping..." -ForegroundColor Red
    $ollamaProcs | Stop-Process -Force
    Start-Sleep -Seconds 2  # Give the OS time to release the port
    Write-Host "  Ollama processes terminated." -ForegroundColor Green
} else {
    Write-Host "  No existing Ollama processes found." -ForegroundColor Green
}

# ── 1.5. Kill orphaned headless browser processes ──────────────────────────────
Write-Host "`n[1.5/4] Cleaning up orphaned headless Chrome processes..." -ForegroundColor Yellow
$headlessChrome = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe' AND CommandLine LIKE '%--headless%'"
if ($headlessChrome) {
    Write-Host "  Found $($headlessChrome.Count) headless Chrome process(es). Stopping..." -ForegroundColor Red
    $headlessChrome | Invoke-CimMethod -MethodName Terminate | Out-Null
    Write-Host "  Headless Chrome processes terminated." -ForegroundColor Green
} else {
    Write-Host "  No orphaned headless Chrome processes found." -ForegroundColor Green
}

# ── 2. Start Ollama server in the background ───────────────────────────────────
Write-Host "`n[2/4] Starting Ollama server..." -ForegroundColor Yellow

# VRAM optimization: quantize KV cache to q8_0 (~50% KV VRAM savings) and enable flash attention
$env:OLLAMA_FLASH_ATTENTION = "1"
$env:OLLAMA_KV_CACHE_TYPE = "q8_0"
Write-Host "  KV cache: q8_0 | Flash Attention: ON" -ForegroundColor Magenta

$ollamaJob = Start-Process -FilePath "ollama" -ArgumentList "serve" `
    -PassThru -WindowStyle Hidden

# Wait a moment for the server to initialize
Start-Sleep -Seconds 3

# Verify it's actually running
$running = Get-Process -Id $ollamaJob.Id -ErrorAction SilentlyContinue
if (-not $running) {
    Write-Host "  ERROR: Ollama server failed to start!" -ForegroundColor Red
    exit 1
}
Write-Host "  Ollama server started (PID: $($ollamaJob.Id))" -ForegroundColor Green

# ── 3. Ensure dependencies are installed ───────────────────────────────────────
Write-Host "`n[3/4] Syncing dependencies with uv..." -ForegroundColor Yellow
Push-Location $PSScriptRoot
uv sync
if ($LASTEXITCODE -ne 0) {
    Write-Host "  ERROR: uv sync failed!" -ForegroundColor Red
    exit 1
}
Write-Host "  Dependencies synced." -ForegroundColor Green

# ── 4. Start Frontend Dev Server (Vite) ───────────────────────────────────────
Write-Host "`n[4/5] Starting Frontend dev server..." -ForegroundColor Yellow
$viteJob = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" `
    -WorkingDirectory "$PSScriptRoot\frontend" -PassThru -WindowStyle Hidden
Write-Host "  Vite dev server started (PID: $($viteJob.Id))" -ForegroundColor Green

# ── 5. Run main.py (Starts the FastAPI server) ──────────────────────────────
Write-Host "`n[5/5] Starting LocalLLM Server..." -ForegroundColor Yellow
Write-Host "  Backend API: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  Dashboard (Vite): http://localhost:5173" -ForegroundColor Cyan
Write-Host ("-" * 50) -ForegroundColor DarkGray
Start-Process "http://localhost:5173"
try {
    uv run python main.py
} finally {
    $agentExit = $LASTEXITCODE
    Pop-Location
    
    Write-Host ("-" * 50) -ForegroundColor DarkGray
    Write-Host "`nPerforming cleanup..." -ForegroundColor Yellow
    
    # 1. Kill Ollama
    $ollamaProcs = Get-Process -Name "ollama*" -ErrorAction SilentlyContinue
    if ($ollamaProcs) {
        $ollamaProcs | Stop-Process -Force
        Write-Host "  [OK] Killed Ollama processes." -ForegroundColor Green
    }
    
    # 2. Kill Vite
    if ($viteJob -and (Get-Process -Id $viteJob.Id -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $viteJob.Id -Force
        Write-Host "  [OK] Killed Vite dev server." -ForegroundColor Green
    }
    
    # 3. Kill orphaned headless Chrome
    $headlessChrome = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe' AND CommandLine LIKE '%--headless%'"
    if ($headlessChrome) {
        $headlessChrome | Invoke-CimMethod -MethodName Terminate | Out-Null
        Write-Host "  [OK] Killed headless Chrome processes." -ForegroundColor Green
    }

    if ($agentExit -eq 0) {
        Write-Host "`nLocalLLM Server finished successfully." -ForegroundColor Green
    } else {
        Write-Host "`nLocalLLM Server exited with code $agentExit." -ForegroundColor Red
    }
}

exit $agentExit

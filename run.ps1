# run.ps1 — Verify LM Studio connection, sync dependencies, then run LocalLLM

$ErrorActionPreference = "Stop"

Write-Host "=== LocalLLM Launcher (LM Studio) ===" -ForegroundColor Cyan

# ── 1. Clean up orphaned headless browser processes ──────────────────────────────
Write-Host "`n[1/4] Cleaning up orphaned headless Chrome processes..." -ForegroundColor Yellow
$headlessChrome = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe' AND CommandLine LIKE '%--headless%'"
if ($headlessChrome) {
    Write-Host "  Found $($headlessChrome.Count) headless Chrome process(es). Stopping..." -ForegroundColor Red
    $headlessChrome | Invoke-CimMethod -MethodName Terminate | Out-Null
    Write-Host "  Headless Chrome processes terminated." -ForegroundColor Green
} else {
    Write-Host "  No orphaned headless Chrome processes found." -ForegroundColor Green
}

# ── 2. Check LM Studio connectivity ──────────────────────────────────────────────
Write-Host "`n[2/4] Checking LM Studio server connectivity..." -ForegroundColor Yellow
$llmUrl = if ($env:LLM_BASE_URL) { $env:LLM_BASE_URL } else { "http://localhost:1234/v1" }

try {
    $modelsUrl = "$($llmUrl.TrimEnd('/'))/models"
    $response = Invoke-RestMethod -Uri $modelsUrl -Method Get -TimeoutSec 3 -ErrorAction Stop
    Write-Host "  [OK] Connected to LM Studio at $llmUrl" -ForegroundColor Green
    if ($response.data) {
        $loadedModels = ($response.data | ForEach-Object { $_.id }) -join ", "
        Write-Host "  Available/Loaded model(s): $loadedModels" -ForegroundColor Cyan
    }
} catch {
    Write-Host "  [WARNING] Could not connect to LM Studio at $llmUrl" -ForegroundColor Yellow
    Write-Host "  Please ensure LM Studio is running with its local server started on port 1234." -ForegroundColor Yellow
    Write-Host "  (In LM Studio: Local Server / Developer tab -> Start Server)" -ForegroundColor Yellow
}

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
Write-Host "`n[4/4] Starting Frontend dev server..." -ForegroundColor Yellow
$viteJob = Start-Process -FilePath "npm.cmd" -ArgumentList "run", "dev" `
    -WorkingDirectory "$PSScriptRoot\frontend" -PassThru -WindowStyle Hidden
Write-Host "  Vite dev server started (PID: $($viteJob.Id))" -ForegroundColor Green

# ── 5. Run main.py (Starts the FastAPI server) ──────────────────────────────
Write-Host "`nStarting LocalLLM Server..." -ForegroundColor Yellow
Write-Host "  Backend API: http://localhost:8000" -ForegroundColor Cyan
Write-Host "  Dashboard (Vite): http://localhost:5173" -ForegroundColor Cyan
Write-Host ("-" * 50) -ForegroundColor DarkGray
Start-Process "http://localhost:5173"
try {
    uv run python -m backend.main
} finally {
    $agentExit = $LASTEXITCODE
    Pop-Location
    
    Write-Host ("-" * 50) -ForegroundColor DarkGray
    Write-Host "`nPerforming cleanup..." -ForegroundColor Yellow
    
    # 1. Kill Vite
    if ($viteJob -and (Get-Process -Id $viteJob.Id -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $viteJob.Id -Force
        Write-Host "  [OK] Killed Vite dev server." -ForegroundColor Green
    }
    
    # 2. Kill orphaned headless Chrome
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


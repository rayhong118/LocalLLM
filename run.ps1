# run.ps1 — Verify LM Studio connection, sync dependencies, then run LocalLLM

$ErrorActionPreference = "Stop"

# Ensure local tool paths are available in PATH for current process
if (Test-Path "$HOME\.local\bin") {
    if ($env:Path -notlike "*$HOME\.local\bin*") {
        $env:Path = "$HOME\.local\bin;$env:Path"
    }
}
if (Test-Path "C:\Program Files\nodejs") {
    if ($env:Path -notlike "*C:\Program Files\nodejs*") {
        $env:Path = "C:\Program Files\nodejs;$env:Path"
    }
}

# Load .env file if present
$envPath = Join-Path $PSScriptRoot ".env"
if (Test-Path $envPath) {
    Get-Content $envPath | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $key, $val = $line.Split("=", 2)
            $key = $key.Trim()
            $val = $val.Trim()
            [System.Environment]::SetEnvironmentVariable($key, $val, "Process")
        }
    }
}

Write-Host "=== LocalLLM Launcher (LM Studio) ===" -ForegroundColor Cyan

# ── 1. Clean up orphaned task browser processes ─────────────────────────────────
Write-Host "`n[1/4] Cleaning up orphaned task Chrome processes..." -ForegroundColor Yellow
$taskChrome = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object { $_.CommandLine -like "*browser_session_web*" -or $_.CommandLine -like "*--headless*" }
if ($taskChrome) {
    Write-Host "  Found $($taskChrome.Count) task Chrome process(es). Stopping..." -ForegroundColor Red
    $taskChrome | Invoke-CimMethod -MethodName Terminate | Out-Null
    Write-Host "  Task Chrome processes terminated." -ForegroundColor Green
}
else {
    Write-Host "  No orphaned task Chrome processes found." -ForegroundColor Green
}

# ── 2. Ensure LM Studio is running in normal mode & check connectivity ────────────
Write-Host "`n[2/4] Checking LM Studio (normal mode)..." -ForegroundColor Yellow

$lmProc = Get-Process -Name "Bionic", "LM Studio" -ErrorAction SilentlyContinue
if (-not $lmProc) {
    $lmExe = @(
        "$env:LOCALAPPDATA\Programs\Bionic\Bionic.exe",
        "$HOME\AppData\Local\Programs\Bionic\Bionic.exe",
        "C:\Program Files\Bionic\Bionic.exe",
        "$env:LOCALAPPDATA\Programs\LM Studio\LM Studio.exe",
        "C:\Program Files\LM Studio\LM Studio.exe"
    ) | Where-Object { Test-Path $_ } | Select-Object -First 1

    if ($lmExe) {
        Write-Host "  LM Studio is not running. Starting in normal mode (GUI, not headless): $lmExe..." -ForegroundColor Cyan
        Start-Process -FilePath $lmExe -WindowStyle Normal
        Write-Host "  Waiting for LM Studio to initialize in normal mode..." -ForegroundColor Yellow
        Start-Sleep -Seconds 5
    }
    else {
        Write-Host "  [WARNING] LM Studio executable not found in default paths." -ForegroundColor Yellow
    }
}
else {
    Write-Host "  [OK] LM Studio is running in normal mode (GUI)." -ForegroundColor Green
}

$llmUrl = if ($env:LLM_BASE_URL) { $env:LLM_BASE_URL } else { "http://localhost:1234/v1" }
$serverRoot = $llmUrl.TrimEnd('/') -replace '/v1$', ''
$modelsUrl = "$($llmUrl.TrimEnd('/'))/models"
$connected = $false

for ($attempt = 1; $attempt -le 3; $attempt++) {
    try {
        $response = Invoke-RestMethod -Uri $modelsUrl -Method Get -TimeoutSec 3 -ErrorAction Stop
        Write-Host "  [OK] Connected to LM Studio at $llmUrl" -ForegroundColor Green
        $connected = $true
        break
    }
    catch {
        if ($attempt -lt 3 -and -not $lmProc) {
            Start-Sleep -Seconds 2
        }
    }
}

if (-not $connected) {
    Write-Host "  [WARNING] Could not connect to LM Studio at $llmUrl" -ForegroundColor Yellow
    Write-Host "  Please ensure LM Studio GUI is running in normal mode with its local server started." -ForegroundColor Yellow
    Write-Host "  (In LM Studio: Developer / Local Server tab -> Start Server)" -ForegroundColor Yellow
}
else {
    # ── Automatic Model Detection & Loading ─────────────────────────────────────────
    $targetModel = if ($env:LLM_MODEL) { $env:LLM_MODEL } else { "google/gemma-4-12b-qat" }
    $ctxLength = if ($env:CONTEXT_WINDOW) { [int]$env:CONTEXT_WINDOW } else { 32768 }

    try {
        # Query LM Studio v0 API for detailed model states
        $v0ModelsUrl = "$serverRoot/api/v0/models"
        $v0Res = Invoke-RestMethod -Uri $v0ModelsUrl -Method Get -TimeoutSec 5 -ErrorAction Stop
        $allModels = $v0Res.data
        $loadedModel = $allModels | Where-Object { $_.state -eq "loaded" } | Select-Object -First 1

        $needsLoad = $true
        if ($loadedModel) {
            # Check if currently loaded model matches requested targetModel
            if ($loadedModel.id -eq $targetModel -or $loadedModel.id -like "*$targetModel*" -or $targetModel -like "*$($loadedModel.id)*") {
                Write-Host "  [OK] Desired model already loaded in memory: $($loadedModel.id) (context: $($loadedModel.loaded_context_length))" -ForegroundColor Green
                $needsLoad = $false
            }
            else {
                Write-Host "  Currently loaded model '$($loadedModel.id)' does not match target '$targetModel'. Switching model..." -ForegroundColor Cyan
                try {
                    Invoke-RestMethod -Uri "$serverRoot/api/v1/models/unload" -Method Post -ContentType "application/json" -Body (@{ instance_id = $loadedModel.id } | ConvertTo-Json) -TimeoutSec 15 | Out-Null
                } catch {}
                $needsLoad = $true
            }
        }

        if ($needsLoad) {
            # Find best match for requested targetModel
            $matchedModel = $allModels | Where-Object { $_.id -eq $targetModel } | Select-Object -First 1
            if (-not $matchedModel) {
                $matchedModel = $allModels | Where-Object { $_.id -like "*$targetModel*" } | Select-Object -First 1
            }
            if (-not $matchedModel) {
                # Fallback to the first available LLM/VLM model
                $matchedModel = $allModels | Where-Object { $_.type -in @("llm", "vlm") } | Select-Object -First 1
            }

            if ($matchedModel) {
                $modelToLoad = $matchedModel.id
                Write-Host "  Auto-loading '$modelToLoad' into memory (context: $ctxLength)..." -ForegroundColor Cyan
                $loadUrl = "$serverRoot/api/v1/models/load"
                $loadBody = @{
                    model = $modelToLoad
                    context_length = $ctxLength
                } | ConvertTo-Json

                $loadRes = Invoke-RestMethod -Uri $loadUrl -Method Post -ContentType "application/json" -Body $loadBody -TimeoutSec 120
                Write-Host "  [OK] Successfully loaded model '$modelToLoad' into LM Studio!" -ForegroundColor Green
            }
            else {
                Write-Host "  [WARNING] No available model matching '$targetModel' found in LM Studio." -ForegroundColor Yellow
            }
        }
    }
    catch {
        Write-Host "  [NOTE] Could not auto-query or auto-load via LM Studio native API: $_" -ForegroundColor DarkGray
    }
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
if (-not (Test-Path "$PSScriptRoot\frontend\node_modules")) {
    Write-Host "  Installing frontend dependencies..." -ForegroundColor Yellow
    npm.cmd --prefix "$PSScriptRoot\frontend" install
}
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
}
finally {
    $agentExit = $LASTEXITCODE
    Pop-Location
    
    Write-Host ("-" * 50) -ForegroundColor DarkGray
    Write-Host "`nPerforming cleanup..." -ForegroundColor Yellow
    
    # 1. Kill Vite
    if ($viteJob -and (Get-Process -Id $viteJob.Id -ErrorAction SilentlyContinue)) {
        Stop-Process -Id $viteJob.Id -Force
        Write-Host "  [OK] Killed Vite dev server." -ForegroundColor Green
    }
    
    # 2. Close task Chrome browser window(s)
    $taskChrome = Get-CimInstance Win32_Process -Filter "Name = 'chrome.exe'" | Where-Object { $_.CommandLine -like "*browser_session_web*" -or $_.CommandLine -like "*--headless*" }
    if ($taskChrome) {
        $taskChrome | Invoke-CimMethod -MethodName Terminate | Out-Null
        Write-Host "  [OK] Closed task browser window(s)." -ForegroundColor Green
    }

    if ($agentExit -eq 0) {
        Write-Host "`nLocalLLM Server finished successfully." -ForegroundColor Green
    }
    else {
        Write-Host "`nLocalLLM Server exited with code $agentExit." -ForegroundColor Red
    }
}

exit $agentExit


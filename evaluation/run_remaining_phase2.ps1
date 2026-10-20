# ============================================================================
# Meet2MoM Phase 2 - remaining evaluation steps (resumable, safe to re-run)
#
# Run from the project root in PowerShell:
#     powershell -ExecutionPolicy Bypass -File evaluation\run_remaining_phase2.ps1
#
# Every step:
#   - is SKIPPED if its result file is already complete (no completed work is re-run)
#   - is SKIPPED (for now) if the same job is still running in another process;
#     just run this script again later
#   - resumes from its checkpoint if it was interrupted
#   - writes its output to evaluation\results\logs\<step>.log
# A summary is appended to evaluation\results\logs\run_remaining_summary.log
# ============================================================================
$ErrorActionPreference = "Continue"
Set-Location (Split-Path -Parent $PSScriptRoot)

$py   = ".\.venv\Scripts\python.exe"
$res  = "evaluation\results"
$logs = "$res\logs"
$summary = "$logs\run_remaining_summary.log"
New-Item -ItemType Directory -Force $logs | Out-Null
$env:PYTHONIOENCODING = "utf-8"
Remove-Item Env:FALLBACK_EXTRACTOR -ErrorAction SilentlyContinue
Remove-Item Env:DIALOGUE_MODEL_PATH -ErrorAction SilentlyContinue

function Log($msg) {
    $line = "{0}  {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Write-Host $line
    Add-Content -Path $summary -Value $line
}

function Test-Running($pattern) {
    @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
      Where-Object { $_.CommandLine -and $_.CommandLine -like "*$pattern*" }).Count -gt 0
}

function Test-Complete($file) {
    if (-not (Test-Path $file)) { return $false }
    try {
        $j = Get-Content $file -Raw | ConvertFrom-Json
        return ($j.complete -eq $true) -or ($null -ne $j.seconds) -or ($null -ne $j.seconds_total)
    } catch { return $false }
}

function Invoke-Step($name, $out, $pattern, [scriptblock]$cmd) {
    if (Test-Complete $out) { Log "[skip] $name - $out already complete"; return $true }
    if (Test-Running $pattern) { Log "[wait] $name - still running in another process; re-run this script later"; return $false }
    Log "[run ] $name -> $out (log: $logs\$name.log)"
    $start = Get-Date
    & $cmd *>> "$logs\$name.log"
    $ok = Test-Complete $out
    Log ("[{0}] {1} after {2:N1} min" -f ($(if ($ok) { "done" } else { "FAIL" })), $name, ((Get-Date) - $start).TotalMinutes)
    return $ok
}

Log "==== run_remaining_phase2.ps1 started ===="

# The held-out test split must be scored with the frozen model recorded on 2026-10-16.
$model = "app\services\intelligence\models\dialogue_model_v1.npz"
$expected = (Get-Content "$res\model_used_for_test.sha256" -Raw).Split(" ")[0].Trim().ToLower()
$actual = (Get-FileHash $model -Algorithm SHA256).Hash.ToLower()
if ($expected -ne $actual) {
    Log "[STOP] dialogue model changed since test scoring began (expected $expected, got $actual). Not scoring test data."
    exit 1
}

# 1-2. Held-out test extraction: rules-v2 baseline, then hybrid (same frozen model).
$testRules  = Invoke-Step "test_rules"  "$res\test_rules.json"  "--mode rules --sets ami-test"  { & $py -m evaluation.run_extraction_eval --mode rules  --sets ami-test --out "$res\test_rules.json" }
$testHybrid = Invoke-Step "test_hybrid" "$res\test_hybrid.json" "--mode hybrid --sets ami-test" { & $py -m evaluation.run_extraction_eval --mode hybrid --sets ami-test --out "$res\test_hybrid.json" }

# 3-4. Verification (deterministic + retrieval/NLI + fusion) on known-good and corrupted claims.
$verDev  = Invoke-Step "verification_dev"  "$res\verification_dev.json"  "run_verification_eval --split dev"  { & $py -m evaluation.run_verification_eval --split dev  --meetings 6 --per-meeting 3 --out "$res\verification_dev.json" }
$verTest = Invoke-Step "verification_test" "$res\verification_test.json" "run_verification_eval --split test" { & $py -m evaluation.run_verification_eval --split test --meetings 6 --per-meeting 3 --out "$res\verification_test.json" }

# 5. Fusion threshold calibration: chosen on dev, reported once on test.
if ($verDev -and $verTest) {
    if (Test-Path "$res\fusion_calibration.json") { Log "[skip] calibration - already done" }
    else {
        Log "[run ] calibration -> $res\fusion_calibration.json"
        & $py -m evaluation.calibrate_fusion --dev "$res\verification_dev.json" --test "$res\verification_test.json" --out "$res\fusion_calibration.json" *>> "$logs\calibration.log"
        Log "[done] calibration (exit $LASTEXITCODE)"
    }
} else { Log "[wait] calibration - needs both verification runs complete" }

# 6. Backend test suite (all tests, including slow real-model ones). Uses a temp storage root.
$allEvalDone = $testRules -and $testHybrid -and $verDev -and $verTest
if ($allEvalDone) {
    if (Test-Path "$res\pytest_phase2.xml") { Log "[skip] backend tests - $res\pytest_phase2.xml exists" }
    else {
        $dbBefore = (Get-FileHash "storage\meet2mom.db" -Algorithm SHA256).Hash
        Log "[run ] backend tests (log: $logs\pytest_phase2.log)"
        & $py -m pytest -p no:cacheprovider -q -rf --junitxml "$res\pytest_phase2.xml" *>> "$logs\pytest_phase2.log"
        $code = $LASTEXITCODE
        $dbAfter = (Get-FileHash "storage\meet2mom.db" -Algorithm SHA256).Hash
        Log "[done] backend tests exit=$code; production DB unchanged by tests: $($dbBefore -eq $dbAfter)"
    }

    # 7. Frontend tests and production build (dev dependencies must be included).
    if (Test-Path "$logs\frontend_phase2.log") { Log "[skip] frontend - log exists" }
    else {
        Push-Location frontend
        $env:NODE_ENV = "development"
        Log "[run ] frontend tests + build (log: $logs\frontend_phase2.log)"
        npx vitest run *>> "..\$logs\frontend_phase2.log"
        $t = $LASTEXITCODE
        npx vite build --outDir ..\eval_data\frontend_build_check --emptyOutDir *>> "..\$logs\frontend_phase2.log"
        $b = $LASTEXITCODE
        Pop-Location
        Log "[done] frontend tests exit=$t build exit=$b"
    }
} else { Log "[wait] tests - run after all evaluations are complete (re-run this script)" }

Log "==== finished; send Claude: evaluation\results\logs\run_remaining_summary.log ===="

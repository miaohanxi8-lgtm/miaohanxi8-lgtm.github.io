param(
    [Parameter(Position = 0)]
    [ValidateSet('fetch', 'test-login', 'commit', 'storage-report')]
    [string]$Action = 'fetch',

    [int]$Days = 7,

    [int]$Uid = 0,

    [string]$DataDir = ''
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonScript = Join-Path $scriptDir 'qq_mail_export.py'

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    if (-not [string]::IsNullOrWhiteSpace($env:AUTUMN_RECRUITMENT_MAIL_DATA_DIR)) {
        $DataDir = $env:AUTUMN_RECRUITMENT_MAIL_DATA_DIR
    }
    else {
        $skillRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)
        $codexSkillsDir = Split-Path -Parent $skillRoot
        $codexDir = Split-Path -Parent $codexSkillsDir
        $userProfileFromSkill = Split-Path -Parent $codexDir
        $migratedDataDir = Join-Path $userProfileFromSkill 'Documents\Codex\.runtime\autumn-recruitment-system\qq_job_mail'
        $migratedConfig = Join-Path $migratedDataDir 'config.json'
        $migratedSecret = Join-Path $migratedDataDir 'qq_auth.dpapi'
        if ((Test-Path -LiteralPath $migratedConfig) -and (Test-Path -LiteralPath $migratedSecret)) {
            $DataDir = $migratedDataDir
        }
    }
}

$commonArgs = @()
if (-not [string]::IsNullOrWhiteSpace($DataDir)) {
    $commonArgs += @('--data-dir', $DataDir)
}

if ($Action -eq 'commit') {
    if ($Uid -lt 0) {
        throw 'The commit action requires a non-negative UID.'
    }
    & python $pythonScript @commonArgs commit $Uid
}
elseif ($Action -eq 'test-login') {
    & python $pythonScript @commonArgs test-login
}
elseif ($Action -eq 'storage-report') {
    & python $pythonScript @commonArgs storage-report
}
else {
    & python $pythonScript @commonArgs fetch --days $Days
}

exit $LASTEXITCODE


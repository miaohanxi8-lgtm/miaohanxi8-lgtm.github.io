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


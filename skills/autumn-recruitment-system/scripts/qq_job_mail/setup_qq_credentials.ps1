param(
    [string]$DataDir = ''
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($DataDir)) {
    if (-not [string]::IsNullOrWhiteSpace($env:AUTUMN_RECRUITMENT_MAIL_DATA_DIR)) {
        $DataDir = $env:AUTUMN_RECRUITMENT_MAIL_DATA_DIR
    }
    else {
        $DataDir = Join-Path $env:LOCALAPPDATA 'Codex\autumn-recruitment-system\qq_job_mail'
    }
}

$resolvedDataDir = [System.IO.Path]::GetFullPath($DataDir)
New-Item -ItemType Directory -Path $resolvedDataDir -Force | Out-Null
$configPath = Join-Path $resolvedDataDir 'config.json'
$secretPath = Join-Path $resolvedDataDir 'qq_auth.dpapi'

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Security

$form = New-Object System.Windows.Forms.Form
$form.Text = '连接 QQ 邮箱（只读）'
$form.Size = New-Object System.Drawing.Size(540, 310)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.TopMost = $true

$intro = New-Object System.Windows.Forms.Label
$intro.Location = New-Object System.Drawing.Point(24, 18)
$intro.Size = New-Object System.Drawing.Size(475, 62)
$intro.Text = "输入 QQ 邮箱地址和 IMAP/SMTP 授权码。`r`n不要输入 QQ 登录密码；授权码使用 Windows 当前用户 DPAPI 加密。"
$form.Controls.Add($intro)

$emailLabel = New-Object System.Windows.Forms.Label
$emailLabel.Location = New-Object System.Drawing.Point(24, 92)
$emailLabel.Size = New-Object System.Drawing.Size(110, 24)
$emailLabel.Text = 'QQ 邮箱地址'
$form.Controls.Add($emailLabel)

$emailBox = New-Object System.Windows.Forms.TextBox
$emailBox.Location = New-Object System.Drawing.Point(140, 90)
$emailBox.Size = New-Object System.Drawing.Size(350, 26)
$form.Controls.Add($emailBox)

$codeLabel = New-Object System.Windows.Forms.Label
$codeLabel.Location = New-Object System.Drawing.Point(24, 135)
$codeLabel.Size = New-Object System.Drawing.Size(110, 24)
$codeLabel.Text = '邮箱授权码'
$form.Controls.Add($codeLabel)

$codeBox = New-Object System.Windows.Forms.TextBox
$codeBox.Location = New-Object System.Drawing.Point(140, 133)
$codeBox.Size = New-Object System.Drawing.Size(350, 26)
$codeBox.UseSystemPasswordChar = $true
$form.Controls.Add($codeBox)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Location = New-Object System.Drawing.Point(24, 174)
$statusLabel.Size = New-Object System.Drawing.Size(475, 28)
$statusLabel.ForeColor = [System.Drawing.Color]::DarkRed
$form.Controls.Add($statusLabel)

$saveButton = New-Object System.Windows.Forms.Button
$saveButton.Location = New-Object System.Drawing.Point(310, 215)
$saveButton.Size = New-Object System.Drawing.Size(85, 32)
$saveButton.Text = '安全保存'
$saveButton.Add_Click({
    $address = $emailBox.Text.Trim()
    $authCode = $codeBox.Text.Trim()
    if ($address -notmatch '^[^@\s]+@(qq\.com|foxmail\.com)$') {
        $statusLabel.Text = '请输入有效的 @qq.com 或 @foxmail.com 地址。'
        return
    }
    if ([string]::IsNullOrWhiteSpace($authCode)) {
        $statusLabel.Text = '请输入 QQ 邮箱生成的 IMAP/SMTP 授权码。'
        return
    }

    $plainBytes = [System.Text.Encoding]::UTF8.GetBytes($authCode)
    try {
        $protectedBytes = [System.Security.Cryptography.ProtectedData]::Protect(
            $plainBytes,
            $null,
            [System.Security.Cryptography.DataProtectionScope]::CurrentUser
        )
        [Convert]::ToBase64String($protectedBytes) | Set-Content -LiteralPath $secretPath -Encoding ASCII
        @{ email = $address; initial_days = 7 } | ConvertTo-Json | Set-Content -LiteralPath $configPath -Encoding UTF8
    }
    finally {
        if ($plainBytes) { [Array]::Clear($plainBytes, 0, $plainBytes.Length) }
        if ($protectedBytes) { [Array]::Clear($protectedBytes, 0, $protectedBytes.Length) }
        $codeBox.Text = ''
    }

    $form.DialogResult = [System.Windows.Forms.DialogResult]::OK
    $form.Close()
})
$form.Controls.Add($saveButton)

$cancelButton = New-Object System.Windows.Forms.Button
$cancelButton.Location = New-Object System.Drawing.Point(405, 215)
$cancelButton.Size = New-Object System.Drawing.Size(85, 32)
$cancelButton.Text = '取消'
$cancelButton.Add_Click({
    $form.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
    $form.Close()
})
$form.Controls.Add($cancelButton)

$form.AcceptButton = $saveButton
$form.CancelButton = $cancelButton
$result = $form.ShowDialog()

if ($result -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output "QQ_CREDENTIALS_SAVED=$resolvedDataDir"
    exit 0
}

Write-Output 'QQ_CREDENTIALS_CANCELLED'
exit 2


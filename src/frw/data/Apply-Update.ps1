param(
    [Parameter(Mandatory=$true)][string]$Request,
    [Parameter(Mandatory=$true)][int]$ParentPid,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $Request -Raw | ConvertFrom-Json
$installer = [IO.Path]::GetFullPath($plan.installer)
$executable = [IO.Path]::GetFullPath($plan.executable)
$updateProfile = [IO.Path]::GetFullPath($plan.profile)
$resultPath = Join-Path (Split-Path -Parent $Request) 'install-result.json'
$result = @{version=$plan.version; success=$false; message=''; checked_at_utc=[DateTime]::UtcNow.ToString('o')}
$parentExited = $false
function Quote-Argument([string]$value) {
    # Windows command-line quoting, not shell code. Paths are never executed.
    return '"' + [regex]::Replace([regex]::Replace($value, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"'
}
try {
    if ($plan.version -notmatch '^\d{1,6}\.\d{1,6}\.\d{1,6}$' -or
        [IO.Path]::GetFileName($installer) -cne "FlightRadar24-$($plan.version)-Windows-x64-Setup.exe" -or
        [IO.Path]::GetFileName($executable) -ine 'FlightRadar24.exe' -or
        $plan.sha256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Invalid update request.' }
    if (-not $ValidateOnly) {
        $parent = Get-Process -Id $ParentPid -ErrorAction SilentlyContinue
        if ($parent -and -not $parent.WaitForExit(90000)) { throw 'The application did not close; installation was cancelled.' }
        $parentExited = $true
    }
    $item = Get-Item -LiteralPath $installer
    $stream = [IO.File]::OpenRead($installer)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { $digest = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
    if ($item.Length -ne $plan.size -or $digest -ine $plan.sha256) {
        throw 'Installer verification failed. Nothing was installed.'
    }
    if ($ValidateOnly) { Write-Output 'Update request and installer digest verified.'; exit 0 }
    $arguments = '/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /TASKS= /FRWUPDATE=1 /NOCLOSEAPPLICATIONS /NORESTARTAPPLICATIONS /DIR=' +
        (Quote-Argument (Split-Path -Parent $executable)) + ' /LOG=' + (Quote-Argument (Join-Path (Split-Path -Parent $Request) 'install.log'))
    $setup = Start-Process -FilePath $installer -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
    if ($setup.ExitCode -ne 0) { throw "Setup returned exit code $($setup.ExitCode). Check the update installation log." }
    $result.success = $true
    $result.message = "Updated to $($plan.version)."
} catch {
    $result.message = $_.Exception.Message
    if ($ValidateOnly) { [Console]::Error.WriteLine($result.message) }
} finally {
    if (-not $ValidateOnly) {
        $result | ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding UTF8
        if ($parentExited -and (Test-Path -LiteralPath $executable -PathType Leaf)) {
            $arguments = '--data-dir ' + (Quote-Argument $updateProfile)
            if ($plan.resume_receiver -eq $true) { $arguments += ' --start-receiver' }
            # The user-facing main application must open a visible window.
            Start-Process -FilePath $executable -ArgumentList $arguments
        }
    }
}
if (-not $result.success) { exit 1 }

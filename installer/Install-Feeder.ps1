<#
Install the optional, unmodified official feeder from its vendor download.
The public installer does not redistribute this proprietary executable.
No receiver settings, sharing key or account details are read or changed.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$InstallDirectory,
    [string]$ArchivePath,
    [string]$SummaryPath
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$FeederUrl = 'https://repo-feed.flightradar24.com/linux_binaries/fr24feed_1.0.57-1_amd64.tgz'
$ArchiveSha256 = '7d746c4b32a2f82bb8dd2c7666e3f8eb7028e2833d603fc8a7a6907d6fae328d'
$FeederSha256 = 'a6ea25ce76c4c4341f08a4edf81509314ad414cf6601c10b439f4c7cbbfeaa74'
$FeederTemporary = $null
$StagedFeeder = $null

function Get-Sha256([string]$Path) {
    $Hasher = [Security.Cryptography.SHA256]::Create()
    $Stream = [IO.File]::OpenRead($Path)
    try {
        return [BitConverter]::ToString($Hasher.ComputeHash($Stream)).Replace('-', '').ToLowerInvariant()
    } finally {
        $Stream.Dispose()
        $Hasher.Dispose()
    }
}

function Write-Result([string]$Message) {
    if ($SummaryPath) {
        $ResolvedSummary = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($SummaryPath)
        [IO.File]::WriteAllText($ResolvedSummary, $Message, (New-Object Text.UTF8Encoding($false)))
    }
    Write-Output $Message
}

try {
    $InstallRoot = (Resolve-Path -LiteralPath $InstallDirectory).ProviderPath
    if (-not (Test-Path -LiteralPath (Join-Path $InstallRoot 'FlightRadar24.exe') -PathType Leaf)) {
        throw 'Choose the installed FlightRadar24 application folder.'
    }
    $FeederDirectory = Join-Path $InstallRoot 'runtime\feeder'
    if (-not (Test-Path -LiteralPath $FeederDirectory -PathType Container)) {
        throw 'The FlightRadar24 compatibility runtime is missing. Reinstall the application.'
    }
    $Destination = Join-Path $FeederDirectory 'fr24feed'
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-Sha256 $Destination) -eq $FeederSha256) {
            Write-Result 'The verified Flightradar24 sharing component is already installed.'
            exit 0
        }
        throw 'A different sharing component is already installed. It has been left unchanged.'
    }

    $TemporaryRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    $FeederTemporary = Join-Path $TemporaryRoot ('frw-feeder-' + [Guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $FeederTemporary | Out-Null
    $Archive = Join-Path $FeederTemporary 'official-feeder.tgz'
    if ($ArchivePath) {
        $ArchiveSource = (Resolve-Path -LiteralPath $ArchivePath).ProviderPath
        Copy-Item -LiteralPath $ArchiveSource -Destination $Archive
    } else {
        # Windows certificate verification stays enabled. This only selects TLS
        # for this PowerShell process; it does not change system TLS settings.
        [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
        Invoke-WebRequest -UseBasicParsing -Uri $FeederUrl -OutFile $Archive -TimeoutSec 60
    }
    if ((Get-Item -LiteralPath $Archive).Length -ne 6886662 -or
        (Get-Sha256 $Archive) -ne $ArchiveSha256) {
        throw 'The vendor download did not match the verified release. No component was installed.'
    }
    $Tar = Join-Path ([Environment]::GetFolderPath('Windows')) 'System32\tar.exe'
    if (-not (Test-Path -LiteralPath $Tar -PathType Leaf)) {
        throw 'Windows tar.exe is required for optional sharing setup (Windows 10 version 1803 or later).'
    }
    # Only the two fixed members of this hash-verified archive are extracted.
    & $Tar -xzf $Archive -C $FeederTemporary 'fr24feed_amd64/fr24feed' 'fr24feed_amd64/licences'
    if ($LASTEXITCODE -ne 0) { throw 'The verified vendor archive could not be extracted.' }
    $Extracted = Join-Path $FeederTemporary 'fr24feed_amd64\fr24feed'
    if ((Get-Sha256 $Extracted) -ne $FeederSha256) {
        throw 'The extracted sharing component did not match the verified executable.'
    }
    $Notices = Join-Path $InstallRoot 'runtime\licences\fr24feed'
    New-Item -ItemType Directory -Path $Notices -Force | Out-Null
    Get-ChildItem -LiteralPath (Join-Path $FeederTemporary 'fr24feed_amd64\licences') -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Notices $_.Name) -Force
    }
    # Destination must still be absent. A concurrent install never overwrites
    # another component or changes a running receiver's configuration.
    $StagedFeeder = Join-Path $FeederDirectory ('.fr24feed-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    [IO.File]::Copy($Extracted, $StagedFeeder, $false)
    [IO.File]::Move($StagedFeeder, $Destination)
    Write-Result 'Flightradar24 sharing component installed from its official source. Configure sharing in Receiver settings.'
    exit 0
} catch {
    Write-Result ('Optional sharing setup failed: ' + $_.Exception.Message + ' Local aircraft reception is still available. Run setup again to retry.')
    exit 21
} finally {
    if ($StagedFeeder -and [IO.File]::Exists($StagedFeeder)) {
        [IO.File]::Delete($StagedFeeder)
    }
    if ($FeederTemporary -and (Test-Path -LiteralPath $FeederTemporary)) {
        $ResolvedTemporary = [IO.Path]::GetFullPath($FeederTemporary)
        $TemporaryItem = Get-Item -LiteralPath $ResolvedTemporary
        if ($ResolvedTemporary.StartsWith($TemporaryRoot, [StringComparison]::OrdinalIgnoreCase) -and
            $TemporaryItem.Name.StartsWith('frw-feeder-') -and
            -not ($TemporaryItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Remove-Item -LiteralPath $ResolvedTemporary -Recurse -Force
        }
    }
}

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or
    -not $IsWindows -or -not $env:GITHUB_WORKSPACE -or -not $env:RUNNER_TEMP) {
    throw 'This entry point requires a disposable GitHub-hosted Windows runner.'
}

$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '../..')).Path
$workspace = (Resolve-Path -LiteralPath $env:GITHUB_WORKSPACE).Path
$runnerTemp = (Resolve-Path -LiteralPath $env:RUNNER_TEMP).Path
if ($repo -ne $workspace -or $runnerTemp -eq $repo -or
    $runnerTemp.StartsWith($repo + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Expected the checked-out workspace and a separate runner temporary directory.'
}

$python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source
$git = (Get-Command git.exe -CommandType Application | Select-Object -First 1).Source
$system32 = Join-Path $env:SystemRoot 'System32'
$icacls = Join-Path $system32 'icacls.exe'
$nonce = [Guid]::NewGuid().ToString('N')
$accountName = 'ruocun-ci-' + $nonce.Substring(0, 8)
$state = Join-Path $repo '.cache/ci'
$childRoot = Join-Path $runnerTemp ('ruocun-hosted-' + $nonce)
$childHome = Join-Path $childRoot 'home'
$childWork = Join-Path $childRoot 'work'
[void][IO.Directory]::CreateDirectory($state)
[void][IO.Directory]::CreateDirectory($childHome)
[void][IO.Directory]::CreateDirectory($childWork)

function Invoke-Acl([string] $Target, [string[]] $Options) {
    & $icacls $Target @Options /Q | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Temporary runner account ACL operation $($Options[0]) failed (icacls exit $LASTEXITCODE)."
    }
}

$password = [Security.SecureString]::new()
$upper = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
$lower = 'abcdefghijklmnopqrstuvwxyz'
$digits = '0123456789'
$symbols = '!#%*-_+@'
foreach ($alphabet in @($upper, $lower, $digits, $symbols)) {
    $password.AppendChar($alphabet[[Security.Cryptography.RandomNumberGenerator]::GetInt32($alphabet.Length)])
}
$characters = $upper + $lower + $digits + $symbols
for ($count = 0; $count -lt 44; $count++) {
    $password.AppendChar($characters[[Security.Cryptography.RandomNumberGenerator]::GetInt32($characters.Length)])
}
$password.MakeReadOnly()

$account = $null
$process = $null
$granted = [System.Collections.Generic.List[string]]::new()
$code = 1
try {
    $account = New-LocalUser -Name $accountName -Password $password -UserMayNotChangePassword -Description 'Disposable RuoCun hosted quality checks'
    $sid = $account.SID.Value
    $users = [Security.Principal.SecurityIdentifier]::new('S-1-5-32-545')
    if (-not (Get-LocalGroupMember -SID $users | Where-Object { $_.SID.Value -eq $sid })) {
        Add-LocalGroupMember -SID $users -Member $account
    }
    foreach ($permission in @(@($repo, 'RX'), @($state, 'M'), @($childRoot, 'M'))) {
        Invoke-Acl $permission[0] @('/grant', "*${sid}:(OI)(CI)$($permission[1])")
        $granted.Add($permission[0])
    }

    $start = [Diagnostics.ProcessStartInfo]::new($python)
    $start.UserName = $accountName
    $start.Domain = $env:COMPUTERNAME
    $start.Password = $password
    $start.LoadUserProfile = $true
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.WorkingDirectory = $repo
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.Encoding]::UTF8
    $start.StandardErrorEncoding = [Text.Encoding]::UTF8
    $start.Environment.Clear()
    $start.Environment['SystemRoot'] = $env:SystemRoot
    $start.Environment['WINDIR'] = $env:SystemRoot
    $start.Environment['COMSPEC'] = Join-Path $system32 'cmd.exe'
    $start.Environment['PATH'] = ((Split-Path -Parent $python), (Split-Path -Parent $git), $system32) -join ';'
    $start.Environment['PATHEXT'] = '.COM;.EXE;.BAT;.CMD'
    $start.Environment['HOME'] = $childHome
    $start.Environment['USERPROFILE'] = $childHome
    $start.Environment['APPDATA'] = $childHome
    $start.Environment['LOCALAPPDATA'] = $childHome
    $start.Environment['TEMP'] = $childRoot
    $start.Environment['TMP'] = $childRoot
    $arguments = @('-I', '-X', 'utf8', (Join-Path $PSScriptRoot 'windows_gate.py'), '--state', $state,
        '--work', $childWork, '--git', $git, '--without-native-mxc')
    foreach ($argument in $arguments) {
        $start.ArgumentList.Add($argument)
    }
    $process = [Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEndAsync()
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    [Console]::Out.Write($stdout.GetAwaiter().GetResult())
    [Console]::Error.Write($stderr.GetAwaiter().GetResult())
    $code = $process.ExitCode
} finally {
    try {
        if ($null -ne $process) {
            if (-not $process.HasExited) {
                $process.Kill($true)
                $process.WaitForExit()
            }
            $process.Dispose()
        }
        if ($null -ne $account) {
            try {
                foreach ($target in $granted) {
                    Invoke-Acl $target @('/remove:g', "*$($account.SID.Value)")
                }
            } finally {
                Remove-LocalUser -SID $account.SID
            }
        }
    } finally {
        $password.Dispose()
    }
}
exit $code

param(
  [Parameter(Mandatory = $true)]
  [string]$ConformanceRoot,
  [string]$ArtifactDirectory = "artifacts/wire-e2e",
  [string]$PythonExecutable = "python",
  [string]$NativeArtifactRoot = "src/nnrp/native_artifacts"
)

$ErrorActionPreference = "Stop"
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$conformanceRoot = [System.IO.Path]::GetFullPath($ConformanceRoot)
$artifactRoot = if ([System.IO.Path]::IsPathRooted($ArtifactDirectory)) {
  [System.IO.Path]::GetFullPath($ArtifactDirectory)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $ArtifactDirectory))
}
$nativeArtifactRoot = if ([System.IO.Path]::IsPathRooted($NativeArtifactRoot)) {
  [System.IO.Path]::GetFullPath($NativeArtifactRoot)
} else {
  [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $NativeArtifactRoot))
}
$suiteManifest = Join-Path $conformanceRoot "wire-conformance/nnrp-1-preview4/manifest.json"
$executableSuffix = if ($IsWindows) { ".exe" } else { "" }
$runnerExecutable = Join-Path $conformanceRoot "target/debug/nnrp-conformance-runner$executableSuffix"
$resolvedPythonExecutable = (Get-Command $PythonExecutable -ErrorAction Stop).Source
$hostRouteTargetCommand = Get-Command "nnrp-wire-host-route-target" -CommandType Application -ErrorAction SilentlyContinue
$hostRouteTargetExecutable = if ($null -eq $hostRouteTargetCommand) { "" } else { $hostRouteTargetCommand.Source }

function Invoke-Checked {
  param(
    [Parameter(Mandatory = $true)]
    [string]$FilePath,
    [Parameter(Mandatory = $true)]
    [string[]]$ArgumentList,
    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory
  )

  Push-Location $WorkingDirectory
  try {
    & $FilePath @ArgumentList
    $exitCode = $LASTEXITCODE
  } finally {
    Pop-Location
  }
  if ($exitCode -ne 0) {
    throw "$FilePath failed with exit code $exitCode."
  }
}

function Get-FreeTcpPort {
  $listener = [System.Net.Sockets.TcpListener]::new(
    [System.Net.IPAddress]::Loopback,
    0
  )
  $listener.Start()
  try {
    return ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
  } finally {
    $listener.Stop()
  }
}

function Get-FreeUdpPort {
  $client = [System.Net.Sockets.UdpClient]::new(0)
  try {
    return ([System.Net.IPEndPoint]$client.Client.LocalEndPoint).Port
  } finally {
    $client.Dispose()
  }
}

function New-WireCertificate {
  param([Parameter(Mandatory = $true)][string]$Directory)

  New-Item -ItemType Directory -Force -Path $Directory | Out-Null
  $rsa = [System.Security.Cryptography.RSA]::Create(2048)
  $subject = [System.Security.Cryptography.X509Certificates.X500DistinguishedName]::new("CN=localhost")
  $request = [System.Security.Cryptography.X509Certificates.CertificateRequest]::new(
    $subject,
    $rsa,
    [System.Security.Cryptography.HashAlgorithmName]::SHA256,
    [System.Security.Cryptography.RSASignaturePadding]::Pkcs1
  )
  $subjectAlternativeName = [System.Security.Cryptography.X509Certificates.SubjectAlternativeNameBuilder]::new()
  $subjectAlternativeName.AddDnsName("localhost")
  $request.CertificateExtensions.Add($subjectAlternativeName.Build())
  $certificate = $request.CreateSelfSigned(
    [System.DateTimeOffset]::UtcNow.AddMinutes(-5),
    [System.DateTimeOffset]::UtcNow.AddDays(1)
  )
  try {
    [System.IO.File]::WriteAllBytes(
      (Join-Path $Directory "server.der"),
      $certificate.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    )
    [System.IO.File]::WriteAllBytes(
      (Join-Path $Directory "server-key.der"),
      $rsa.ExportPkcs8PrivateKey()
    )
  } finally {
    $certificate.Dispose()
    $rsa.Dispose()
  }
}

function Wait-ForReadyFile {
  param(
    [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Process,
    [Parameter(Mandatory = $true)][string]$ReadyPath,
    [int]$TimeoutSeconds = 20
  )

  $deadline = [System.DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
  while ([System.DateTimeOffset]::UtcNow -lt $deadline) {
    if (Test-Path -LiteralPath $ReadyPath) {
      return
    }
    if ($Process.HasExited) {
      throw "Python wire target exited before publishing readiness (exit $($Process.ExitCode))."
    }
    Start-Sleep -Milliseconds 100
  }
  throw "Python wire target did not publish readiness within $TimeoutSeconds seconds."
}

if (-not (Test-Path -LiteralPath $suiteManifest)) {
  throw "Preview4 wire suite manifest not found: $suiteManifest"
}
if (-not (Test-Path -LiteralPath $nativeArtifactRoot)) {
  throw "Prepared native artifact root not found: $nativeArtifactRoot"
}

New-Item -ItemType Directory -Force -Path $artifactRoot | Out-Null
Invoke-Checked -FilePath "cargo" -ArgumentList @(
  "build",
  "-p", "nnrp-conformance-runner",
  "--bins"
) -WorkingDirectory $conformanceRoot

$pathSeparator = [System.IO.Path]::PathSeparator
$sourceRoot = Join-Path $repositoryRoot "src"
$env:PYTHONPATH = if ([string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
  $sourceRoot
} else {
  "$sourceRoot$pathSeparator$($env:PYTHONPATH)"
}
$env:NNRP_NATIVE_ARTIFACT_ROOT = $nativeArtifactRoot
$env:NNRP_NATIVE_E2E = "1"

Invoke-Checked -FilePath $PythonExecutable -ArgumentList @(
  "-m", "pytest",
  "tests/test_native_artifact_e2e.py",
  "-q"
) -WorkingDirectory $repositoryRoot

$capabilities = @(
  "control.cancel_abort",
  "control.result_drop_reason",
  "control.trace_context",
  "control.priority_update",
  "control.deadline_expire",
  "control.progress_partial",
  "control.credit_backpressure",
  "object.lifecycle",
  "control.capability_costs",
  "control.route_execution_hint",
  "cache.reference",
  "control.degrade_profile",
  "control.budget_update"
)
$modes = @("suite_as_client", "suite_as_server", "suite_as_proxy")
$summaries = @()

foreach ($mode in $modes) {
  $modeDirectory = Join-Path $artifactRoot $mode
  $certificateDirectory = Join-Path $modeDirectory "certs"
  if (Test-Path -LiteralPath $modeDirectory) {
    Remove-Item -LiteralPath $modeDirectory -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $modeDirectory | Out-Null
  New-WireCertificate -Directory $certificateDirectory

  $targetManifest = Join-Path $modeDirectory "target.json"
  $executionPlan = Join-Path $modeDirectory "plan.json"
  $resultReport = Join-Path $modeDirectory "results.json"
  $evidenceDirectory = Join-Path $modeDirectory "evidence"
  $readyPath = Join-Path $modeDirectory "ready"
  $targetStdout = Join-Path $modeDirectory "target.stdout.log"
  $targetStderr = Join-Path $modeDirectory "target.stderr.log"
  $tcpPort = Get-FreeTcpPort
  $quicPort = Get-FreeUdpPort
  $webSocketPort = Get-FreeTcpPort
  $ipcEndpoint = if ($IsWindows) {
    "npipe://nnrp-py-wire-$PID-$mode"
  } else {
    "unix:///tmp/nnrp-py-wire-$PID-$mode.sock"
  }

  $transportArguments = switch ($mode) {
    "suite_as_client" {
      @(
        "--transport", "tcp=127.0.0.1:$tcpPort",
        "--transport", "quic=127.0.0.1:$quicPort",
        "--transport", "ipc=$ipcEndpoint"
      )
    }
    "suite_as_server" {
      @(
        "--transport", "tcp=127.0.0.1:$tcpPort",
        "--transport", "websocket=wss://localhost:$webSocketPort/nnrp"
      )
    }
    "suite_as_proxy" {
      @("--transport", "quic=127.0.0.1:$quicPort")
    }
  }

  $securityArguments = @()
  if ($mode -in @("suite_as_client", "suite_as_server")) {
    $securityArguments += @(
      "--transport-security",
      (@{
          transport = "tcp"
          server_name = "localhost"
          trusted_certificate_der_path = "certs/server.der"
          certificate_der_path = "certs/server.der"
          private_key_pkcs8_der_path = "certs/server-key.der"
        } | ConvertTo-Json -Compress)
    )
  }
  if ($mode -in @("suite_as_client", "suite_as_proxy")) {
    $securityArguments += @(
      "--transport-security",
      (@{
          transport = "quic"
          server_name = "localhost"
          trusted_certificate_der_path = "certs/server.der"
          certificate_der_path = "certs/server.der"
          private_key_pkcs8_der_path = "certs/server-key.der"
        } | ConvertTo-Json -Compress)
    )
  }
  if ($mode -eq "suite_as_server") {
    $securityArguments += @(
      "--transport-security",
      (@{
          transport = "websocket"
          server_name = "localhost"
          trusted_certificate_der_path = "certs/server.der"
          certificate_der_path = "certs/server.der"
          private_key_pkcs8_der_path = "certs/server-key.der"
        } | ConvertTo-Json -Compress)
    )
  }

  $manifestArguments = @(
    "-m", "nnrp.tools.wire_conformance",
    "manifest",
    "--target-name", "nnrp-py-live-$mode",
    "--mode", $mode
  ) + $transportArguments + $securityArguments
  foreach ($capability in $capabilities) {
    $manifestArguments += @("--capability", $capability)
  }
  $manifestArguments += @("--output", $targetManifest)
  Invoke-Checked -FilePath $PythonExecutable -ArgumentList $manifestArguments -WorkingDirectory $repositoryRoot

  Invoke-Checked -FilePath $runnerExecutable -ArgumentList @(
    "wire-plan",
    "--suite", $suiteManifest,
    "--target", $targetManifest,
    "--output", $executionPlan,
    "--results-path", $resultReport,
    "--evidence-dir", $evidenceDirectory
  ) -WorkingDirectory $repositoryRoot

  $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
  $startInfo.FileName = $PythonExecutable
  foreach ($argument in @(
      "-m", "nnrp.tools.wire_conformance",
      "serve-target",
      "--plan", $executionPlan,
      "--target", $targetManifest,
      "--mode", $mode,
      "--ready-file", $readyPath,
      "--timeout-seconds", "20"
    )) {
    $startInfo.ArgumentList.Add($argument)
  }
  $startInfo.WorkingDirectory = $repositoryRoot
  $startInfo.UseShellExecute = $false
  $startInfo.RedirectStandardOutput = $true
  $startInfo.RedirectStandardError = $true

  $targetProcess = [System.Diagnostics.Process]::new()
  $targetProcess.StartInfo = $startInfo
  if (-not $targetProcess.Start()) {
    throw "Failed to start Python wire target for $mode."
  }
  $stdoutTask = $targetProcess.StandardOutput.ReadToEndAsync()
  $stderrTask = $targetProcess.StandardError.ReadToEndAsync()
  $runnerError = $null
  try {
    Wait-ForReadyFile -Process $targetProcess -ReadyPath $readyPath
    try {
      Invoke-Checked -FilePath $runnerExecutable -ArgumentList @(
        "wire-run",
        "--plan", $executionPlan,
        "--target", $targetManifest,
        "--output", $resultReport
      ) -WorkingDirectory $repositoryRoot
    } catch {
      $runnerError = $_
    }

    if (-not $targetProcess.WaitForExit(20000)) {
      throw "Python wire target did not finish after $mode execution."
    }
    $stdoutTask.GetAwaiter().GetResult() | Set-Content -LiteralPath $targetStdout
    $stderrTask.GetAwaiter().GetResult() | Set-Content -LiteralPath $targetStderr
    if ($targetProcess.ExitCode -ne 0) {
      throw "Python wire target failed for $mode (exit $($targetProcess.ExitCode)). See $targetStderr."
    }
    if ($null -ne $runnerError) {
      throw $runnerError
    }

    Invoke-Checked -FilePath $runnerExecutable -ArgumentList @(
      "validate-wire-results",
      "--plan", $executionPlan,
      "--results", $resultReport
    ) -WorkingDirectory $repositoryRoot

    $report = Get-Content -Raw -LiteralPath $resultReport | ConvertFrom-Json
    $results = @($report.results)
    $failed = @($results | Where-Object { $_.outcome -ne "passed" })
    if ($failed.Count -ne 0) {
      throw "$mode produced $($failed.Count) non-passing wire result(s)."
    }
    $summaries += [pscustomobject]@{
      mode = $mode
      passed = $results.Count
      results = $resultReport
    }
  } finally {
    if (-not $targetProcess.HasExited) {
      $targetProcess.Kill($true)
      $targetProcess.WaitForExit()
    }
    $targetProcess.Dispose()
  }
}

$totalPassed = ($summaries | Measure-Object -Property passed -Sum).Sum
if ($totalPassed -ne 6) {
  throw "Expected six Preview4 wire scenarios, got $totalPassed."
}

if ([string]::IsNullOrWhiteSpace($hostRouteTargetExecutable) -or -not (Test-Path -LiteralPath $hostRouteTargetExecutable)) {
  throw "Python host-route target executable not found: $hostRouteTargetExecutable"
}

$hostRouteProfiles = @(
  @{
    Name = "installed-native"
    Expected = 10
    Providers = @(
      @{ transport = "tcp"; provider_id = "nnrp.transport.tcp.native"; installed = $true; platforms = @("native"); security_modes = @("plain", "tls_server_auth") },
      @{ transport = "quic"; provider_id = "nnrp.transport.quic.native"; installed = $true; platforms = @("native"); security_modes = @("tls_server_auth") },
      @{ transport = "ipc"; provider_id = "nnrp.transport.ipc.native"; installed = $true; platforms = @("native"); security_modes = @("plain") },
      @{ transport = "websocket"; provider_id = "nnrp.transport.websocket.native"; installed = $true; platforms = @("native"); security_modes = @("plain", "wss") }
    )
  },
  @{
    Name = "known-uninstalled"
    Expected = 1
    Providers = @(
      @{ transport = "quic"; provider_id = "example.transport.quic.uninstalled"; installed = $false; platforms = @("native"); security_modes = @("tls_server_auth") }
    )
  }
)

foreach ($profile in $hostRouteProfiles) {
  $profileDirectory = Join-Path $artifactRoot "host-route-$($profile.Name)"
  if (Test-Path -LiteralPath $profileDirectory) {
    Remove-Item -LiteralPath $profileDirectory -Recurse -Force
  }
  New-Item -ItemType Directory -Force -Path $profileDirectory | Out-Null
  $targetManifest = Join-Path $profileDirectory "target.json"
  $executionPlan = Join-Path $profileDirectory "plan.json"
  $resultReport = Join-Path $profileDirectory "results.json"
  $evidenceDirectory = Join-Path $profileDirectory "evidence"

  $manifestArguments = @(
    "-m", "nnrp.tools.wire_conformance",
    "manifest",
    "--target-name", "nnrp-py-host-route-$($profile.Name)",
    "--mode", "suite_as_client",
    "--mode", "suite_as_server",
    "--capability", "host.routes"
  )
  foreach ($provider in $profile.Providers) {
    $manifestArguments += @("--host-route-provider", ($provider | ConvertTo-Json -Compress))
  }
  $manifestArguments += @("--output", $targetManifest)
  Invoke-Checked -FilePath $resolvedPythonExecutable -ArgumentList $manifestArguments -WorkingDirectory $repositoryRoot

  Invoke-Checked -FilePath $runnerExecutable -ArgumentList @(
    "wire-plan",
    "--suite", $suiteManifest,
    "--target", $targetManifest,
    "--output", $executionPlan,
    "--results-path", $resultReport,
    "--evidence-dir", $evidenceDirectory
  ) -WorkingDirectory $repositoryRoot

  Invoke-Checked -FilePath $runnerExecutable -ArgumentList @(
    "wire-run",
    "--plan", $executionPlan,
    "--target", $targetManifest,
    "--host-route-target", $hostRouteTargetExecutable,
    "--output", $resultReport
  ) -WorkingDirectory $repositoryRoot

  Invoke-Checked -FilePath $runnerExecutable -ArgumentList @(
    "validate-wire-results",
    "--plan", $executionPlan,
    "--results", $resultReport
  ) -WorkingDirectory $repositoryRoot

  $report = Get-Content -Raw -LiteralPath $resultReport | ConvertFrom-Json
  $results = @($report.results)
  $failed = @($results | Where-Object { $_.outcome -ne "passed" })
  if ($failed.Count -ne 0 -or $results.Count -ne $profile.Expected) {
    throw "$($profile.Name) expected $($profile.Expected) passing host-route scenarios; got $($results.Count) total and $($failed.Count) non-passing."
  }
  $summaries += [pscustomobject]@{
    mode = "host-route-$($profile.Name)"
    passed = $results.Count
    results = $resultReport
  }
}

$hostRoutePassed = (
  $summaries |
    Where-Object { $_.mode -like "host-route-*" } |
    Measure-Object -Property passed -Sum
).Sum
if ($hostRoutePassed -ne 11) {
  throw "Expected eleven Preview4 host-route scenarios, got $hostRoutePassed."
}
$summaries | ConvertTo-Json -Depth 4

$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install Docker Desktop first (see README.txt).' }
    docker info *> $null
    if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop in Linux containers mode first.' }
    docker compose version
    if ($LASTEXITCODE -ne 0) { throw 'Install the Docker Compose plugin.' }
    foreach ($line in Get-Content SHA256SUMS) {
        $parts = $line -split '  ', 2
        if ((Get-FileHash -Algorithm SHA256 $parts[1]).Hash.ToLower() -ne $parts[0]) {
            throw "Checksum mismatch: $($parts[1]). Download and extract the bundle again."
        }
    }
    $expected = (Get-Content architecture.txt).Trim()
    $actual = (docker info --format '{{.Architecture}}').Trim()
    if ($actual -eq 'x86_64') { $actual = 'amd64' }
    if ($actual -eq 'aarch64') { $actual = 'arm64' }
    if ($actual -ne $expected) { throw "This bundle is $expected; Docker is $actual. Download the matching bundle." }
    docker load -i waveraider.tar
    if ($LASTEXITCODE -ne 0) { throw 'Could not load the bundled image.' }
    Write-Host 'Open http://localhost:8899 after the server starts. Stop with Ctrl+C.'
    docker compose up --pull never
    if ($LASTEXITCODE -ne 0) { throw 'Startup failed. Read the Docker error above.' }
} catch {
    Write-Host $_ -ForegroundColor Red
    exit 1
}

param(
    [string]$Destination = "backups"
)

$ErrorActionPreference = "Stop"

function Invoke-DockerCompose {
    param([string[]]$Arguments)
    & docker compose @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker compose $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Invoke-Docker {
    param([string[]]$Arguments)
    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "docker $($Arguments -join ' ') failed with exit code $LASTEXITCODE."
    }
}

function Get-ComposeContainerId {
    param(
        [string]$Service,
        [switch]$Running
    )

    $arguments = @("compose", "ps")
    if ($Running) {
        $arguments += @("--status", "running")
    }
    $arguments += @("-q", $Service)
    $containerIds = & docker @arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Could not inspect Docker Compose service '$Service'."
    }
    if (-not $containerIds) {
        return ""
    }
    return ([string]$containerIds[0]).Trim()
}

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupPath = Join-Path (Resolve-Path ".").Path (Join-Path $Destination $timestamp)

$pausedServices = @("inngest", "ai-backend", "qdrant", "observability")
$runningServices = @()
foreach ($service in $pausedServices) {
    $containerId = Get-ComposeContainerId -Service $service -Running
    if ($containerId) {
        $runningServices += $service
    }
}
foreach ($requiredService in @("ai-backend", "qdrant", "observability")) {
    if (-not $runningServices.Contains($requiredService)) {
        throw "Start $requiredService with 'docker compose up -d' before backing up."
    }
}

$postgresId = Get-ComposeContainerId -Service "postgres" -Running
if (-not $postgresId) {
    throw "Start PostgreSQL with 'docker compose up -d postgres' before backing up."
}
$backendId = Get-ComposeContainerId -Service "ai-backend"
$qdrantId = Get-ComposeContainerId -Service "qdrant"
$observabilityId = Get-ComposeContainerId -Service "observability"
$gatewayId = Get-ComposeContainerId -Service "gateway"
New-Item -ItemType Directory -Path $backupPath -Force | Out-Null

try {
    Invoke-DockerCompose @(
        "exec", "-T", "postgres", "sh", "-c",
        'pg_dump --format=custom --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --file /tmp/omniretrive-identity.dump'
    )
    Invoke-Docker @(
        "cp", "${postgresId}:/tmp/omniretrive-identity.dump",
        (Join-Path $backupPath "identity.dump")
    )

    Invoke-DockerCompose (@("stop") + $runningServices)

    Invoke-Docker @(
        "cp", "${backendId}:/app/data/omniretrive.db",
        (Join-Path $backupPath "ai-metadata.db")
    )
    Invoke-Docker @(
        "cp", "${qdrantId}:/qdrant/storage",
        (Join-Path $backupPath "qdrant_storage")
    )
    Invoke-Docker @(
        "cp", "${observabilityId}:/app/data/omniretrive.db",
        (Join-Path $backupPath "observability.db")
    )

    if ($gatewayId) {
        & docker compose exec -T gateway sh -c 'test -f /legacy/gateway.db'
        if ($LASTEXITCODE -eq 0) {
            Invoke-Docker @(
                "cp", "${gatewayId}:/legacy/gateway.db",
                (Join-Path $backupPath "legacy-gateway.db")
            )
        }
    }

    $uploadsPath = Join-Path (Resolve-Path ".").Path "uploads"
    if (Test-Path $uploadsPath) {
        Copy-Item -LiteralPath $uploadsPath -Destination (Join-Path $backupPath "uploads") -Recurse
    } else {
        New-Item -ItemType Directory -Path (Join-Path $backupPath "uploads") | Out-Null
    }

    Write-Output "Backup created at $backupPath"
} finally {
    if ($runningServices.Count -gt 0) {
        Invoke-DockerCompose (@("up", "-d") + $runningServices)
    }
    if ($postgresId) {
        & docker compose exec -T postgres rm -f /tmp/omniretrive-identity.dump
    }
}

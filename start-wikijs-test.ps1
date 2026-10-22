$ErrorActionPreference = "Stop"

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker não foi encontrado. Instale e inicie o Docker Desktop antes de ativar o teste."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "O Docker Desktop não está em execução. Inicie-o e tente novamente."
}

$defaultRoute = Get-NetRoute -DestinationPrefix "0.0.0.0/0" |
    Sort-Object RouteMetric |
    Select-Object -First 1

if (-not $defaultRoute) {
    throw "Não foi possível encontrar uma interface de rede IPv4 ativa."
}

$machineIp = Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $defaultRoute.InterfaceIndex |
    Where-Object {
        $_.AddressState -eq "Preferred" -and
        $_.IPAddress -ne "127.0.0.1" -and
        -not $_.IPAddress.StartsWith("169.254.")
    } |
    Select-Object -First 1 -ExpandProperty IPAddress

if (-not $machineIp) {
    throw "Não foi possível encontrar o endereço IPv4 da interface de rede ativa."
}

$listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, 0)
$listener.Start()
$port = ([System.Net.IPEndPoint]$listener.LocalEndpoint).Port
$listener.Stop()

$env:WIKIJS_BIND_IP = $machineIp
$env:WIKIJS_PORT = [string]$port

Push-Location $PSScriptRoot
try {
    docker compose up -d
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível iniciar os serviços do Wiki.js com Docker Compose."
    }
}
finally {
    Pop-Location
}

$url = "http://${machineIp}:${port}"
$deadline = (Get-Date).AddMinutes(3)
$ready = $false

while ((Get-Date) -lt $deadline) {
    try {
        Invoke-WebRequest -Uri $url -UseBasicParsing -TimeoutSec 5 | Out-Null
        $ready = $true
        break
    }
    catch {
        Start-Sleep -Seconds 3
    }
}

if (-not $ready) {
    throw "O Wiki.js não respondeu em $url dentro de 3 minutos. Verifique os logs com 'docker compose logs wiki'."
}

Start-Process $url
Write-Host "Wiki.js aberto no navegador: $url"

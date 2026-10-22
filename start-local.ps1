$ErrorActionPreference = "Stop"

$dataDirectory = Join-Path $PSScriptRoot "data"
$serverInfoPath = Join-Path $dataDirectory "server.json"
$launcherThemePath = Join-Path $dataDirectory "launcher-theme.json"
$launcherSettingsPath = Join-Path $dataDirectory "launcher-settings.json"
$venvPython = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$mainScript = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "main.py"))
$script:pythonLauncher = $null
$script:launcherAppName = "Consat_Wiki"
$script:portOverride = if ([string]::IsNullOrWhiteSpace($env:WIKI_PORT)) { $null } else { $env:WIKI_PORT }

function Get-LauncherTheme {
    if (Test-Path $launcherThemePath) {
        try {
            $settings = Get-Content $launcherThemePath -Raw | ConvertFrom-Json
            if ($settings.theme -in @("light", "dark")) {
                return $settings.theme
            }
        }
        catch {
            Write-Host "A preferência de tema do launcher está inválida; será usado o tema claro."
        }
    }
    return "light"
}

function Set-LauncherTheme([string]$Theme) {
    if ($Theme -notin @("light", "dark")) {
        throw "Tema inválido."
    }

    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    @{ theme = $Theme } | ConvertTo-Json | Set-Content -Path $launcherThemePath -Encoding UTF8
    $Host.UI.RawUI.BackgroundColor = if ($Theme -eq "dark") { "Black" } else { "White" }
    $Host.UI.RawUI.ForegroundColor = if ($Theme -eq "dark") { "Gray" } else { "Black" }
    Clear-Host
}

function Test-ProjectFiles {
    $requiredFiles = @(
        "Consat_Wiki.cmd",
        "start-local.ps1",
        "main.py",
        "requirements.txt",
        "templates\admin_settings.html",
        "templates\base.html",
        "static\style.css",
        "static\app.js"
    )
    foreach ($relativePath in $requiredFiles) {
        $path = Join-Path $PSScriptRoot $relativePath
        if (-not (Test-Path $path -PathType Leaf) -or (Get-Item $path).Length -eq 0) {
            throw "Arquivo obrigatório ausente ou vazio: $relativePath. Restaure-o de uma cópia oficial/confiável do projeto."
        }
    }
    foreach ($directory in @("templates", "static")) {
        $files = Get-ChildItem (Join-Path $PSScriptRoot $directory) -File
        if (-not $files) {
            throw "A pasta obrigatória '$directory' está vazia. Restaure os arquivos do projeto de uma cópia oficial/confiável."
        }
        foreach ($file in $files) {
            if ($file.Length -eq 0) {
                throw "Arquivo do projeto vazio: $($file.FullName). Restaure-o de uma cópia oficial/confiável."
            }
        }
    }
}

function Install-OfficialPython {
    $winget = Get-Command winget.exe -ErrorAction SilentlyContinue
    if (-not $winget) {
        throw "Python 3.10+ não encontrado e WinGet indisponível. Instale Python somente pelo site oficial https://www.python.org/downloads/ e execute novamente."
    }

    Write-Host "Python ausente. Instalando Python 3.14 pelo repositório WinGet da Microsoft..."
    & $winget.Source install --id Python.Python.3.14 --exact --source winget `
        --accept-source-agreements --accept-package-agreements --silent --disable-interactivity
    if ($LASTEXITCODE -ne 0) {
        throw "A instalação oficial do Python falhou. Instale manualmente em https://www.python.org/downloads/ e execute novamente."
    }

    $pythonPaths = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python314\python.exe"),
        (Join-Path $env:ProgramFiles "Python314\python.exe")
    )
    foreach ($pythonPath in $pythonPaths) {
        if (Test-Path $pythonPath) {
            $script:pythonLauncher = $pythonPath
            break
        }
    }

    if (-not $script:pythonLauncher) {
        $launcher = Get-Command py.exe -ErrorAction SilentlyContinue
        if ($launcher) {
            $script:pythonLauncher = $launcher.Source
        }
    }
    if (-not $script:pythonLauncher) {
        throw "Python foi instalado, mas não foi localizado. Reinicie o Windows e execute novamente o launcher."
    }
}

function Repair-Dependencies {
    Write-Host "Reinstalando as dependências oficiais para reparar o ambiente..."
    & $venvPython -m pip install --disable-pip-version-check --force-reinstall --no-cache-dir --index-url "https://pypi.org/simple/" -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao reparar as dependências listadas em requirements.txt."
    }
    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "A verificação de integridade encontrou dependências Python incompatíveis mesmo após o reparo."
    }
    & $venvPython -c "import fastapi, uvicorn, jinja2, multipart"
    if ($LASTEXITCODE -ne 0) {
        throw "Uma ou mais dependências essenciais continuam indisponíveis após o reparo."
    }
}

function Get-ManagedServer {
    if (-not (Test-Path $serverInfoPath)) {
        return $null
    }

    $serverInfo = Get-Content $serverInfoPath -Raw | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId = $($serverInfo.pid)"
    if (-not $process) {
        Remove-Item $serverInfoPath -Force
        return $null
    }

    $processPath = [System.IO.Path]::GetFullPath($process.ExecutablePath)
    $expectedPath = [System.IO.Path]::GetFullPath($venvPython)
    if (
        $processPath -ine $expectedPath -or
        $process.CommandLine -notlike "*$mainScript*"
    ) {
        throw "O PID salvo em data\server.json pertence a outro processo. Por segurança, ele não será encerrado; verifique esse arquivo antes de continuar."
    }

    return $serverInfo
}

function Ensure-Environment {
    Test-ProjectFiles

    $pythonCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($pythonCommand) {
        $script:pythonLauncher = $pythonCommand.Source
        & $script:pythonLauncher -3 --version
        if ($LASTEXITCODE -ne 0) {
            $script:pythonLauncher = $null
        }
    }
    if (-not $script:pythonLauncher) {
        $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($pythonCommand) {
            $script:pythonLauncher = $pythonCommand.Source
            & $script:pythonLauncher --version
            if ($LASTEXITCODE -ne 0) {
                $script:pythonLauncher = $null
            }
        }
    }
    if (-not $script:pythonLauncher) {
        Install-OfficialPython
    }

    if (-not (Test-Path $venvPython)) {
        if ([System.IO.Path]::GetFileName($script:pythonLauncher) -like "py*") {
            & $script:pythonLauncher -3 -m venv (Join-Path $PSScriptRoot ".venv")
        }
        else {
            & $script:pythonLauncher -m venv (Join-Path $PSScriptRoot ".venv")
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Não foi possível criar o ambiente virtual .venv."
        }
    }

    $pythonVersion = & $venvPython -c "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    if ($LASTEXITCODE -ne 0 -or [version]$pythonVersion -lt [version]"3.10") {
        throw "O ambiente .venv precisa usar Python 3.10 ou superior. Remova somente a pasta .venv e tente novamente com Python atualizado."
    }

    & $venvPython -m pip install --disable-pip-version-check --index-url "https://pypi.org/simple/" -r (Join-Path $PSScriptRoot "requirements.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Falha ao instalar as dependências listadas em requirements.txt."
    }

    & $venvPython -m pip check
    if ($LASTEXITCODE -ne 0) {
        Repair-Dependencies
    }

    & $venvPython -c "import fastapi, uvicorn, jinja2, multipart"
    if ($LASTEXITCODE -ne 0) {
        Repair-Dependencies
    }

    & $venvPython $mainScript check
    if ($LASTEXITCODE -ne 0) {
        throw "A verificação de integridade do banco de dados falhou. Restaure uma cópia de backup antes de iniciar o servidor."
    }

    & $venvPython $mainScript create-admin
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível verificar ou criar a conta administradora."
    }
}

function Get-MachineIPv4 {
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

    return $machineIp
}

function Get-ConfiguredPort {
    if ($null -ne $script:portOverride) {
        $portText = $script:portOverride
    }
    else {
        $settings = Get-LauncherSettings
        $portText = if ($settings.ContainsKey("port")) { [string]$settings.port } else { "51600" }
    }

    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "A porta configurada precisa ser um número entre 1 e 65535."
    }
    return $port
}

function Get-LauncherSettings {
    if (-not (Test-Path $launcherSettingsPath)) {
        return @{}
    }
    try {
        $loaded = Get-Content $launcherSettingsPath -Raw | ConvertFrom-Json
        $settings = @{}
        foreach ($property in $loaded.PSObject.Properties) {
            $settings[$property.Name] = $property.Value
        }
        return $settings
    }
    catch {
        throw "A configuração do launcher em data\launcher-settings.json está inválida: $($_.Exception.Message)"
    }
}

function Get-ApplicationBrand {
    $brandJson = & $venvPython $mainScript app-brand
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível carregar a identidade configurada do aplicativo."
    }
    try {
        $brand = ($brandJson | Out-String).Trim() | ConvertFrom-Json
    }
    catch {
        throw "A identidade do aplicativo retornou dados inválidos."
    }
    if ([string]::IsNullOrWhiteSpace($brand.name)) {
        throw "O nome configurado do aplicativo está vazio."
    }
    return $brand
}

function Set-DesktopShortcutIcon($Shortcut, [int]$BrandVersion, [bool]$HasLogo) {
    $iconPath = Join-Path $dataDirectory "launcher-app-$BrandVersion.ico"
    if (-not $HasLogo) {
        if (Test-Path $iconPath -PathType Leaf) {
            Remove-Item -LiteralPath $iconPath -Force
        }
        $Shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll, 220"
        return
    }

    $logoBase64 = & $venvPython $mainScript app-logo
    if ($LASTEXITCODE -ne 0) {
        throw "Não foi possível carregar o logo para o ícone do atalho."
    }
    $logoBytes = [Convert]::FromBase64String(($logoBase64 | Out-String).Trim())

    if (Test-Path $iconPath -PathType Leaf) {
        Remove-Item -LiteralPath $iconPath -Force
    }
    Add-Type -AssemblyName System.Drawing
    if (-not ("WikiIconNative" -as [type])) {
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class WikiIconNative {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool DestroyIcon(IntPtr handle);
}
"@
    }

    $image = $null
    $imageStream = $null
    $bitmap = $null
    $graphics = $null
    $icon = $null
    $stream = $null
    $iconHandle = [IntPtr]::Zero
    try {
        $imageStream = [System.IO.MemoryStream]::new($logoBytes)
        $image = [System.Drawing.Image]::FromStream($imageStream)
        $bitmap = New-Object System.Drawing.Bitmap 64, 64
        $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
        $graphics.Clear([System.Drawing.Color]::Transparent)
        $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
        $scale = [Math]::Min(64.0 / $image.Width, 64.0 / $image.Height)
        $width = [int][Math]::Round($image.Width * $scale)
        $height = [int][Math]::Round($image.Height * $scale)
        $graphics.DrawImage($image, [int]((64 - $width) / 2), [int]((64 - $height) / 2), $width, $height)
        $iconHandle = $bitmap.GetHicon()
        $icon = [System.Drawing.Icon]::FromHandle($iconHandle)
        $stream = [System.IO.File]::Create($iconPath)
        $icon.Save($stream)
    }
    finally {
        if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $icon) { $icon.Dispose() }
        if ($iconHandle -ne [IntPtr]::Zero) { [WikiIconNative]::DestroyIcon($iconHandle) | Out-Null }
        if ($null -ne $graphics) { $graphics.Dispose() }
        if ($null -ne $bitmap) { $bitmap.Dispose() }
        if ($null -ne $image) { $image.Dispose() }
        if ($null -ne $imageStream) { $imageStream.Dispose() }
    }
    $Shortcut.IconLocation = $iconPath
}

function Ensure-DesktopShortcut([string]$AppName, [int]$BrandVersion, [bool]$HasLogo) {
    $desktop = [Environment]::GetFolderPath("Desktop")
    if ([string]::IsNullOrWhiteSpace($desktop)) {
        throw "Não foi possível localizar a Área de Trabalho para criar o atalho."
    }
    $safeName = $AppName -replace '[<>:"/\\|?*\x00-\x1f]', "_"
    $safeName = $safeName.Trim().TrimEnd(".")
    if ([string]::IsNullOrWhiteSpace($safeName)) {
        $safeName = "Wiki"
    }
    if ($safeName.Length -gt 80) {
        $safeName = $safeName.Substring(0, 80).TrimEnd(".")
    }

    $settings = Get-LauncherSettings
    $previousIconPath = if ($settings.ContainsKey("iconPath")) { [string]$settings.iconPath } else { $null }
    $shortcutPath = Join-Path $desktop "$safeName.lnk"
    $targetPath = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot "Consat_Wiki.cmd"))
    $shell = New-Object -ComObject WScript.Shell
    if (
        $settings.ContainsKey("shortcutPath") -and
        $settings.ContainsKey("brandVersion") -and
        $settings.brandVersion -eq $BrandVersion -and
        $settings.appName -ceq $AppName -and
        (Test-Path $settings.shortcutPath -PathType Leaf)
    ) {
        $currentShortcut = $shell.CreateShortcut([string]$settings.shortcutPath)
        if (
            [System.IO.Path]::GetFullPath($currentShortcut.TargetPath) -ieq $targetPath -and
            [System.IO.Path]::GetFullPath([string]$settings.shortcutPath) -ieq [System.IO.Path]::GetFullPath($shortcutPath)
        ) {
            return
        }
    }

    if (
        $settings.ContainsKey("shortcutPath") -and
        $settings.shortcutPath -ine $shortcutPath -and
        (Test-Path $settings.shortcutPath -PathType Leaf)
    ) {
        $previousPath = [System.IO.Path]::GetFullPath([string]$settings.shortcutPath)
        $previousShortcut = $shell.CreateShortcut($previousPath)
        if (
            [System.IO.Path]::GetFullPath($previousShortcut.TargetPath) -ieq $targetPath -and
            [System.IO.Path]::GetDirectoryName($previousPath) -ieq [System.IO.Path]::GetFullPath($desktop)
        ) {
            Remove-Item -LiteralPath $previousPath -Force
        }
    }

    if (Test-Path $shortcutPath -PathType Leaf) {
        $existingShortcut = $shell.CreateShortcut($shortcutPath)
        if ([System.IO.Path]::GetFullPath($existingShortcut.TargetPath) -ine $targetPath) {
            throw "Já existe outro atalho chamado '$safeName' na Área de Trabalho. Ele não será substituído."
        }
    }

    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $targetPath
    $shortcut.WorkingDirectory = $PSScriptRoot
    $shortcut.Description = "$AppName - Launcher local"
    try {
        Set-DesktopShortcutIcon $shortcut $BrandVersion $HasLogo
    }
    catch {
        Write-Host "Aviso: não foi possível converter o logo para o ícone do atalho: $($_.Exception.Message)" -ForegroundColor Yellow
        $shortcut.IconLocation = "$env:SystemRoot\System32\shell32.dll, 220"
    }
    $shortcut.Save()

    $settings["appName"] = $AppName
    $settings["brandVersion"] = $BrandVersion
    $settings["shortcutPath"] = $shortcutPath
    $settings["iconPath"] = if ($HasLogo) {
        Join-Path $dataDirectory "launcher-app-$BrandVersion.ico"
    }
    else {
        $null
    }
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $settings | ConvertTo-Json | Set-Content -Path $launcherSettingsPath -Encoding UTF8
    if (
        $previousIconPath -and
        $previousIconPath -ine $settings.iconPath -and
        (Test-Path $previousIconPath -PathType Leaf) -and
        [System.IO.Path]::GetDirectoryName([System.IO.Path]::GetFullPath($previousIconPath)) -ieq [System.IO.Path]::GetFullPath($dataDirectory) -and
        [System.IO.Path]::GetFileName($previousIconPath) -match '^launcher-app-\d+\.ico$'
    ) {
        Remove-Item -LiteralPath $previousIconPath -Force
    }
}

function Set-LauncherWindowTitle {
    $brand = Get-ApplicationBrand
    $script:launcherAppName = $brand.name
    try {
        $Host.UI.RawUI.WindowTitle = "$script:launcherAppName - Launcher"
    }
    catch {
        Write-Host "Este terminal não permite atualizar o título da janela."
    }
    try {
        Ensure-DesktopShortcut $brand.name $brand.version $brand.has_logo
    }
    catch {
        Write-Host "Aviso: não foi possível atualizar o atalho na Área de Trabalho: $($_.Exception.Message)" -ForegroundColor Yellow
    }
}

function Set-WikiPort {
    $portText = Read-Host "Nova porta HTTP (1-65535)"
    $port = 0
    if (-not [int]::TryParse($portText, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "Informe uma porta válida entre 1 e 65535."
    }

    $settings = Get-LauncherSettings
    $settings["port"] = $port
    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $settings | ConvertTo-Json | Set-Content -Path $launcherSettingsPath -Encoding UTF8
    $script:portOverride = $null
    Write-Host "Porta salva: $port"

    if (Get-ManagedServer) {
        Stop-WikiServer
        Start-WikiServer
    }
}

function Start-WikiServer {
    Ensure-Environment
    $brand = Get-ApplicationBrand
    $script:launcherAppName = $brand.name
    try {
        Ensure-DesktopShortcut $brand.name $brand.version $brand.has_logo
    }
    catch {
        Write-Host "Aviso: não foi possível atualizar o atalho na Área de Trabalho: $($_.Exception.Message)" -ForegroundColor Yellow
    }

    $existingServer = Get-ManagedServer
    if ($existingServer) {
        Write-Host "O servidor já está ativo: $($existingServer.url)"
        return
    }

    $machineIp = Get-MachineIPv4
    $port = Get-ConfiguredPort
    $listener = [System.Net.Sockets.TcpListener]::new(
        [System.Net.IPAddress]::Parse($machineIp),
        $port
    )
    try {
        $listener.Start()
    }
    catch {
        throw "A porta $port já está ocupada ou indisponível em $machineIp. Libere-a ou escolha outra porta pelo menu."
    }
    finally {
        $listener.Stop()
    }

    $env:WIKI_HOST = $machineIp
    $previousPort = $env:WIKI_PORT
    $env:WIKI_PORT = [string]$port

    New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null
    $stdoutLog = Join-Path $dataDirectory "app.log"
    $stderrLog = Join-Path $dataDirectory "app-error.log"
    try {
        $server = Start-Process `
            -FilePath $venvPython `
            -ArgumentList "`"$mainScript`"" `
            -WorkingDirectory $PSScriptRoot `
            -PassThru `
            -RedirectStandardOutput $stdoutLog `
            -RedirectStandardError $stderrLog
    }
    finally {
        if ($null -eq $previousPort) {
            Remove-Item Env:WIKI_PORT -ErrorAction SilentlyContinue
        }
        else {
            $env:WIKI_PORT = $previousPort
        }
    }

    $serverInfo = [ordered]@{
        pid       = $server.Id
        host      = $machineIp
        port      = $port
        url       = "http://${machineIp}:${port}"
        startedAt = (Get-Date).ToString("o")
    }
    $serverInfo | ConvertTo-Json | Set-Content -Path $serverInfoPath -Encoding UTF8

    $deadline = (Get-Date).AddMinutes(2)
    $ready = $false
    while ((Get-Date) -lt $deadline) {
        if ($server.HasExited) {
            break
        }
        try {
            Invoke-WebRequest -Uri $serverInfo.url -UseBasicParsing -TimeoutSec 3 | Out-Null
            $ready = $true
            break
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }

    if (-not $ready) {
        if (-not $server.HasExited) {
            Stop-Process -Id $server.Id
        }
        Remove-Item $serverInfoPath -Force -ErrorAction SilentlyContinue
        $errorDetails = if (Test-Path $stderrLog) {
            (Get-Content $stderrLog -Tail 20) -join [Environment]::NewLine
        }
        else {
            "Nenhum detalhe de erro foi registrado."
        }
        throw "A aplicação não iniciou. Consulte data\app-error.log. $errorDetails"
    }

    Start-Process $serverInfo.url
    Write-Host "Servidor ativo: $($serverInfo.url)"
}

function Stop-WikiServer {
    $serverInfo = Get-ManagedServer
    if (-not $serverInfo) {
        Write-Host "O servidor já está parado."
        return
    }

    Stop-Process -Id $serverInfo.pid
    $deadline = (Get-Date).AddSeconds(20)
    while ((Get-Date) -lt $deadline) {
        $process = Get-Process -Id $serverInfo.pid -ErrorAction SilentlyContinue
        if (-not $process) {
            break
        }
        Start-Sleep -Milliseconds 250
    }

    if (Get-Process -Id $serverInfo.pid -ErrorAction SilentlyContinue) {
        throw "O servidor não encerrou no prazo esperado. O PID continua salvo em data\server.json."
    }

    Remove-Item $serverInfoPath -Force
    Write-Host "Servidor encerrado."
}

function Show-WikiServerStatus {
    $serverInfo = Get-ManagedServer
    if ($serverInfo) {
        Write-Host "Estado: ATIVO"
        Write-Host "Endereço: $($serverInfo.url)"
        Write-Host "PID: $($serverInfo.pid)"
        Write-Host "Iniciado em: $($serverInfo.startedAt)"
    }
    else {
        Write-Host "Estado: PARADO"
    }
}

function Invoke-LauncherAction([scriptblock]$Action) {
    try {
        & $Action
    }
    catch {
        Write-Host "Erro: $($_.Exception.Message)" -ForegroundColor Red
    }
}

Invoke-LauncherAction { Start-WikiServer }

$exitLauncher = $false
while (-not $exitLauncher) {
    $currentTheme = Get-LauncherTheme
    Invoke-LauncherAction { Set-LauncherWindowTitle }
    try {
        $Host.UI.RawUI.BackgroundColor = if ($currentTheme -eq "dark") { "Black" } else { "White" }
        $Host.UI.RawUI.ForegroundColor = if ($currentTheme -eq "dark") { "Gray" } else { "Black" }
    }
    catch {
        Write-Host "Este terminal não permite trocar as cores do launcher."
    }

    Write-Host ""
    Write-Host "========== $script:launcherAppName =========="
    Write-Host "Tema do launcher: $currentTheme"
    Show-WikiServerStatus
    Write-Host ""
    Write-Host "[1] Iniciar servidor"
    Write-Host "[2] Parar servidor"
    Write-Host "[3] Reiniciar servidor"
    Write-Host "[4] Abrir no navegador"
    Write-Host "[5] Alternar tema claro/escuro"
    Write-Host "[6] Alterar porta do servidor"
    Write-Host "[7] Sair (encerra o servidor)"
    $choice = Read-Host "Escolha uma opção"

    switch ($choice) {
        "1" {
            Invoke-LauncherAction { Start-WikiServer }
        }
        "2" {
            Invoke-LauncherAction { Stop-WikiServer }
        }
        "3" {
            Invoke-LauncherAction {
                Stop-WikiServer
                Start-WikiServer
            }
        }
        "4" {
            Invoke-LauncherAction {
                $serverInfo = Get-ManagedServer
                if (-not $serverInfo) {
                    throw "O servidor está parado. Escolha Iniciar servidor primeiro."
                }
                Start-Process $serverInfo.url
                Write-Host "Página aberta: $($serverInfo.url)"
            }
        }
        "5" {
            Invoke-LauncherAction {
                $nextTheme = if ((Get-LauncherTheme) -eq "dark") { "light" } else { "dark" }
                Set-LauncherTheme $nextTheme
                Write-Host "Tema do launcher alterado para $nextTheme."
            }
        }
        "6" {
            Invoke-LauncherAction { Set-WikiPort }
        }
        "7" {
            Invoke-LauncherAction { Stop-WikiServer }
            $exitLauncher = $true
        }
        default {
            Write-Host "Opção inválida."
        }
    }
}

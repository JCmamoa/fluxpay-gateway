<#
.SYNOPSIS
    Sobe o FluxPay nativamente no Windows com um único comando.

.DESCRIPTION
    - Encontra um Python >= 3.11 (py launcher, PATH ou instalação padrão).
    - Cria o virtualenv .venv na primeira execução.
    - Instala as dependências de requirements.txt.
    - Copia .env.example para .env se ainda não existir.
    - Executa a API + worker + dispatcher em um único processo (python -m app).

.EXAMPLE
    .\run.ps1

.EXAMPLE
    .\run.ps1 -Port 8080

.EXAMPLE
    .\run.ps1 -SkipInstall
#>
[CmdletBinding()]
param(
    [ValidateRange(0, 65535)]
    [int]$Port = 0,

    [switch]$SkipInstall,

    [switch]$Reinstall
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Test-PythonCandidate {
    param(
        [string]$File,
        [string[]]$PrefixArgs = @()
    )

    try {
        $version = & $File @PrefixArgs -c "import sys; print('%d.%d.%d' % sys.version_info[:3])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $version) { return $null }
        $version = ($version | Select-Object -First 1).Trim()
        if ($version -match '^(\d+)\.(\d+)\.(\d+)$') {
            $major = [int]$Matches[1]
            $minor = [int]$Matches[2]
            if ($major -eq 3 -and $minor -ge 11) {
                return $version
            }
        }
    } catch {
        # Not a usable interpreter (e.g. the Microsoft Store app alias).
    }
    return $null
}

function Resolve-Python {
    $candidates = @(
        @{ File = "py";      Args = @("-3.12") },
        @{ File = "py";      Args = @("-3.11") },
        @{ File = "py";      Args = @("-3") },
        @{ File = "python";  Args = @() },
        @{ File = "python3"; Args = @() }
    )

    foreach ($candidate in $candidates) {
        if (-not (Get-Command $candidate.File -ErrorAction SilentlyContinue)) { continue }
        $version = Test-PythonCandidate -File $candidate.File -PrefixArgs $candidate.Args
        if ($version) {
            return [pscustomobject]@{
                File    = (Get-Command $candidate.File).Source
                Args    = $candidate.Args
                Version = $version
            }
        }
    }

    # Common per-user and machine-wide install locations (including the one used
    # by `winget install Python.Python.3.12`).
    $patterns = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python3*\python.exe"),
        (Join-Path $env:ProgramFiles "Python3*\python.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Python3*\python.exe"),
        "C:\Python3*\python.exe"
    )
    $found = foreach ($pattern in $patterns) {
        Get-ChildItem -Path $pattern -ErrorAction SilentlyContinue
    }
    foreach ($entry in ($found | Sort-Object FullName -Descending)) {
        $version = Test-PythonCandidate -File $entry.FullName
        if ($version) {
            return [pscustomobject]@{
                File    = $entry.FullName
                Args    = @()
                Version = $version
            }
        }
    }

    return $null
}

Write-Host ""
Write-Host "  FluxPay - Gateway de Pagamento (demo)" -ForegroundColor Cyan
Write-Host "  ------------------------------------" -ForegroundColor DarkGray

$venvPython = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    $python = Resolve-Python
    if (-not $python) {
        Write-Host ""
        Write-Host "Python 3.11+ nao encontrado." -ForegroundColor Red
        Write-Host "Instale com:  winget install -e --id Python.Python.3.12" -ForegroundColor Yellow
        Write-Host "Depois abra um novo terminal e rode .\run.ps1 novamente." -ForegroundColor Yellow
        exit 1
    }

    Write-Host ("  Python {0} encontrado: {1}" -f $python.Version, $python.File) -ForegroundColor DarkGray
    Write-Host "  Criando virtualenv .venv ..." -ForegroundColor DarkGray
    & $python.File @($python.Args) -m venv .venv
    if ($LASTEXITCODE -ne 0) {
        Write-Host "Falha ao criar o virtualenv." -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

if (-not $SkipInstall) {
    $marker = Join-Path $root ".venv\.deps-installed"
    $requirements = Join-Path $root "requirements.txt"
    $needsInstall = $Reinstall -or -not (Test-Path $marker)
    if (-not $needsInstall) {
        $needsInstall = (Get-Item $requirements).LastWriteTimeUtc -gt (Get-Item $marker).LastWriteTimeUtc
    }
    if ($needsInstall) {
        Write-Host "  Instalando dependencias ..." -ForegroundColor DarkGray
        & $venvPython -m pip install --disable-pip-version-check --quiet -r $requirements
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Falha ao instalar as dependencias." -ForegroundColor Red
            exit $LASTEXITCODE
        }
        New-Item -ItemType File -Path $marker -Force | Out-Null
    }
}

if (-not (Test-Path (Join-Path $root ".env"))) {
    Copy-Item (Join-Path $root ".env.example") (Join-Path $root ".env")
    Write-Host "  .env criado a partir de .env.example" -ForegroundColor DarkGray
}

if ($Port -gt 0) {
    $env:PORT = [string]$Port
    $env:WEBHOOK_URL = "http://127.0.0.1:$Port/demo/webhook-receiver?endpoint=whe_demo"
}

$effectivePort = if ($env:PORT) { $env:PORT } else { "8000" }
Write-Host ""
Write-Host ("  Console:  http://localhost:{0}/" -f $effectivePort) -ForegroundColor Green
Write-Host ("  Docs:     http://localhost:{0}/docs" -f $effectivePort) -ForegroundColor Green
Write-Host ("  Health:   http://localhost:{0}/health" -f $effectivePort) -ForegroundColor Green
Write-Host "  API key:  sk_demo_fluxpay_2026" -ForegroundColor DarkGray
Write-Host "  Ctrl+C para encerrar." -ForegroundColor DarkGray
Write-Host ""

& $venvPython -m app

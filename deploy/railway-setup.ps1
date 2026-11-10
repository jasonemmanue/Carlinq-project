# Carlinq - mise en place du projet Railway (a lancer une fois, relancable sans doublon).
#
# Prerequis : Railway CLI (railway --version), `railway login`, projet cree sur Railway
# puis `railway link` depuis la racine du monorepo.
#
#   powershell -ExecutionPolicy Bypass -File deploy\railway-setup.ps1
#   powershell -ExecutionPolicy Bypass -File deploy\railway-setup.ps1 -NoSeed -NoDeploy
#
# Cree (si absents) : Postgres, Redis, api (API/), backoffice (Backoffice/),
# pose les variables, genere les domaines publics puis deploie les deux services.

param(
    [string]$Environment = "production",
    [switch]$NoSeed,     # ne pas charger les comptes de demo (SEED_DEMO_DATA=false)
    [switch]$NoDeploy    # tout configurer sans lancer `railway up`
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Rw {
    # Appel du CLI Railway avec arret sur erreur.
    & railway @args
    if ($LASTEXITCODE -ne 0) { throw "railway $($args -join ' ') a echoue (code $LASTEXITCODE)" }
}

function New-Secret([int]$Bytes = 32) {
    $buf = New-Object byte[] $Bytes
    [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
    return ([Convert]::ToBase64String($buf) -replace '[+/=]', '')
}

function Get-ServiceVars([string]$Service) {
    $ErrorActionPreference = "Continue"  # PS 5.1 : stderr natif redirige != erreur fatale
    $out = & railway variable list --service $Service --environment $Environment --kv 2>$null
    if ($LASTEXITCODE -ne 0) { return @{} }
    $vars = @{}
    foreach ($line in $out) {
        $i = $line.IndexOf("=")
        if ($i -gt 0) { $vars[$line.Substring(0, $i)] = $line.Substring($i + 1) }
    }
    return $vars
}

function Ensure-Domain([string]$Service, [int]$Port) {
    $ErrorActionPreference = "Continue"
    $list = (& railway domain list --service $Service --environment $Environment 2>$null) -join "`n"
    if ($list -notmatch "railway\.app") {
        Rw domain --service $Service --environment $Environment --port $Port
    } else {
        Write-Host "  domaine deja present pour $Service"
    }
}

# ------------------------------------------------------------------ verifications
if (-not (Get-Command railway -ErrorAction SilentlyContinue)) {
    throw "Railway CLI introuvable : npm i -g @railway/cli (ou scoop install railway)"
}
& railway whoami | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Non connecte : lance d'abord ``railway login``" }
& railway status | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Dossier non lie a un projet : lance ``railway link`` depuis $Root" }

Write-Host "`n== Services ==" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
$services = (& railway service list --environment $Environment --json 2>$null) -join "`n"
$ErrorActionPreference = "Stop"
function Has-Service([string]$Name) { return $services -match ('"name"\s*:\s*"' + [regex]::Escape($Name) + '"') }

if (-not (Has-Service "Postgres")) { Rw add --database postgres } else { Write-Host "  Postgres present" }
if (-not (Has-Service "Redis"))    { Rw add --database redis }    else { Write-Host "  Redis present" }
if (-not (Has-Service "api"))        { Rw add --service api }        else { Write-Host "  api present" }
if (-not (Has-Service "backoffice")) { Rw add --service backoffice } else { Write-Host "  backoffice present" }

# ------------------------------------------------------------------ variables API
Write-Host "`n== Variables api ==" -ForegroundColor Cyan
$apiVars = Get-ServiceVars "api"
$secret = if ($apiVars["SECRET_KEY"]) { $apiVars["SECRET_KEY"] } else { New-Secret 48 }
$seed = if ($NoSeed) { "false" } else { "true" }
$demoPassword = if ($apiVars["SEED_DEMO_PASSWORD"]) { $apiVars["SEED_DEMO_PASSWORD"] } else { New-Secret 18 }

# Les references ${{...}} sont resolues par Railway (guillemets simples : pas d'expansion PowerShell).
Rw variable set --service api --environment $Environment --skip-deploys `
    'DATABASE_URL=${{Postgres.DATABASE_URL}}' `
    'REDIS_URL=${{Redis.REDIS_URL}}' `
    "SECRET_KEY=$secret" `
    "SEED_DEMO_DATA=$seed" `
    "SEED_DEMO_PASSWORD=$demoPassword" `
    "PAYMENTS_SIMULATED=false" `
    "INSTALL_DEV=false" `
    "APP_ENV=staging" `
    "PORT=8000" `
    "TIMEZONE=Africa/Douala" `
    'CORS_ORIGINS=https://${{backoffice.RAILWAY_PUBLIC_DOMAIN}}' `
    "ACCESS_TOKEN_EXPIRE_MINUTES=60" `
    "REFRESH_TOKEN_EXPIRE_DAYS=30" `
    "CACHE_TTL_SECONDS=300"

# ------------------------------------------------------------------ variables backoffice
Write-Host "`n== Variables backoffice ==" -ForegroundColor Cyan
$boVars = Get-ServiceVars "backoffice"
$boPassword = if ($boVars["BACKOFFICE_BASIC_AUTH_PASSWORD"]) { $boVars["BACKOFFICE_BASIC_AUTH_PASSWORD"] } else { New-Secret 18 }

# Le backoffice appelle l'API cote serveur via son domaine public.
# CARLINQ_ADMIN_* : compte admin de l'amorcage (app/seed.py) ; a changer si SEED_DEMO_DATA=false.
Rw variable set --service backoffice --environment $Environment --skip-deploys `
    'CARLINQ_API_URL=https://${{api.RAILWAY_PUBLIC_DOMAIN}}' `
    "CARLINQ_ADMIN_PHONE=+237600000000" `
    "CARLINQ_ADMIN_PASSWORD=$demoPassword" `
    "BACKOFFICE_BASIC_AUTH_USER=admin" `
    "BACKOFFICE_BASIC_AUTH_PASSWORD=$boPassword" `
    "NEXT_PUBLIC_APP_ENV=staging" `
    "PORT=3000"

# ------------------------------------------------------------------ domaines publics
Write-Host "`n== Domaines ==" -ForegroundColor Cyan
Ensure-Domain "api" 8000
Ensure-Domain "backoffice" 3000

# ------------------------------------------------------------------ stockage
Write-Host "`n== Volume api (/data) ==" -ForegroundColor Cyan
$ErrorActionPreference = "Continue"
# `railway volume` agit sur le service lie au dossier.
Rw service link api
$volumes = (& railway volume list 2>$null) -join "`n"
if ($volumes -notmatch "Attached to: api") { Rw volume add --mount-path /data } else { Write-Host "  volume deja present" }
$ErrorActionPreference = "Stop"

# ------------------------------------------------------------------ deploiement
if (-not $NoDeploy) {
    & (Join-Path $PSScriptRoot "railway-deploy.ps1") -Environment $Environment
}

Write-Host "`n== Termine ==" -ForegroundColor Green
Write-Host "Domaines :"
& railway domain list --service api --environment $Environment
& railway domain list --service backoffice --environment $Environment
Write-Host "Backoffice : utilisateur 'admin', mot de passe : railway variable list --service backoffice --kv"
Write-Host "Apps Flutter : --dart-define=CARLINQ_API_URL=https://<domaine api> (ou ecran Serveur de la connexion)"

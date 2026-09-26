param(
    [ValidateRange(1, 65535)]
    [int]$Port = 8764
)

$ErrorActionPreference = "Stop"
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$BaseUrl = "http://127.0.0.1:$Port"

function Test-LocalPort {
    param([int]$TargetPort)

    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $Pending = $Client.BeginConnect("127.0.0.1", $TargetPort, $null, $null)
        if (-not $Pending.AsyncWaitHandle.WaitOne(400)) {
            return $false
        }
        $Client.EndConnect($Pending)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $Client.Close()
    }
}

function Get-PortOwnerSummary {
    param([int]$TargetPort)

    try {
        $Connection = Get-NetTCPConnection -State Listen -LocalPort $TargetPort -ErrorAction Stop |
            Select-Object -First 1
        $Process = Get-CimInstance Win32_Process -Filter "ProcessId=$($Connection.OwningProcess)" -ErrorAction Stop
        return "PID $($Process.ProcessId) ($($Process.Name)): $($Process.CommandLine)"
    }
    catch {
        return "無法取得占用程序資訊"
    }
}

if (Test-LocalPort -TargetPort $Port) {
    $IsDemoService = $false
    try {
        $Health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -TimeoutSec 2
        $IsDemoService = $Health.service -eq "tainan-bias-demo" -and $Health.status -eq "ok"
    }
    catch {
        $IsDemoService = $false
    }

    if ($IsDemoService) {
        Write-Host "展示頁已在執行：$BaseUrl" -ForegroundColor Green
        exit 0
    }

    $Owner = Get-PortOwnerSummary -TargetPort $Port
    [Console]::Error.WriteLine("連接埠 $Port 已被其他程序占用。$Owner")
    exit 2
}

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    [Console]::Error.WriteLine("找不到 uv。請先安裝 uv：https://docs.astral.sh/uv/")
    exit 3
}

Write-Host "[1/2] 建立或更新獨立環境..." -ForegroundColor Cyan
& uv sync --project $ProjectRoot --extra test
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "[2/2] 啟動展示頁：$BaseUrl" -ForegroundColor Green
& uv run --project $ProjectRoot tainan-demo serve --host 127.0.0.1 --port $Port
exit $LASTEXITCODE

param(
    # 固定使用项目专属端口，避免与常见的 8080 / 8012 开发服务冲突。
    [int]$Port = 18080
)

$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

# 兼容旧命令：实际唯一入口是项目根目录的 preview_server.py。
# 这个脚本不再直接拼接 Uvicorn 参数，避免和 Python 入口出现两套不同启动逻辑。
$env:PREVIEW_SERVER_PORT = "$Port"
$env:PREVIEW_SERVER_RELOAD = "true"
& .\.venv\Scripts\python.exe .\preview_server.py
exit $LASTEXITCODE

param([string]$GenesisPython='D:\xwchen\genesis-garment-demo\.venv\Scripts\python.exe')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath $GenesisPython)) { throw "找不到已有 Genesis 环境：$GenesisPython；请通过 -GenesisPython 指定解释器。" }
& $GenesisPython "$PSScriptRoot\run.py"

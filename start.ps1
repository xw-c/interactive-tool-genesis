param([string]$GenesisPython=(Join-Path $PSScriptRoot '.venv\Scripts\python.exe'), [string]$UipcPackageDir='')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (!(Test-Path -LiteralPath $GenesisPython -PathType Leaf)) {
    throw "找不到 Python 环境：$GenesisPython。请按 README.md 创建项目 .venv，或通过 -GenesisPython 指定已有 Genesis 环境的解释器。"
}
$previousPythonPath = $env:PYTHONPATH
$previousUipcPackage = $env:INTERACTIVE_IPC_UIPC_PACKAGE
try {
    if ($UipcPackageDir) {
        $UipcPackageDir = (Resolve-Path -LiteralPath $UipcPackageDir).Path
        if (!(Test-Path -LiteralPath (Join-Path $UipcPackageDir 'uipc\__init__.py'))) { throw 'UipcPackageDir 必须是包含 uipc 包的目录。' }
        $env:INTERACTIVE_IPC_UIPC_PACKAGE = $UipcPackageDir
        $env:PYTHONPATH = $UipcPackageDir + [IO.Path]::PathSeparator + $env:PYTHONPATH
    }
    & $GenesisPython (Join-Path $PSScriptRoot 'run.py')
} finally {
    $env:PYTHONPATH = $previousPythonPath
    $env:INTERACTIVE_IPC_UIPC_PACKAGE = $previousUipcPackage
}

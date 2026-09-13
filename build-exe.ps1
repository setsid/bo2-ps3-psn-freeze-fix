# Builds dist\bo2-psn-fix.exe.
#
# Requires Python 3 and PyInstaller:  pip install pyinstaller
# Run from anywhere:                  .\build-exe.ps1

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Definition
Push-Location $root

try {
    # scetool and the patcher are both loaded from disk at run time, so a
    # build missing either produces an exe that fails once the user presses Go
    # rather than one that fails to build.
    $required = @(
        "bo2-gui.py",
        "patch-bo2.py",
        "tools\scetool\scetool.exe",
        "tools\scetool\data\keys"
    )
    foreach ($item in $required) {
        if (-not (Test-Path -LiteralPath $item)) {
            throw "Missing $item. The build needs the whole tools\scetool folder, keys included."
        }
    }

    # --icon puts it on the exe itself; --add-data is what the running window
    # loads for its title bar, and it needs the file at run time either way.
    $arguments = @(
        "--onefile", "--windowed", "--name", "bo2-psn-fix",
        "--add-data", "patch-bo2.py;.",
        "--add-data", "tools\scetool;tools/scetool"
    )
    foreach ($icon in @("icon.ico", "icon.png")) {
        if (Test-Path -LiteralPath $icon) {
            $arguments += @("--add-data", "$icon;.")
        }
    }
    if (Test-Path -LiteralPath "icon.ico") {
        $arguments += @("--icon", "icon.ico")
    }
    pyinstaller @arguments bo2-gui.py

    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited $LASTEXITCODE."
    }

    Write-Host ""
    Write-Host "Built dist\bo2-psn-fix.exe"
    Write-Host "Run it from a local drive. scetool cannot run from a UNC path."
}
finally {
    Pop-Location
}

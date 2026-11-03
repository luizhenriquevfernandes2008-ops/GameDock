# Instala o build de dist\GameDock em %LOCALAPPDATA%\Programs\GameDock e cria o atalho na Área de Trabalho.
Set-Location $PSScriptRoot
$source = "dist\GameDock"
$target = Join-Path $env:LOCALAPPDATA "Programs\GameDock"
if (-not (Test-Path "$source\GameDock.exe")) { throw "Rode .\build.ps1 primeiro." }

$running = Get-Process GameDock -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path.StartsWith($target, [StringComparison]::OrdinalIgnoreCase) }
if ($running) { throw "Feche o GameDock antes de atualizar (ele está aberto)." }

if (Test-Path $target) { Remove-Item -Recurse -Force $target }
New-Item -ItemType Directory -Force $target | Out-Null
Copy-Item -Recurse "$source\*" $target

$desktop = [Environment]::GetFolderPath("Desktop")
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut((Join-Path $desktop "GameDock.lnk"))
$link.TargetPath = Join-Path $target "GameDock.exe"
$link.WorkingDirectory = $target
$link.IconLocation = (Join-Path $target "GameDock.exe") + ",0"
$link.Description = "GameDock — todos os seus jogos num só lugar"
$link.Save()

# versão antiga (onefile) copiada direto na Área de Trabalho
Remove-Item (Join-Path $desktop "GameDock.exe") -ErrorAction SilentlyContinue
"GameDock instalado em $target (atalho na Área de Trabalho)"

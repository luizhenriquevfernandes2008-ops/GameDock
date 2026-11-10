# Gera dist\GameDock\ (o app) e dist\GameDock-Setup.exe (instalador com o app dentro).
# Requer: python -m pip install pyinstaller pywebview pystray pillow
#
# O app é "onedir" (pasta) em vez de "onefile": o onefile descompacta ~13 MB numa pasta temporária
# a cada abertura (~1,5 s a mais). Só o instalador é onefile, porque ele roda uma vez.
Set-Location $PSScriptRoot
if (-not (Test-Path assets\gamedock.ico)) { python assets\make_icon.py }
# testes primeiro: versão com teste quebrado não chega a ser gerada
python -m unittest discover -s tests
if ($LASTEXITCODE -ne 0) { Write-Error "Testes falharam: build cancelado."; exit $LASTEXITCODE }
python -m PyInstaller --onedir --windowed --name GameDock --icon assets\gamedock.ico `
    --add-data "gamedock\web;gamedock\web" --clean --noconfirm run_gamedock.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
python -m PyInstaller --onefile --windowed --name GameDock-Setup --icon assets\gamedock.ico `
    --add-data "dist\GameDock;payload" --noconfirm run_setup.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
# hash que a atualização automática confere (vai junto no release do GitHub)
$hash = (Get-FileHash dist\GameDock-Setup.exe -Algorithm SHA256).Hash.ToLower()
[IO.File]::WriteAllText("$PWD\dist\GameDock-Setup.exe.sha256", "$hash  GameDock-Setup.exe`n")

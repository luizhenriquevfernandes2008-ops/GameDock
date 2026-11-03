# Ponto de entrada do GameDock.exe (PyInstaller precisa de um script, não de um pacote).
# A linha de comando continua disponível via:  python -m gamedock <comando>
from gamedock.app import run

run()

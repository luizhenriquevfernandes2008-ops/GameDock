"""Testes do GameDock. Rodar da pasta do projeto:  python -m unittest discover -s tests -v

Cada teste usa uma pasta temporária como GAMEDOCK_HOME, então nada toca na biblioteca real.
"""
import os
import tempfile

# antes de qualquer import do gamedock: a pasta de dados vira uma pasta temporária
os.environ["GAMEDOCK_HOME"] = tempfile.mkdtemp(prefix="gamedock-test-")

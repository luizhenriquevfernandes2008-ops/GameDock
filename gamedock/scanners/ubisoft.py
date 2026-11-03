from __future__ import annotations

import winreg
from pathlib import Path

from ..models import Game
from .base import Scanner, reg_subkeys


class UbisoftScanner(Scanner):
    name = "ubisoft"

    def scan(self):
        for game_id, values in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Ubisoft\Launcher\Installs"):
            install_dir = values.get("InstallDir")
            if not install_dir or not Path(install_dir).exists():
                continue
            yield Game(
                id=f"ubisoft:{game_id}",
                # O registro não guarda o nome; a pasta de instalação costuma ser o nome do jogo.
                name=Path(install_dir.rstrip("/\\")).name,
                source=self.name,
                launch_type="uri",
                launch_target=f"uplay://launch/{game_id}/0",
                install_dir=install_dir,
            )

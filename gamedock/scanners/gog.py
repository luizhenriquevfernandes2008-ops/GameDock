from __future__ import annotations

import shlex
import winreg

from ..models import Game
from .base import Scanner, reg_subkeys


class GogScanner(Scanner):
    name = "gog"

    def scan(self):
        for game_id, values in reg_subkeys(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\GOG.com\Games"):
            exe = values.get("exe") or values.get("EXE")
            if not exe or values.get("dependsOn"):
                continue  # DLCs têm "dependsOn" apontando pro jogo base
            yield Game(
                id=f"gog:{game_id}",
                name=values.get("gameName") or values.get("GAMENAME") or game_id,
                source=self.name,
                launch_type="exe",
                launch_target=exe,
                launch_args=shlex.split(values.get("launchParam", ""), posix=False),
                working_dir=values.get("workingDir") or values.get("path"),
                install_dir=values.get("path"),
                exe=exe,
            )

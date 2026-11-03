from __future__ import annotations

import json
import os
from pathlib import Path

from ..models import Game
from .base import Scanner

MANIFESTS = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"


class EpicScanner(Scanner):
    name = "epic"

    def scan(self):
        if not MANIFESTS.exists():
            return
        for item in MANIFESTS.glob("*.item"):
            try:
                data = json.loads(item.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if data.get("bIsIncompleteInstall"):
                continue
            categories = data.get("AppCategories") or []
            if categories and "games" not in categories:
                continue  # DLCs, plugins da Unreal, etc.

            app_name = data.get("AppName", "")
            install = Path(data.get("InstallLocation", ""))
            exe = install / data["LaunchExecutable"] if data.get("LaunchExecutable") else None
            # O launcher da Epic precisa ser usado para autenticação dos jogos.
            uri = (
                "com.epicgames.launcher://apps/"
                f"{data.get('CatalogNamespace', '')}%3A{data.get('CatalogItemId', '')}%3A{app_name}"
                "?action=launch&silent=true"
            )
            yield Game(
                id=f"epic:{app_name}",
                name=data.get("DisplayName", app_name),
                source=self.name,
                launch_type="uri",
                launch_target=uri,
                install_dir=str(install),
                exe=str(exe) if exe else None,
            )

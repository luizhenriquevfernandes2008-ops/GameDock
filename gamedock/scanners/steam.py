from __future__ import annotations

import winreg
from pathlib import Path

from ..formats import read_vdf
from ..models import Game
from .base import Scanner, reg_value

# Apps da Steam que não são jogos (runtimes, redistribuíveis, ferramentas).
IGNORED_APP_IDS = {
    "228980",   # Steamworks Common Redistributables
    "250820",   # SteamVR
    "1070560",  # Steam Linux Runtime
    "1391110",  # Steam Linux Runtime - Soldier
    "1628350",  # Steam Linux Runtime - Sniper
    "1493710",  # Proton Experimental
}
STATE_FULLY_INSTALLED = 4


class SteamScanner(Scanner):
    name = "steam"

    def steam_root(self) -> Path | None:
        for root, key, value in (
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam", "SteamPath"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam", "InstallPath"),
        ):
            path = reg_value(root, key, value)
            if path and Path(path).exists():
                return Path(path)
        return None

    def library_folders(self, root: Path) -> list[Path]:
        folders = [root]
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            data = read_vdf(vdf).get("libraryfolders", {})
            for entry in data.values():
                if isinstance(entry, dict) and entry.get("path"):
                    folders.append(Path(entry["path"]))
        unique = {str(p).lower(): p for p in folders}
        return list(unique.values())

    def scan(self):
        root = self.steam_root()
        if root is None:
            return
        for library in self.library_folders(root):
            steamapps = library / "steamapps"
            for manifest in steamapps.glob("appmanifest_*.acf"):
                app = read_vdf(manifest).get("AppState", {})
                app_id = app.get("appid")
                if not app_id or app_id in IGNORED_APP_IDS:
                    continue
                if not int(app.get("StateFlags", "0")) & STATE_FULLY_INSTALLED:
                    continue  # baixando / incompleto
                install_dir = steamapps / "common" / app.get("installdir", "")
                yield Game(
                    id=f"steam:{app_id}",
                    name=app.get("name", f"Steam App {app_id}"),
                    source=self.name,
                    # Iniciar pela Steam garante DRM, overlay e Steam Input funcionando.
                    launch_type="uri",
                    launch_target=f"steam://rungameid/{app_id}",
                    install_dir=str(install_dir) if install_dir.exists() else None,
                )

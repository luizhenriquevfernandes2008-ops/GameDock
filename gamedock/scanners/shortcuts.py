"""Detecta jogos pelos atalhos do Menu Iniciar e da Área de Trabalho.

Cobre launchers que não têm um "banco de dados" fácil de ler, mas criam atalhos:
Riot (LoL, Valorant, 2XKO), Battle.net, Google Play Games, EA app, etc.
Atalhos para um .exe qualquer passam por `detect.analyze`, que decide se é um jogo
(ex: jogos instalados sem launcher, como Castlevania: Lords of Shadow).
"""
from __future__ import annotations

import json
import os
import re
import shlex
from pathlib import Path

from ..detect import analyze
from ..formats import read_lnk, read_url_shortcut
from ..models import Game
from .base import Scanner, slug

# esquema de URI -> fonte
URI_SOURCES = {
    "steam://rungameid/": "steam",
    "com.epicgames.launcher://": "epic",
    "uplay://": "ubisoft",
    "origin://": "ea",
    "origin2://": "ea",
    "battlenet://": "battlenet",
    "googleplaygames://": "google",
    "goggalaxy://": "gog",
}

# executável do launcher -> (fonte, argumento que indica "abrir um jogo específico")
LAUNCHER_EXES = {
    "riotclientservices.exe": ("riot", "--launch-product"),
    "battle.net.exe": ("battlenet", "--exec"),
    "battle.net launcher.exe": ("battlenet", "--exec"),
    "eadesktop.exe": ("ea", "-gameId"),
}

# pastas do Menu Iniciar cujos atalhos são todos jogos
GAME_FOLDERS = {
    "google play games": "google",
}
# atalhos dentro dessas pastas que são o próprio launcher, não um jogo
LAUNCHER_NAMES = {"google play games", "riot client", "cliente riot", "battle.net", "steam", "ea", "gamedock"}


# "Jogo - Atalho.lnk", "Jogo - Shortcut.lnk", "Jogo (2).lnk": sufixos que o Windows adiciona
SHORTCUT_SUFFIX = re.compile(r"(\s*-\s*(atalho|shortcut|c[oó]pia|copy|acceso directo|verknüpfung)|\s*\(\d+\))+$", re.I)


def clean_name(stem: str) -> str:
    return SHORTCUT_SUFFIX.sub("", stem).strip() or stem


def shortcut_dirs() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    programdata = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    public = Path(os.environ.get("PUBLIC", r"C:\Users\Public"))
    return [
        appdata / "Microsoft/Windows/Start Menu/Programs",
        programdata / "Microsoft/Windows/Start Menu/Programs",
        Path.home() / "Desktop",
        public / "Desktop",
    ]


class ShortcutScanner(Scanner):
    name = "shortcuts"

    def scan(self):
        for directory in shortcut_dirs():
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if path.suffix.lower() not in (".lnk", ".url"):
                    continue
                game = self.from_url(path) if path.suffix.lower() == ".url" else self.from_lnk(path)
                if game:
                    yield game

    def from_url(self, path: Path) -> Game | None:
        url = read_url_shortcut(path)
        if not url:
            return None
        source = next((s for prefix, s in URI_SOURCES.items() if url.lower().startswith(prefix)), None)
        if source is None:
            source = self.folder_source(path)
            if source is None:
                return None
        return Game(
            id=f"{source}:{slug(clean_name(path.stem))}",
            name=clean_name(path.stem),
            source=source,
            launch_type="uri",
            launch_target=url,
        )

    def from_lnk(self, path: Path) -> Game | None:
        if clean_name(path.stem).lower() in LAUNCHER_NAMES:
            return None
        info = read_lnk(path)
        if not info or not info.get("target"):
            return None
        target = info["target"]
        args = info.get("arguments", "") or ""

        if not target.lower().endswith(".exe"):
            return None
        source, install_dir = None, None
        exe_name = Path(target).name.lower()
        if exe_name in LAUNCHER_EXES:
            launcher_source, game_flag = LAUNCHER_EXES[exe_name]
            if game_flag.lower() not in args.lower():
                return None  # atalho que só abre o launcher
            source = launcher_source
        else:
            source = self.folder_source(path)
        if source is None:
            # .exe desconhecido: analisa o executável e a pasta para decidir se é jogo
            if "\\steamapps\\common\\" in target.lower() or not Path(target).is_file():
                return None  # jogos da Steam já vêm do scanner da Steam
            verdict = analyze(Path(target), quick=True)
            if not verdict.is_game:
                return None
            source, install_dir = "local", str(verdict.root)
        if source == "riot":
            install_dir = riot_install_dir(clean_name(path.stem))

        return Game(
            id=f"{source}:{slug(clean_name(path.stem))}",
            name=clean_name(path.stem),
            source=source,
            launch_type="exe",
            launch_target=target,
            launch_args=_split_args(args),
            working_dir=info.get("working_dir") or str(Path(target).parent),
            install_dir=install_dir,
            exe=target,
            icon=info.get("icon_location") or None,
        )

    @staticmethod
    def folder_source(path: Path) -> str | None:
        for parent in path.parents:
            if parent.name.lower() in GAME_FOLDERS:
                return GAME_FOLDERS[parent.name.lower()]
        return None


def riot_install_dir(game_name: str) -> str | None:
    """O Riot Client lista as pastas dos jogos em RiotClientInstalls.json."""
    installs = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Riot Games" / "RiotClientInstalls.json"
    try:
        folders = json.loads(installs.read_text(encoding="utf-8")).get("associated_client", {})
    except (OSError, json.JSONDecodeError):
        return None
    wanted = slug(game_name)
    for folder in folders:
        parts = [slug(p) for p in Path(folder).parts]
        if wanted in parts:
            # "D:/Riot Games/2XKO/Live/" -> "D:/Riot Games/2XKO"
            return str(Path(*Path(folder).parts[:parts.index(wanted) + 1]))
    return None


def _split_args(args: str) -> list[str]:
    return shlex.split(args, posix=False) if args.strip() else []

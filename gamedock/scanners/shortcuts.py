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

# carregadores de mod que abrem o jogo por argumento (ex: "SILENT HILL 3 (Reloaded).lnk"
# -> Reloaded-II.exe --launch "...\sh3.exe"). Analisamos o jogo, mas iniciamos pelo
# carregador, senão o jogo abriria sem os mods.
MOD_LOADERS = {
    "reloaded-ii.exe": "--launch",
}

# launchers de jogos recompilados/portados que atualizam e abrem o jogo (o atalho aponta
# para o launcher, o jogo mora em outra pasta). Cada um sabe ler onde está o jogo.
RECOMP_LAUNCHERS = {"nx2navi.exe": "nx2navi"}
# nomes bonitos para o .exe do jogo (senão usamos o nome do arquivo do dump)
RECOMP_TITLES = {"ocarina.exe": "The Legend of Zelda: Ocarina of Time"}

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

    # fontes que este scanner pode produzir (o merge usa para saber o que sumiu)
    sources = {"local", "riot", "battlenet", "ea", "google", *URI_SOURCES.values(), *GAME_FOLDERS.values()}

    def scan(self):
        games = []
        for directory in shortcut_dirs():
            if not directory.exists():
                continue
            for path in directory.rglob("*"):
                if path.suffix.lower() not in (".lnk", ".url"):
                    continue
                game = self.from_url(path) if path.suffix.lower() == ".url" else self.from_lnk(path)
                if game:
                    games.append(game)
        # o mesmo jogo com e sem carregador de mods (Reloaded-II): a versão com mods ganha
        # a deduplicação por pasta, que fica com o primeiro que aparece
        games.sort(key=lambda g: Path(g.launch_target).name.lower() not in MOD_LOADERS)
        yield from games

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
        game_exe = target
        exe_name = Path(target).name.lower()
        if exe_name in RECOMP_LAUNCHERS:
            return self.from_recomp_launcher(path, info, RECOMP_LAUNCHERS[exe_name])
        if exe_name in MOD_LOADERS:
            game_exe = _flag_value(args, MOD_LOADERS[exe_name])
            if not game_exe or not game_exe.lower().endswith(".exe"):
                return None  # atalho que só abre o carregador de mods
        if exe_name in LAUNCHER_EXES:
            launcher_source, game_flag = LAUNCHER_EXES[exe_name]
            if game_flag.lower() not in args.lower():
                return None  # atalho que só abre o launcher
            source = launcher_source
        else:
            source = self.folder_source(path)
        if source is None:
            # .exe desconhecido: analisa o executável e a pasta para decidir se é jogo
            if "\\steamapps\\common\\" in game_exe.lower() or not Path(game_exe).is_file():
                return None  # jogos da Steam já vêm do scanner da Steam
            verdict = analyze(Path(game_exe), quick=True)
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
            exe=game_exe,
            icon=info.get("icon_location") or None,
        )

    def from_recomp_launcher(self, path: Path, info: dict, kind: str) -> Game | None:
        """Atalho de um launcher de recompilação (ex: NX2navi → Ocarina of Time recompilado).
        Abrimos pelo launcher (ele atualiza o jogo), mas pasta e .exe são os do jogo: assim o
        tempo de jogo é contado e a capa é procurada pelo nome certo."""
        launcher = Path(info["target"])
        found = recomp_game(launcher, kind)
        if found is None:
            return None
        name, game_dir, game_exe = found
        return Game(
            id=f"local:{slug(name)}",
            name=name,
            source="local",
            launch_type="exe",
            launch_target=str(launcher),
            launch_args=_split_args(info.get("arguments", "") or ""),
            working_dir=info.get("working_dir") or str(launcher.parent),
            install_dir=str(game_dir),
            exe=str(game_exe),
            icon=str(game_exe),
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


def recomp_game(launcher: Path, kind: str) -> tuple[str, Path, Path] | None:
    """(nome, pasta do jogo, .exe do jogo) de um launcher de recompilação, ou None."""
    if kind == "nx2navi":
        # o NX2navi grava em <launcher>\data\launcher.json: {"game_dir": "D:\\Games\\NX2navi", "dump": "...\\Jogo [id][v].nup"}
        try:
            config = json.loads((launcher.parent / "data" / "launcher.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        game_dir = Path(config.get("game_dir") or "")
        if not config.get("game_dir") or not game_dir.is_dir():
            return None   # ainda não instalou o jogo
        from .folders import best_executable
        exes = [p for p in game_dir.glob("*.exe") if p.name.lower() in RECOMP_TITLES]
        game_exe = exes[0] if exes else best_executable(game_dir)
        if game_exe is None:
            return None
        dump = Path(config.get("dump") or "").stem
        dump_name = re.sub(r"\s*\[[^\]]*\]", "", dump).strip()   # "Jogo [0400486024dec000][v131072]" -> "Jogo"
        name = RECOMP_TITLES.get(game_exe.name.lower()) or dump_name or game_dir.name
        return name, game_dir, game_exe
    return None


def _flag_value(args: str, flag: str) -> str | None:
    """'--launch "C:\\x\\sh3.exe" --foo' -> 'C:\\x\\sh3.exe'"""
    parts = _split_args(args)
    for i, part in enumerate(parts[:-1]):
        if part.lower() == flag.lower():
            return parts[i + 1].strip('"')
    return None


def _split_args(args: str) -> list[str]:
    """Separa os argumentos com as regras do próprio Windows (CommandLineToArgvW).

    O shlex (posix=False) deixava as aspas no argumento: '"C:\\a b\\x.exe"'. O launcher
    remonta a linha com list2cmdline, que escapava essas aspas e o jogo recebia lixo.
    """
    if not args.strip():
        return []
    try:
        import ctypes
        from ctypes import wintypes
        shell32, kernel32 = ctypes.windll.shell32, ctypes.windll.kernel32
        shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
        shell32.CommandLineToArgvW.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int))
        count = ctypes.c_int()
        # o 1º item segue regras especiais (é o nome do programa): um nome falso fica nele
        argv = shell32.CommandLineToArgvW(f"x {args}", ctypes.byref(count))
        if argv:
            try:
                return [argv[i] for i in range(1, count.value)]
            finally:
                kernel32.LocalFree(argv)
    except (OSError, AttributeError):
        pass
    return [a.strip('"') for a in shlex.split(args, posix=False)]

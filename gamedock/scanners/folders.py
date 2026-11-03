"""Jogos sem launcher: o usuário aponta pastas (ex: D:\\Games) e cada subpasta vira um jogo.

O desafio é escolher, entre vários .exe, qual é o do jogo. Usamos uma pontuação:
nome parecido com o da pasta, tamanho do arquivo, profundidade, e uma lista
de nomes que quase nunca são o jogo (desinstalador, crash reporter, redist...).
"""
from __future__ import annotations

import re
from pathlib import Path

from ..models import Game
from .base import Scanner, slug
from .shortcuts import LAUNCHER_EXES

BAD_EXE = re.compile(
    r"unins|setup|install|redist|vc_?redist|dxsetup|directx|dotnet|crash|report|bugsplat|"
    r"prereq|easyanticheat|eac_|battleye|beservice|helper|cefsharp|updat|patch|"
    r"config|benchmark|server|editor|7z|python|java|cleanup|touchup|activation",
    re.IGNORECASE,
)
SKIP_DIRS = {"_commonredist", "redist", "redistributables", "directx", "__installer",
             "support", "engine", "easyanticheat", "battleye", "_redist", "dotnet"}
MAX_DEPTH = 4


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_executables(folder: Path, depth: int = 0):
    try:
        entries = list(folder.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.is_file() and entry.suffix.lower() == ".exe":
            yield entry, depth
        elif entry.is_dir() and depth < MAX_DEPTH and entry.name.lower() not in SKIP_DIRS:
            yield from find_executables(entry, depth + 1)


def score_exe(exe: Path, depth: int, game_folder: str) -> float:
    if BAD_EXE.search(exe.stem):
        return -1
    stem, folder = _norm(exe.stem), _norm(game_folder)
    score = 0.0
    if stem == folder:
        score += 100
    elif stem and folder and (stem in folder or folder in stem):
        score += 50
    if "shipping" in stem:  # Unreal Engine: <Jogo>-Win64-Shipping.exe
        score += 20
    if "launcher" in stem:
        score -= 20
    try:
        score += min(exe.stat().st_size / 1_000_000, 50)  # até +50 por tamanho (MB)
    except OSError:
        pass
    return score - depth * 8


def best_executable(game_dir: Path) -> Path | None:
    found = list(find_executables(game_dir))
    if any(exe.name.lower() in LAUNCHER_EXES for exe, _ in found):
        return None  # pasta do próprio launcher (ex: Riot Client), não de um jogo
    candidates = [(score_exe(exe, depth, game_dir.name), exe) for exe, depth in found]
    candidates = [c for c in candidates if c[0] >= 0]
    return max(candidates, key=lambda c: c[0])[1] if candidates else None


class FolderScanner(Scanner):
    name = "folder"

    def __init__(self, folders: list[str]):
        self.folders = [Path(f) for f in folders]

    def scan(self):
        for root in self.folders:
            if not root.is_dir():
                continue
            for game_dir in sorted(p for p in root.iterdir() if p.is_dir()):
                exe = best_executable(game_dir)
                if exe is None:
                    continue
                yield Game(
                    id=f"folder:{slug(str(game_dir))}",
                    name=game_dir.name,
                    source=self.name,
                    launch_type="exe",
                    launch_target=str(exe),
                    working_dir=str(exe.parent),
                    install_dir=str(game_dir),
                    exe=str(exe),
                )

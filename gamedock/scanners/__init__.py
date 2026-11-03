from __future__ import annotations

from pathlib import Path

from ..models import Game
from .base import Scanner
from .epic import EpicScanner
from .folders import FolderScanner
from .gog import GogScanner
from .shortcuts import ShortcutScanner
from .steam import SteamScanner
from .ubisoft import UbisoftScanner


class EmulatorScanner(Scanner):
    """Jogos dos emuladores integrados (PS3...), nas pastas que o usuário adicionou."""
    name = "emulators"

    def scan(self):
        from ..emulators import EMULATORS
        for emulator in EMULATORS.values():
            yield from emulator.scan()


def build_scanners(config: dict) -> list[Scanner]:
    # Ordem importa: fontes mais confiáveis primeiro. Se um atalho aponta para
    # algo que a Steam já trouxe, a versão da Steam ganha.
    scanners: list[Scanner] = [
        SteamScanner(),
        EpicScanner(),
        GogScanner(),
        UbisoftScanner(),
        ShortcutScanner(),
        FolderScanner(config.get("scan_folders", [])),
        EmulatorScanner(),
    ]
    disabled = set(config.get("disabled_scanners", []))
    return [s for s in scanners if s.name not in disabled]


def run_scanners(scanners: list[Scanner]) -> tuple[list[Game], dict[str, str]]:
    """Roda todos os scanners e remove duplicatas. Retorna (jogos, erros por scanner)."""
    games: list[Game] = []
    errors: dict[str, str] = {}
    ids, signatures, install_dirs = set(), set(), set()

    for scanner in scanners:
        try:
            found = list(scanner.scan())
        except Exception as exc:  # um launcher quebrado não pode derrubar o scan inteiro
            errors[scanner.name] = f"{type(exc).__name__}: {exc}"
            continue
        for game in found:
            install = _norm_path(game.install_dir)
            if (game.id in ids or game.launch_signature in signatures
                    or (install and install in install_dirs)):
                continue
            ids.add(game.id)
            signatures.add(game.launch_signature)
            if install:
                install_dirs.add(install)
            games.append(game)
    return games, errors


def _norm_path(path: str | None) -> str | None:
    return str(Path(path)).lower().rstrip("\\/") if path else None

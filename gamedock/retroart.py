"""Capas de jogos de console (PS1, PS2, PS3, NES) do libretro-thumbnails (github.com/libretro-thumbnails).

É a coleção que o RetroArch usa: capa da caixa ("Named_Boxarts") e tela do jogo ("Named_Snaps"), com o nome no
padrão No-Intro/Redump — "Título (Região).png", igual ao nome dos dumps. A lista de arquivos de cada console
vem uma vez pela API do GitHub e fica guardada por 30 dias; o nome do dump é casado com a lista.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

from .models import Game
from .storage import data_dir

SYSTEMS = {"ps1": "Sony_-_PlayStation", "ps2": "Sony_-_PlayStation_2", "ps3": "Sony_-_PlayStation_3",
           "nes": "Nintendo_-_Nintendo_Entertainment_System"}
TTL = 30 * 24 * 3600
REGION_ORDER = ("(USA", "(World", "(Brazil", "(Europe", "(Japan")


def _norm(name: str) -> str:
    base = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", name)
    return re.sub(r"[^a-z0-9]", "", base.lower().replace("&", "and"))


def _listing(system: str) -> dict[str, list[str]]:
    cache = data_dir() / "art" / "_libretro" / f"{system}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.is_file() and time.time() - cache.stat().st_mtime < TTL:
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        url = f"https://api.github.com/repos/libretro-thumbnails/{system}/git/trees/master?recursive=1"
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GameDock"}), timeout=30) as r:
            tree = json.loads(r.read()).get("tree", [])
    except (OSError, ValueError):
        return json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
    listing: dict[str, list[str]] = {}
    for item in tree:
        folder, _, file = item["path"].partition("/")
        if folder in ("Named_Boxarts", "Named_Snaps", "Named_Titles") and file.endswith(".png"):
            listing.setdefault(folder, []).append(file[:-4])
    cache.write_text(json.dumps(listing), encoding="utf-8")
    return listing


def _best(names: list[str], stem: str, title: str) -> str | None:
    safe = re.sub(r'[&*/:`<>?\\|"]', "_", stem)
    if safe in names:
        return safe
    wanted = _norm(title)
    matches = [n for n in names if _norm(n) == wanted]
    if not matches:
        return None
    return sorted(matches, key=lambda n: next((i for i, r in enumerate(REGION_ORDER) if r in n), 9))[0]


def art(game: Game, dest_dir: Path) -> dict[str, Path | None]:
    """{"cover": capa da caixa, "hero": tela do jogo} baixadas para a pasta de arte do jogo."""
    system = SYSTEMS.get(game.source)
    if not system:
        return {}
    stem = Path(game.launch_target).stem if game.launch_target else game.name
    listing = _listing(system)
    result: dict[str, Path | None] = {}
    for kind, folder in (("cover", "Named_Boxarts"), ("hero", "Named_Snaps")):
        local = dest_dir / f"libretro-{kind}.png"
        if local.is_file():
            result[kind] = local
            continue
        name = _best(listing.get(folder, []), stem, game.name)
        if not name:
            result[kind] = None
            continue
        url = (f"https://raw.githubusercontent.com/libretro-thumbnails/{system}/master/{folder}/"
               f"{urllib.parse.quote(name)}.png")
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "GameDock"}), timeout=20) as r:
                data = r.read()
            if data[:4] == b"\x89PNG":
                local.write_bytes(data)
                result[kind] = local
        except OSError:
            result[kind] = None
    return result

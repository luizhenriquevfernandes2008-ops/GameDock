"""Ferramentas da biblioteca usadas pelas Configurações → Biblioteca / Armazenamento e pela galeria de capas.

- "Talvez seja jogo": atalhos que quase passaram no detector (o scanner grava em candidates.json);
  a pessoa marca "É jogo" (vira jogo no próximo scan) ou "Não é" (some da lista).
- Pastas de jogos: as que a pessoa adicionou e sugestões (pasta que já tem 2+ jogos da biblioteca).
- Limpar desinstalados: tira da biblioteca os jogos marcados como não instalados.
- Armazenamento: tamanho de cada jogo instalado e espaço livre de cada disco.
- Capas: as opções do SteamGridDB para o jogo, para escolher uma (não só a primeira).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from collections import Counter
from pathlib import Path

from . import disk, sgdb
from .art import _title_key, art_dir, search_name, set_custom_cover, _download
from .models import Game
from .scanners.shortcuts import candidates_file
from .storage import LIBRARY_LOCK, Library, load_config, save_config

# fontes cujos jogos moram numa pasta própria escolhida pela pessoa (servem para sugerir pastas)
FOLDER_SOURCES = ("local", "folder", "manual", "gog")


def _norm(path: str | Path | None) -> str:
    return str(path).replace("/", "\\").lower().rstrip("\\") if path else ""


# ---------------------------------------------------------------- talvez seja jogo

def candidates() -> list[dict]:
    try:
        found = json.loads(candidates_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    config = load_config()
    decided = {p.lower() for p in config.get("forced_games", []) + config.get("not_games", [])}
    known = {_norm(g.exe or g.launch_target) for g in Library().games.values()}
    return [c for c in found if c["exe"].lower() not in decided and _norm(c["exe"]) not in known
            and Path(c["exe"]).is_file()]


def decide_candidate(exe: str, is_game: bool) -> None:
    config = load_config()
    key = "forced_games" if is_game else "not_games"
    other = "not_games" if is_game else "forced_games"
    config[key] = sorted({*config.get(key, []), str(Path(exe))})
    config[other] = [p for p in config.get(other, []) if p.lower() != str(Path(exe)).lower()]
    save_config(config)


# ---------------------------------------------------------------- pastas de jogos

def folder_suggestions() -> list[dict]:
    """Pastas que já guardam 2+ jogos da biblioteca (ex: D:\\Games) e ainda não são pastas de busca:
    adicionadas, o GameDock acha também os jogos dali que não têm atalho."""
    from .uninstall import _protected
    config = load_config()
    current = {_norm(f) for f in config.get("scan_folders", [])}
    dismissed = {_norm(f) for f in config.get("dismissed_folders", [])}
    protected = _protected()
    parents: Counter = Counter()
    for game in Library().games.values():
        if game.installed and game.source in FOLDER_SOURCES and game.install_dir:
            parent = Path(game.install_dir).parent
            if len(parent.parts) >= 2:   # D:\Games sim, D:\ não
                parents[str(parent)] += 1
    out = []
    for folder, count in parents.most_common():
        key = _norm(folder)
        if count < 2 or key in current or key in dismissed or key in protected or not Path(folder).is_dir():
            continue
        try:
            subfolders = sum(1 for p in Path(folder).iterdir() if p.is_dir())
        except OSError:
            continue
        out.append({"path": folder, "games": count, "subfolders": subfolders})
    return out


def scan_folders() -> list[str]:
    return list(load_config().get("scan_folders", []))


def add_scan_folder(folder: str) -> list[str]:
    config = load_config()
    folders = config.get("scan_folders", [])
    if _norm(folder) not in {_norm(f) for f in folders}:
        folders.append(str(Path(folder)))
    config["scan_folders"] = folders
    save_config(config)
    return folders


def remove_scan_folder(folder: str) -> list[str]:
    config = load_config()
    config["scan_folders"] = [f for f in config.get("scan_folders", []) if _norm(f) != _norm(folder)]
    save_config(config)
    return config["scan_folders"]


def dismiss_suggestion(folder: str) -> None:
    config = load_config()
    config["dismissed_folders"] = sorted({*config.get("dismissed_folders", []), str(Path(folder))})
    save_config(config)


# ---------------------------------------------------------------- limpar desinstalados

def missing_games() -> list[Game]:
    return [g for g in Library().games.values() if not g.installed]


def purge_missing() -> int:
    """Tira da biblioteca os jogos desinstalados (e o cache de arte deles). O histórico de sessões
    fica no sessions.jsonl; se o jogo voltar, ele reaparece como novo."""
    with LIBRARY_LOCK:
        library = Library()
        gone = [g for g in library.games.values() if not g.installed]
        for game in gone:
            del library.games[game.id]
            shutil.rmtree(art_dir(game), ignore_errors=True)
        library.save()
    return len(gone)


# ---------------------------------------------------------------- armazenamento

def storage_overview() -> dict:
    """Jogos instalados por tamanho + espaço dos discos. Calcula o que ainda não está em cache
    (pode levar alguns segundos na primeira vez)."""
    games = []
    for game in Library().visible(include_hidden=True):
        path = disk.game_path(game)
        if not path:
            continue
        try:
            size = disk.size(game, compute=True)
        except OSError:
            size = None
        games.append({"id": game.id, "name": game.display_name, "source": game.source, "path": str(path),
                      "drive": path.drive.upper() + "\\", "size": size or 0})
    games.sort(key=lambda g: -g["size"])
    return {"drives": disk.drives(), "games": games}


# ---------------------------------------------------------------- galeria de capas

def cover_options(game: Game, limit: int = 30) -> list[dict]:
    """Capas verticais do SteamGridDB para este jogo (a API já ordena pelos votos)."""
    key = load_config().get("steamgriddb_key")
    if not key:
        return []
    appid = game.id.split(":", 1)[1] if game.source == "steam" and game.id.startswith("steam:") else None
    if not appid:
        from .art import steam_store_match
        appid = steam_store_match(game)
    try:
        gid = sgdb.game_id(search_name(game), appid, key, art_dir(game), _title_key)
        if gid is None:
            return []
        data = sgdb._cached(sgdb.QUERIES["cover"].format(id=gid), key, art_dir(game) / "sgdb-cover.json") or {}
    except sgdb.InvalidKey:
        return []
    return [{"url": i["url"], "thumb": i.get("thumb") or i["url"], "author": (i.get("author") or {}).get("name", "")}
            for i in (data.get("data") or [])[:limit] if i.get("url")]


def set_cover_from_url(game: Game, url: str) -> bool:
    """Baixa a capa escolhida na galeria e usa como capa do jogo (vale como "Trocar capa")."""
    allowed = {o["url"] for o in cover_options(game)}
    if url not in allowed:   # só aceita URLs que vieram da galeria do próprio jogo
        return False
    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp) / f"cover{Path(url).suffix.lower() or '.png'}"
        if not _download([url], dest):
            return False
        set_custom_cover(game, dest)
    return True

"""Espaço em disco: quanto cada jogo ocupa e mover jogo para outro HD.

O tamanho é calculado em segundo plano e guardado (recalcula se a pasta mudar de data ou a cada 7 dias).
Mover só vale para jogos que o GameDock abre direto (pasta/atalho/ISO de emulador): jogos de loja
(Steam, Epic...) precisam ser movidos pela própria loja, senão ela perde o jogo.
"""
from __future__ import annotations

import json
import os
import shutil
import threading
import time
from pathlib import Path

from .models import Game
from .storage import LIBRARY_LOCK, Library, data_dir

TTL = 7 * 24 * 3600
MOVABLE = ("local", "folder", "manual", "ps3", "ps2", "ps1", "nes")
_LOCK = threading.Lock()
TASKS: dict[str, dict] = {}   # game_id -> progresso de uma mudança em andamento


def game_path(game: Game) -> Path | None:
    for value in (game.install_dir, str(Path(game.exe).parent) if game.exe else None):
        if value and Path(value).exists():
            return Path(value)
    return None


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.stat(os.path.join(root, name)).st_size
            except OSError:
                pass
    return total


def _cache() -> dict:
    try:
        return json.loads((data_dir() / "disk.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def size(game: Game, compute: bool = True) -> int | None:
    path = game_path(game)
    if not path:
        return None
    cache = _cache()
    hit = cache.get(game.id)
    mtime = path.stat().st_mtime
    if hit and hit.get("path") == str(path) and hit.get("mtime") == mtime and time.time() - hit["at"] < TTL:
        return hit["size"]
    if not compute:
        return hit["size"] if hit else None
    total = _size(path)
    with _LOCK:
        cache = _cache()
        cache[game.id] = {"path": str(path), "mtime": mtime, "at": time.time(), "size": total}
        (data_dir() / "disk.json").write_text(json.dumps(cache), encoding="utf-8")
    return total


def drives() -> list[dict]:
    result = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            try:
                usage = shutil.disk_usage(root)
            except OSError:
                continue
            result.append({"drive": root, "free": usage.free, "total": usage.total})
    return result


def info(game: Game) -> dict:
    path = game_path(game)
    return {"path": str(path) if path else None, "size": size(game, compute=False), "movable": game.source in MOVABLE,
            "drive": path.drive + "\\" if path else None, "drives": drives(), "task": TASKS.get(game.id)}


def _copy_tree(src: Path, dst: Path, task: dict) -> None:
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        task["done"] += src.stat().st_size
        return
    for root, _dirs, files in os.walk(src):
        target = dst / Path(root).relative_to(src)
        target.mkdir(parents=True, exist_ok=True)
        for name in files:
            shutil.copy2(os.path.join(root, name), target / name)
            task["done"] += os.stat(target / name).st_size


def move(game_id: str, dest_folder: str) -> dict:
    """Começa a mover em segundo plano; a interface acompanha por TASKS[game_id]."""
    game = Library().games.get(game_id)
    if not game or game.source not in MOVABLE:
        return {"ok": False, "error": "Esse jogo é de uma loja: mova pela própria loja (ex: Steam → Armazenamento)."}
    src = game_path(game)
    if not src:
        return {"ok": False, "error": "Não achei a pasta do jogo."}
    dst = Path(dest_folder) / src.name
    if dst.exists():
        return {"ok": False, "error": f"Já existe {dst}."}
    total = size(game) or 0
    if shutil.disk_usage(Path(dest_folder).anchor).free < total * 1.05:
        return {"ok": False, "error": "Não tem espaço livre suficiente nesse disco."}
    task = TASKS[game_id] = {"done": 0, "total": total, "running": True, "error": None, "to": str(dst)}

    def work():
        try:
            _copy_tree(src, dst, task)
            if _size(dst) != _size(src):
                raise OSError("a cópia ficou com tamanho diferente do original")
            old, new = str(src), str(dst)
            with LIBRARY_LOCK:
                library = Library()
                g = library.games[game_id]
                for field in ("launch_target", "install_dir", "exe", "working_dir", "icon"):
                    value = getattr(g, field)
                    if value and value.lower().startswith(old.lower()):
                        setattr(g, field, new + value[len(old):])
                library.save()
            if game.source in ("ps3", "ps2", "ps1", "nes"):   # o novo lugar entra nas pastas do emulador
                from .emulators import EMULATORS
                emu = EMULATORS.get(game.source)
                if emu and str(dst.parent) not in emu.settings()["folders"]:
                    emu.save_settings({"folders": emu.settings()["folders"] + [str(dst.parent)]})
            shutil.rmtree(src) if src.is_dir() else src.unlink()
        except Exception as exc:
            task["error"] = str(exc)
            if dst.exists() and src.exists():   # falhou no meio: apaga a cópia incompleta, o original fica
                shutil.rmtree(dst, ignore_errors=True) if dst.is_dir() else dst.unlink(missing_ok=True)
        task["running"] = False

    threading.Thread(target=work, daemon=True, name=f"move-{game_id}").start()
    return {"ok": True}

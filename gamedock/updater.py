"""Atualização automática pelas "Releases" de um repositório do GitHub.

Cada versão publicada é uma release com a tag vX.Y.Z e dois arquivos:
  GameDock-Setup.exe          o instalador gerado pelo build.ps1
  GameDock-Setup.exe.sha256   o SHA-256 dele (o GameDock recusa o download se não conferir)
O GameDock confere uma vez por dia (e no botão "Procurar atualização"), baixa, confere o hash e roda o
instalador em modo silencioso (--silent): ele espera o GameDock fechar, instala por cima e abre de novo.

O repositório padrão é DEFAULT_REPO; config.json → "update_repo" troca (ex: para testar um fork).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
from pathlib import Path

from . import __version__
from .launcher import clean_dll_search
from .storage import load_config, save_config

CHECK_EVERY = 24 * 3600
DEFAULT_REPO = "luizhenriquevfernandes2008-ops/GameDock"   # onde as versões oficiais são publicadas
USER_AGENT = f"GameDock/{__version__}"


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version)[:3]) or (0,)


def newer(remote: str, local: str = __version__) -> bool:
    return _parse(remote) > _parse(local)


def _get(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def repo() -> str | None:
    value = (load_config().get("update_repo") or DEFAULT_REPO).strip().strip("/")
    return value if re.fullmatch(r"[\w.-]+/[\w.-]+", value) else None


def check(force: bool = False) -> dict:
    """{"available": bool, "version", "notes", "current", "error"}. Sem force, usa o resultado do dia."""
    config = load_config()
    cached = config.get("update_check") or {}
    base = {"current": __version__, "repo": repo()}
    if not base["repo"]:
        return base | {"available": False, "error": "no_repo"}
    if not force and time.time() - cached.get("at", 0) < CHECK_EVERY and cached.get("repo") == base["repo"]:
        return base | cached | {"available": newer(cached.get("version", "0"))}
    try:
        release = json.loads(_get(f"https://api.github.com/repos/{base['repo']}/releases/latest"))
    except Exception as exc:
        return base | {"available": False, "error": str(exc)}
    assets = {a["name"]: a["browser_download_url"] for a in release.get("assets", [])}
    info = {"at": time.time(), "repo": base["repo"], "version": release.get("tag_name", "").lstrip("v"),
            "notes": (release.get("body") or "").strip()[:2000], "setup": assets.get("GameDock-Setup.exe"),
            "sha256": assets.get("GameDock-Setup.exe.sha256")}
    config["update_check"] = info
    save_config(config)
    return base | info | {"available": newer(info["version"]) and bool(info["setup"])}


def download_and_install(progress=lambda done, total: None) -> Path:
    """Baixa o instalador novo, confere o SHA-256 e abre ele em modo silencioso. Depois disso o app deve sair."""
    info = check()
    if not info.get("available"):
        raise RuntimeError("Nenhuma versão nova.")
    if not info.get("sha256"):
        raise RuntimeError("A versão publicada não tem o arquivo .sha256; por segurança, não instalo sem conferir.")
    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GameDock" / "update"
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / "GameDock-Setup.exe"
    req = urllib.request.Request(info["setup"], headers={"User-Agent": USER_AGENT})
    digest = hashlib.sha256()
    with urllib.request.urlopen(req, timeout=60) as resp, dest.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 16):
            out.write(chunk)
            digest.update(chunk)
            done += len(chunk)
            progress(done, total)
    expected = _get(info["sha256"]).decode("utf-8", "replace").split()[0].lower()
    if digest.hexdigest() != expected:
        dest.unlink(missing_ok=True)
        raise RuntimeError("O download veio diferente do publicado (SHA-256 não confere). Nada foi instalado.")
    with clean_dll_search():
        subprocess.Popen([str(dest), "--silent"], cwd=str(folder))
    return dest

"""Desinstalar um jogo pelo GameDock.

Ordem de preferência (sempre o jeito "oficial" primeiro):
  1. Steam: o desinstalador da própria Steam (steam://uninstall/<appid>) — ela pede confirmação.
  2. Desinstalador registrado no Windows ("Aplicativos instalados": Inno Setup, GOG, Riot...)
     ou um unins*.exe na pasta do jogo.
  3. Sem desinstalador (jogo copiado numa pasta, ISO/ROM de emulador): manda para a LIXEIRA
     (dá para restaurar), junto com os atalhos que apontavam para ele.

Travas da opção 3: nunca apaga raiz de disco, pastas do Windows/usuário, pastas de biblioteca
(D:\\Games, SteamLibrary...), nem uma pasta que tenha outro jogo da biblioteca dentro.
Antes de qualquer coisa, os saves são copiados (backup "antes de desinstalar", que não expira).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import time
import winreg
from ctypes import wintypes
from pathlib import Path

from .detect import install_root
from .formats import read_lnk
from .models import Game
from .scanners.base import reg_subkeys
from .scanners.shortcuts import _split_args, shortcut_dirs
from .storage import Library, data_dir

UNINSTALL_KEYS = (
    (winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
)
EMULATED = ("ps1", "ps2", "ps3", "nes")
DELETABLE = ("local", "folder", "manual", *EMULATED)
# pastas que guardam vários jogos: apagar uma delas apagaria a biblioteca inteira
LIBRARY_NAMES = {"games", "jogos", "steamlibrary", "steamapps", "common", "gog games", "epic games",
                 "riot games", "program files", "program files (x86)", "classicos", "roms", "isos"}
ERROR_ELEVATION_REQUIRED = 740
WAIT_LIMIT = 15 * 60   # quanto tempo esperamos o desinstalador terminar


def _norm(path: str | Path | None) -> str:
    return str(path).replace("/", "\\").lower().rstrip("\\") if path else ""


def _inside(child: str, parent: str) -> bool:
    return bool(child and parent) and (child == parent or child.startswith(parent + "\\"))


def _name_key(name: str) -> str:
    from .art import _title_key, NAME_TAGS
    return _title_key(NAME_TAGS.sub("", name or ""))


# ---------------------------------------------------------------- o que vai acontecer

def _registry_uninstaller(game: Game) -> dict | None:
    """Entrada de "Aplicativos instalados" deste jogo: pela pasta de instalação, ou pelo nome
    (desde que a pasta registrada, se houver, bata com a do jogo)."""
    install = _norm(game.install_dir)
    wanted = _name_key(game.name)
    best = None
    for root, key in UNINSTALL_KEYS:
        for sub, values in reg_subkeys(root, key):
            command = values.get("UninstallString")
            if not command or values.get("SystemComponent") == 1:
                continue
            location = _norm(values.get("InstallLocation") or values.get("Inno Setup: App Path"))
            related = bool(location and install) and (_inside(location, install) or _inside(install, location))
            same_name = bool(wanted) and _name_key(values.get("DisplayName", "")) == wanted
            entry = {"name": values.get("DisplayName") or game.name, "command": command, "key": (root, f"{key}\\{sub}")}
            if related and (same_name or location == install):
                return entry
            if same_name and not location and best is None:
                best = entry
    return best


def _folder_uninstaller(game: Game) -> dict | None:
    if not game.install_dir or not Path(game.install_dir).is_dir():
        return None
    found = sorted(Path(game.install_dir).glob("unins*.exe"))
    return {"name": game.name, "command": subprocess.list2cmdline([str(found[-1])])} if found else None


def _protected() -> set[str]:
    env = os.environ
    home = Path.home()
    paths = {env.get("SystemRoot"), env.get("ProgramFiles"), env.get("ProgramFiles(x86)"), env.get("ProgramData"),
             env.get("USERPROFILE"), env.get("PUBLIC"), env.get("APPDATA"), env.get("LOCALAPPDATA"),
             env.get("OneDrive"), str(home), str(home / "Desktop"), str(home / "Downloads"), str(home / "Documents"),
             str(home / "Videos"), str(home / "Pictures"), str(home / "Music"), str(Path(env.get("PUBLIC", "")) / "Desktop"),
             str(data_dir()), str(Path(env.get("LOCALAPPDATA", "")) / "Programs"),
             str(Path(env.get("LOCALAPPDATA", "")) / "Programs" / "GameDock")}
    return {_norm(p) for p in paths if p}


def _unsafe_reason(path: Path, game: Game) -> str | None:
    """Por que NÃO podemos mandar esta pasta/arquivo para a Lixeira (None = pode)."""
    norm = _norm(path)
    if not path.exists():
        return "Os arquivos do jogo não estão mais no disco."
    if len(path.parts) < 2 or path.parent == path:
        return f"{path} é a raiz de um disco."
    for protected in _protected():
        if norm == protected or _inside(protected, norm):
            return f"{path} é uma pasta do Windows ou do seu usuário."
    if path.is_dir() and path.name.lower() in LIBRARY_NAMES:
        return f"{path} parece uma pasta com vários jogos, não a pasta deste jogo."
    for other in Library().games.values():
        if other.id == game.id or not other.installed:
            continue
        other_dir = _norm(other.install_dir)
        if other_dir == norm:
            continue   # o mesmo jogo listado duas vezes (ex: com e sem carregador de mods)
        for value in (other.install_dir, other.launch_target if other.launch_type in ("exe", "emu") else None):
            if value and _inside(_norm(value), norm):
                return f"Dentro de {path} também está {other.display_name}."
    return None


def _emulated_files(game: Game) -> list[Path]:
    """ISO/ROM do jogo, mais as faixas .bin de um .cue e os discos de uma playlist .m3u."""
    from .emulators_retro import _cue_bins
    files: list[Path] = []

    def add(p: Path):
        if p.exists() and p not in files:
            files.append(p)
            if p.suffix.lower() == ".cue":
                for b in _cue_bins(p):
                    add(b)

    target = Path(game.launch_target)
    if target.suffix.lower() == ".m3u" and target.is_file():
        for line in target.read_text(errors="replace").splitlines():
            if line.strip() and not line.startswith("#"):
                add(target.parent / line.strip())
        add(target)
    if game.install_dir:
        add(Path(game.install_dir))
    elif target.exists():
        add(target)
    return files


def _game_folder(game: Game) -> Path | None:
    if game.install_dir and Path(game.install_dir).exists():
        return Path(game.install_dir)
    if game.exe and Path(game.exe).is_file():
        return install_root(Path(game.exe))
    if game.launch_type == "exe" and Path(game.launch_target).is_file():
        return install_root(Path(game.launch_target))
    return None


def _shortcuts_to(paths: list[Path]) -> list[Path]:
    """Atalhos da Área de Trabalho / Menu Iniciar que abrem algo dentro de `paths`."""
    roots = [_norm(p) for p in paths]
    found = []
    for directory in shortcut_dirs():
        if not directory.exists():
            continue
        for lnk in directory.rglob("*.lnk"):
            info = read_lnk(lnk) or {}
            pointed = [_norm(info.get("target"))] + [_norm(a) for a in _split_args(info.get("arguments") or "")]
            if any(_inside(p, r) for p in pointed for r in roots):
                found.append(lnk)
    return found


def plan(game: Game) -> dict:
    """O que "Desinstalar" vai fazer com este jogo. mode: store | uninstaller | recycle | none."""
    if game.source == "steam" and game.id.startswith("steam:"):
        return {"mode": "store", "text": "A Steam vai abrir a confirmação de desinstalação dela."}
    found = _registry_uninstaller(game) or (_folder_uninstaller(game) if game.source not in EMULATED else None)
    if found:
        return {"mode": "uninstaller", "command": found["command"], "key": found.get("key"),
                "text": f"Vai abrir o desinstalador de {found['name']}. Siga as etapas dele."}
    if game.source not in DELETABLE:
        launcher = {"epic": "Epic Games", "gog": "GOG Galaxy", "ubisoft": "Ubisoft Connect", "ea": "EA app",
                    "battlenet": "Battle.net", "google": "Google Play Games", "riot": "Riot Client"}.get(game.source, game.source)
        return {"mode": "none", "text": f"Esse jogo é do {launcher}: desinstale por lá."}
    paths = _emulated_files(game) if game.source in EMULATED else [p for p in [_game_folder(game)] if p]
    if not paths:
        return {"mode": "none", "text": "Os arquivos do jogo não estão mais no disco."}
    for path in paths:
        if reason := _unsafe_reason(path, game):
            return {"mode": "none", "text": f"Não vou apagar sozinho: {reason} Apague pelo Explorador de Arquivos."}
    from . import disk
    size = sum(disk._size(p) for p in paths)
    links = _shortcuts_to(paths)
    where = str(paths[0]) + (f" (+{len(paths) - 1} arquivo(s))" if len(paths) > 1 else "")
    extra = f" e {len(links)} atalho(s)" if links else ""
    return {"mode": "recycle", "paths": [str(p) for p in paths], "shortcuts": [str(l) for l in links], "size": size,
            "text": f"Vai mandar para a Lixeira {where} — {_fmt_size(size)}{extra}. Dá para restaurar pela Lixeira."}


def _fmt_size(size: int) -> str:
    return f"{size / 1e9:.1f} GB" if size >= 1e9 else f"{max(1, round(size / 1e6))} MB"


# ---------------------------------------------------------------- execução

class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]


FO_DELETE = 3
FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_WANTNUKEWARNING = 0x10, 0x40, 0x4000


def recycle(paths: list[str]) -> bool:
    """Manda para a Lixeira (com a janela de progresso do Windows). Se algum item não couber
    na Lixeira, o próprio Windows avisa que vai apagar de vez e pergunta."""
    if not paths:
        return True
    buffer = ctypes.create_unicode_buffer("\0".join(paths) + "\0\0")
    op = _SHFILEOPSTRUCTW(wFunc=FO_DELETE, pFrom=ctypes.cast(buffer, wintypes.LPCWSTR),
                          fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_WANTNUKEWARNING)
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    return result == 0 and not op.fAnyOperationsAborted and not any(Path(p).exists() for p in paths)


def _run_uninstaller(command: str) -> subprocess.Popen | None:
    try:
        return subprocess.Popen(command)
    except OSError as exc:
        if getattr(exc, "winerror", None) != ERROR_ELEVATION_REQUIRED:
            raise
    args = _split_args(command)   # pede administrador (UAC), como no "Aplicativos instalados"
    os.startfile(args[0], "runas", subprocess.list2cmdline(args[1:]), str(Path(args[0]).parent))
    return None


def _watch_path(game: Game) -> Path | None:
    """O que some quando o jogo foi desinstalado."""
    if game.source == "steam" and game.install_dir:
        appid = game.id.split(":", 1)[1]
        return Path(game.install_dir).parent.parent / f"appmanifest_{appid}.acf"
    install = _norm(game.install_dir)
    if game.exe and Path(game.exe).is_file() and (not install or _inside(_norm(game.exe), install)):
        return Path(game.exe)
    return Path(game.install_dir) if game.install_dir else None


def _key_exists(key) -> bool:
    if not key:
        return True
    try:
        winreg.CloseKey(winreg.OpenKey(key[0], key[1]))
        return True
    except OSError:
        return False


def wait_gone(game: Game, limit: float = WAIT_LIMIT, key=None) -> bool:
    """Espera o desinstalador terminar o serviço. Não dá para esperar o processo: muitos
    (Inno Setup) se copiam para a pasta temporária e o original fecha na hora. Então vigiamos
    o arquivo do jogo; se a pessoa cancelar, desistimos depois de `limit` segundos."""
    watch = _watch_path(game)
    deadline = time.time() + limit
    def gone() -> bool:   # arquivo do jogo sumiu, ou a entrada em "Aplicativos instalados" sumiu
        return watch is None or not watch.exists() or (key is not None and not _key_exists(key))
    while not gone() and time.time() < deadline:
        time.sleep(2)
    return gone()


def execute(game: Game, how: dict) -> dict:
    mode = how["mode"]
    if mode == "store":
        os.startfile(f"steam://uninstall/{game.id.split(':', 1)[1]}")
        return {"ok": True, "removed": wait_gone(game)}
    if mode == "uninstaller":
        _run_uninstaller(how["command"])
        return {"ok": True, "removed": wait_gone(game, key=how.get("key"))}
    if mode == "recycle":
        if not recycle(how["paths"]):
            return {"ok": False, "error": "A exclusão foi cancelada ou não terminou."}
        recycle([s for s in how.get("shortcuts", []) if Path(s).exists()])
        return {"ok": True, "removed": True}
    return {"ok": False, "error": how.get("text") or "Não sei desinstalar esse jogo."}

"""Capturas de tela: índice (galeria) + tecla de atalho global.

O índice (captures.json) junta as capturas manuais (tecla de atalho) e as automáticas de troféu.
Os arquivos ficam em art/<jogo>/captures/ e art/<jogo>/trophies/, que o servidor local já serve.
A tecla usa RegisterHotKey do Windows: funciona com qualquer janela em primeiro plano, inclusive o jogo.
"""
from __future__ import annotations

import ctypes
import json
import threading
import time
from ctypes import wintypes
from pathlib import Path

from .storage import _read_json, atomic_write, data_dir

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012
# nome mostrado nas configurações -> (modificadores, tecla virtual). F12 sozinho não entra: o Windows
# reserva essa tecla para depuradores e recusa o registro (assim como Alt/Ctrl+Print Screen).
HOTKEYS = {
    "Print Screen": (0, 0x2C),
    "Ctrl+F12": (MOD_CONTROL, 0x7B),
    "Alt+F12": (MOD_ALT, 0x7B),
    "F10": (0, 0x79),
    "F9": (0, 0x78),
    "Ctrl+Shift+S": (MOD_CONTROL | MOD_SHIFT, 0x53),
    "Alt+F10": (MOD_ALT, 0x79),
    "Ctrl+F10": (MOD_CONTROL, 0x79),
}
DEFAULT_CLIP_HOTKEY = "Alt+F10"
DEFAULT_HOTKEY = "Print Screen"
_LOCK = threading.Lock()


# ---------------------------------------------------------------- índice

def _index_path() -> Path:
    return data_dir() / "captures.json"


def _file_for(item: dict) -> Path | None:
    from .clips import CLIP_PREFIX, clip_file
    if item["path"].startswith(CLIP_PREFIX):
        return clip_file(item["path"])
    path = data_dir() / "art" / item["path"]
    return path if path.is_file() else None


def all_captures(include_clips: bool = True) -> list[dict]:
    with _LOCK:
        items = _read_json(_index_path(), {"captures": []})["captures"]
    if not include_clips:
        items = [c for c in items if c.get("kind") != "clip"]
    # apagadas pelo Explorer somem da galeria (mas clipes removidos continuam marcados, para não voltarem)
    return [c for c in items if not c.get("removed") and _file_for(c)]


def known_paths() -> set[str]:
    """Tudo que já passou pelo índice, inclusive clipes removidos (para não importá-los de novo)."""
    with _LOCK:
        return {c["path"] for c in _read_json(_index_path(), {"captures": []})["captures"]}


def add(game_id: str | None, rel_path: str, kind: str, trophy: str | None = None, ts: int | None = None) -> dict:
    item = {"id": f"{int(time.time() * 1000)}{len(rel_path)}", "game": game_id, "path": rel_path,
            "ts": ts or int(time.time()), "kind": kind, "trophy": trophy}
    with _LOCK:
        data = _read_json(_index_path(), {"captures": []})
        data["captures"].append(item)
        atomic_write(_index_path(), json.dumps(data, ensure_ascii=False, indent=1))
    return item


def _to_recycle_bin(path: Path) -> None:
    """Clipes ficam na pasta Vídeos do usuário: em vez de apagar de vez, vão para a Lixeira."""
    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_uint16), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT = 3, 0x40, 0x10, 0x4
    op = SHFILEOPSTRUCTW(None, FO_DELETE, str(path) + "\0", None, FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT)
    ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))


def remove(capture_id: str) -> bool:
    with _LOCK:
        data = _read_json(_index_path(), {"captures": []})
        gone = [c for c in data["captures"] if c["id"] == capture_id]
        for item in gone:
            path = _file_for(item)
            if item.get("kind") == "clip":
                item["removed"] = True      # marcado, para a importação não trazer de volta
                if path:
                    _to_recycle_bin(path)
            elif path:
                path.unlink(missing_ok=True)
        data["captures"] = [c for c in data["captures"] if c["id"] != capture_id or c.get("kind") == "clip"]
        atomic_write(_index_path(), json.dumps(data, ensure_ascii=False, indent=1))
    return bool(gone)


# ---------------------------------------------------------------- tecla de atalho

class Hotkey(threading.Thread):
    """Registra a tecla numa thread própria (o Windows entrega WM_HOTKEY na fila da thread que registrou)."""

    def __init__(self, name: str, callback, combo: tuple[int, int] | None = None):
        super().__init__(daemon=True, name=f"hotkey-{name}")
        self.key_name = name
        self.combo = combo or HOTKEYS[name]
        self.callback = callback
        self.thread_id = None
        self.registered = threading.Event()
        self.ok = False

    def run(self) -> None:
        self.thread_id = kernel32.GetCurrentThreadId()
        mods, vk = self.combo
        # falha se outro programa já usa a mesma tecla (ex: outro GameDock aberto)
        self.ok = bool(user32.RegisterHotKey(None, 1, mods | MOD_NOREPEAT, vk))
        self.registered.set()
        if not self.ok:
            return
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == WM_HOTKEY:
                threading.Thread(target=self.callback, daemon=True).start()
        user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self.thread_id:
            user32.PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)


def start_hotkey(name: str | None, callback) -> Hotkey | None:
    if not name or name not in HOTKEYS:
        return None
    hotkey = Hotkey(name, callback)
    hotkey.start()
    hotkey.registered.wait(2)
    return hotkey

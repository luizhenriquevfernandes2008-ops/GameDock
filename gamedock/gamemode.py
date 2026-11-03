"""Modo Jogo: enquanto um jogo está aberto, o Windows fica em desempenho máximo e quieto.

Ao abrir o primeiro jogo:
  - plano/modo de energia "Melhor desempenho" (overlay do Windows 10/11; senão o plano "Alto desempenho");
  - notificações do Windows pausadas (as "toasts"; o GameDock usa o próprio aviso por cima do jogo);
  - fecha os apps que o usuário escolheu (pedindo para fechar, como o X da janela — nada é forçado).
Ao fechar o último jogo, tudo volta como estava, e os apps fechados abrem de novo.
O estado fica num arquivo: se o GameDock cair no meio do jogo, ele desfaz tudo na próxima abertura.
"""
from __future__ import annotations

import ctypes
import json
import re
import subprocess
import time
import winreg
from ctypes import wintypes
from pathlib import Path

import psutil

from .launcher import clean_dll_search
from .storage import data_dir, load_config

CREATE_NO_WINDOW = 0x08000000
BEST_PERFORMANCE = "ded574b5-45a0-4f42-8737-46345c09c238"   # Configurações → Energia → "Melhor desempenho"
HIGH_PERFORMANCE_PLAN = "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"
TOASTS_KEY = r"Software\Microsoft\Windows\CurrentVersion\PushNotifications"
NEVER_CLOSE = {"explorer.exe", "gamedock.exe", "python.exe", "pythonw.exe", "steam.exe", "steamwebhelper.exe",
               "epicgameslauncher.exe", "riotclientservices.exe", "ds4windows.exe", "discord.exe", "rpcs3.exe",
               "pcsx2-qt.exe", "retroarch.exe", "dwm.exe", "csrss.exe", "winlogon.exe", "svchost.exe",
               "textinputhost.exe", "applicationframehost.exe", "sechealthui.exe", "systemsettings.exe",
               "shellexperiencehost.exe", "startmenuexperiencehost.exe", "searchhost.exe", "lockapp.exe"}


class GUID(ctypes.Structure):
    _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    @classmethod
    def parse(cls, text: str) -> "GUID":
        g = cls()
        ctypes.windll.ole32.CLSIDFromString("{" + text.strip("{}") + "}", ctypes.byref(g))
        return g

    def __str__(self) -> str:
        buf = ctypes.create_unicode_buffer(40)
        ctypes.windll.ole32.StringFromGUID2(ctypes.byref(self), buf, 40)
        return buf.value.strip("{}").lower()


def _state_file() -> Path:
    return data_dir() / "gamemode-state.json"


def _run(args: list[str]) -> str:
    return subprocess.run(args, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=15).stdout


# ---------------------------------------------------------------- energia

def _get_overlay() -> str | None:
    try:
        g = GUID()
        if ctypes.windll.powrprof.PowerGetEffectiveOverlayScheme(ctypes.byref(g)) == 0:
            return str(g)
    except (AttributeError, OSError):
        pass
    return None


def _set_overlay(guid: str) -> bool:
    try:
        return ctypes.windll.powrprof.PowerSetActiveOverlayScheme(GUID.parse(guid)) == 0
    except (AttributeError, OSError):
        return False


def _active_plan() -> str | None:
    m = re.search(r"([0-9a-f]{8}-[0-9a-f-]{27})", _run(["powercfg", "/getactivescheme"]), re.I)
    return m.group(1).lower() if m else None


# ---------------------------------------------------------------- notificações

def _toasts() -> int | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, TOASTS_KEY) as key:
            return winreg.QueryValueEx(key, "ToastEnabled")[0]
    except OSError:
        return None


def _set_toasts(value: int | None) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, TOASTS_KEY) as key:
        if value is None:
            try:
                winreg.DeleteValue(key, "ToastEnabled")
            except OSError:
                pass
        else:
            winreg.SetValueEx(key, "ToastEnabled", 0, winreg.REG_DWORD, value)


# ---------------------------------------------------------------- apps

def running_apps() -> list[dict]:
    """Apps com janela abertos agora (para o usuário escolher quais fechar no Modo Jogo)."""
    user32 = ctypes.windll.user32
    pids: set[int] = set()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if user32.IsWindowVisible(hwnd) and user32.GetWindowTextLengthW(hwnd) and not user32.GetWindow(hwnd, 4):
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            pids.add(pid.value)
        return True

    user32.EnumWindows(each, 0)
    apps = {}
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            name = proc.name().lower()
            if name in NEVER_CLOSE:
                continue
            apps[name] = {"name": name, "title": Path(proc.exe()).stem, "memory": proc.memory_info().rss}
        except (psutil.Error, OSError):
            continue
    return sorted(apps.values(), key=lambda a: -a["memory"])


def _close_apps(names: list[str]) -> list[dict]:
    closed = []
    wanted = {n.lower() for n in names} - NEVER_CLOSE
    for proc in psutil.process_iter(["name", "exe", "cmdline"]):
        if (proc.info["name"] or "").lower() not in wanted:
            continue
        try:
            entry = {"name": proc.info["name"], "exe": proc.info["exe"], "args": (proc.info["cmdline"] or [])[1:]}
            _run(["taskkill", "/PID", str(proc.pid)])   # sem /F: pede para fechar, como o X da janela
            if entry["exe"] and not any(c["exe"] == entry["exe"] for c in closed):
                closed.append(entry)
        except (psutil.Error, OSError, subprocess.SubprocessError):
            continue
    return closed


def _reopen(apps: list[dict]) -> None:
    for app in apps:
        if not app.get("exe") or not Path(app["exe"]).is_file():
            continue
        if any((p.info["exe"] or "").lower() == app["exe"].lower() for p in psutil.process_iter(["exe"])):
            continue   # já foi aberto de novo pelo usuário
        try:
            with clean_dll_search():
                subprocess.Popen([app["exe"], *app.get("args", [])], cwd=str(Path(app["exe"]).parent))
        except OSError:
            pass


# ---------------------------------------------------------------- ligar / desligar

def enabled() -> bool:
    return bool(load_config().get("game_mode", False))


def activate() -> dict:
    if _state_file().is_file():
        return json.loads(_state_file().read_text(encoding="utf-8"))
    config = load_config()
    state = {"at": time.time(), "overlay": _get_overlay(), "plan": None, "toasts": _toasts(), "toasts_changed": False,
             "closed": []}
    if not _set_overlay(BEST_PERFORMANCE):
        state["plan"] = _active_plan()
        _run(["powercfg", "/setactive", HIGH_PERFORMANCE_PLAN])
    if config.get("game_mode_quiet", True):
        _set_toasts(0)
        state["toasts_changed"] = True
    state["closed"] = _close_apps(config.get("game_mode_close") or [])
    _state_file().write_text(json.dumps(state), encoding="utf-8")
    return state


def deactivate() -> None:
    path = _state_file()
    if not path.is_file():
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        path.unlink(missing_ok=True)
        return
    if state.get("overlay"):
        _set_overlay(state["overlay"])
    if state.get("plan"):
        _run(["powercfg", "/setactive", state["plan"]])
    if state.get("toasts_changed"):
        _set_toasts(state.get("toasts"))
    if load_config().get("game_mode_reopen", True):
        _reopen(state.get("closed") or [])
    path.unlink(missing_ok=True)


def active() -> bool:
    return _state_file().is_file()

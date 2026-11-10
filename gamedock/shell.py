"""A "casca" de app: instância única, ícone na bandeja e iniciar com o Windows.

- Instância única: um mutex do Windows. Se o GameDock já está aberto, a segunda cópia só pede
  (pelo servidor local da primeira) para a janela aparecer e sai.
- Bandeja: fechar a janela não encerra o app; ele continua contando tempo, vigiando troféus e
  aceitando capturas. O menu tem os jogos recentes, captura e "Sair".
- Iniciar com o Windows: chave Run do usuário (não precisa de administrador), abrindo direto na bandeja.
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import sys
import urllib.request
import winreg
from pathlib import Path

from .storage import data_dir

MUTEX_NAME = "Local\\GameDock.SingleInstance"
ERROR_ALREADY_EXISTS = 183
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "GameDock"
SHOW_PATH = "/__gamedock/show"
QUIT_PATH = "/__gamedock/quit"

kernel32 = ctypes.windll.kernel32
kernel32.CreateMutexW.restype = ctypes.c_void_p
kernel32.OpenProcess.restype = ctypes.c_void_p
kernel32.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
_mutex = None


# ---------------------------------------------------------------- instância única

def acquire_single_instance() -> bool:
    """True se esta é a primeira instância (o mutex fica preso até o processo acabar)."""
    global _mutex
    _mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    return kernel32.GetLastError() != ERROR_ALREADY_EXISTS


def _instance_file() -> Path:
    return data_dir() / "instance.json"


_token = secrets.token_hex(16)


def publish_instance(port: int) -> None:
    _instance_file().write_text(json.dumps({"port": port, "pid": os.getpid(), "token": _token}), encoding="utf-8")


def valid_token(path: str) -> bool:
    from urllib.parse import parse_qs, urlsplit
    sent = parse_qs(urlsplit(path).query).get("token", [""])[0]
    return secrets.compare_digest(sent, _token)


def request_quit(timeout: float = 25) -> bool:
    """Pede ao GameDock aberto para fechar normalmente (salva tempo de jogo, janela...) e espera
    o processo sair. False se não havia como pedir ou ele não fechou a tempo."""
    import time
    try:
        info = json.loads(_instance_file().read_text(encoding="utf-8"))
        urllib.request.urlopen(f"http://127.0.0.1:{info['port']}{QUIT_PATH}?token={info['token']}", timeout=5).read()
    except (OSError, ValueError, KeyError):
        return False
    pid = int(info.get("pid") or 0)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.3)
    return False


def _pid_alive(pid: int) -> bool:
    SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(handle)


def wake_existing_instance() -> bool:
    try:
        port = json.loads(_instance_file().read_text(encoding="utf-8"))["port"]
        urllib.request.urlopen(f"http://127.0.0.1:{port}{SHOW_PATH}", timeout=3).read()
        return True
    except (OSError, ValueError, KeyError):
        return False


# ---------------------------------------------------------------- iniciar com o Windows

def launch_command(tray: bool = True) -> str:
    if getattr(sys, "frozen", False):
        cmd = f'"{sys.executable}"'
    else:  # rodando pelo código-fonte
        script = Path(__file__).resolve().parent.parent / "run_gamedock.py"
        cmd = f'"{Path(sys.executable).with_name("pythonw.exe")}" "{script}"'
    from .storage import load_config
    if load_config().get("console_mode"):   # Modo Console: o PC liga direto no GameDock em tela cheia
        return cmd + " --console"
    return cmd + (" --tray" if tray else "")


def autostart_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
            return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, launch_command(tray=True))
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass


# ---------------------------------------------------------------- bandeja

def tray_image(size: int = 64):
    """A marca do GameDock (▶ + doca colorida) desenhada com o Pillow."""
    from PIL import Image, ImageDraw
    scale = 4
    s = size * scale
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * .22), fill=(12, 12, 16, 255))
    d.polygon([(s * .38, s * .2), (s * .38, s * .62), (s * .73, s * .41)], fill=(244, 244, 246, 255))
    stops = ((255, 61, 129), (255, 178, 61), (61, 214, 255))
    x0, x1, y0, y1 = int(s * .18), int(s * .82), int(s * .72), int(s * .82)
    bar = Image.new("RGBA", (x1 - x0, y1 - y0))
    for x in range(bar.width):
        t = x / max(1, bar.width - 1)
        a, b, k = (stops[0], stops[1], t * 2) if t < .5 else (stops[1], stops[2], (t - .5) * 2)
        color = tuple(int(a[i] + (b[i] - a[i]) * k) for i in range(3)) + (255,)
        ImageDraw.Draw(bar).line([(x, 0), (x, bar.height)], fill=color)
    mask = Image.new("L", bar.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, bar.width - 1, bar.height - 1), radius=bar.height // 2, fill=255)
    img.paste(bar, (x0, y0), mask)
    return img.resize((size, size), Image.LANCZOS)


class Tray:
    def __init__(self, on_open, on_quit, on_capture, recent_games, on_play):
        import pystray
        self._pystray = pystray
        self.recent_games = recent_games      # () -> [(id, nome)]
        self.on_play = on_play
        item = pystray.MenuItem
        menu = pystray.Menu(
            item("Abrir GameDock", lambda: on_open(), default=True),
            pystray.Menu.SEPARATOR,
            item("Jogar", pystray.Menu(self._recent_items)),
            item("Capturar tela", lambda: on_capture()),
            pystray.Menu.SEPARATOR,
            item("Iniciar com o Windows", lambda: set_autostart(not autostart_enabled()),
                 checked=lambda _: autostart_enabled()),
            item("Sair do GameDock", lambda: on_quit()),
        )
        self.icon = pystray.Icon("GameDock", tray_image(), "GameDock", menu)

    def _recent_items(self):
        item = self._pystray.MenuItem
        games = self.recent_games()
        if not games:
            return (item("Nenhum jogo ainda", None, enabled=False),)
        return tuple(item(name, (lambda gid: lambda: self.on_play(gid))(gid)) for gid, name in games)

    def start(self) -> None:
        self.icon.run_detached()

    def notify(self, message: str) -> None:
        try:
            self.icon.notify(message, "GameDock")
        except Exception:
            pass

    def stop(self) -> None:
        try:
            self.icon.stop()
        except Exception:
            pass

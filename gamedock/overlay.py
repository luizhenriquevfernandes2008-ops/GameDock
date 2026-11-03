"""Aviso de troféu por cima do jogo + captura de tela.

A janela do aviso é criada junto com o app, *visível mas fora da tela* (OFFSCREEN), e é trazida
para o canto com chamadas diretas ao Windows que não ativam a janela — um jogo em tela cheia que
perde o foco minimiza. (Criada escondida, o WebView2 acha que está invisível e não desenha nada.)
"""
from __future__ import annotations

import ctypes
import json
import queue
import threading
import time
from ctypes import wintypes
from pathlib import Path

user32 = ctypes.windll.user32
dwmapi = ctypes.windll.dwmapi

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW, WS_EX_NOACTIVATE = 0x8, 0x80, 0x40000, 0x08000000
HWND_TOPMOST = -1
SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x10, 0x40
DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2

WIDTH, HEIGHT, MARGIN = 440, 112, 28   # pixels lógicos (multiplicados pela escala do monitor)
SHOW_SECONDS = 6.5
OFFSCREEN = -32000                      # onde a janela do aviso fica "estacionada"

user32.GetForegroundWindow.restype = wintypes.HWND
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, wintypes.UINT)
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int)
user32.SetWindowLongPtrW.argtypes = (wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t)


def _dotnet():
    import clr  # pythonnet, já carregado pelo pywebview
    clr.AddReference("System.Drawing")
    clr.AddReference("System.Windows.Forms")
    import System
    import System.Drawing as Drawing
    import System.Drawing.Imaging as Imaging
    from System.Windows.Forms import Screen
    return System, Drawing, Imaging, Screen


def game_screen_bounds() -> tuple[int, int, int, int]:
    """(x, y, largura, altura) em pixels reais do monitor onde está a janela em primeiro plano."""
    System, _, _, Screen = _dotnet()
    bounds = Screen.FromHandle(System.IntPtr(user32.GetForegroundWindow() or 0)).Bounds
    return bounds.X, bounds.Y, bounds.Width, bounds.Height


def capture_screen(dest: Path) -> bool:
    """Captura o monitor do jogo em JPEG. Jogos em tela cheia *exclusiva* podem sair pretos
    (o Windows não deixa copiar a imagem deles); em janela sem borda funciona sempre."""
    try:
        System, Drawing, Imaging, _ = _dotnet()
        x, y, w, h = game_screen_bounds()
        bitmap = Drawing.Bitmap(w, h)
        graphics = Drawing.Graphics.FromImage(bitmap)
        graphics.CopyFromScreen(x, y, 0, 0, Drawing.Size(w, h))
        graphics.Dispose()
        codec = next(c for c in Imaging.ImageCodecInfo.GetImageEncoders() if c.MimeType == "image/jpeg")
        params = Imaging.EncoderParameters(1)
        params.Param[0] = Imaging.EncoderParameter(Imaging.Encoder.Quality, System.Int64(90))
        dest.parent.mkdir(parents=True, exist_ok=True)
        bitmap.Save(str(dest), codec, params)
        bitmap.Dispose()
        return dest.is_file()
    except Exception as exc:
        print("captura falhou:", exc)
        return False


def style_overlay(hwnd: int) -> None:
    """Fora da barra de tarefas/Alt+Tab, nunca ativável (não tira o foco do jogo), cantos arredondados."""
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_TOPMOST) & ~WS_EX_APPWINDOW
    user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex)
    corner = ctypes.c_int(DWMWCP_ROUND)
    dwmapi.DwmSetWindowAttribute(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, ctypes.byref(corner), 4)


class GamePanel:
    """Painel do Alt+G: aparece na lateral direita do monitor do jogo, sem tirar o foco dele."""

    WIDTH, MAX_HEIGHT, MARGIN = 400, 720, 24

    def __init__(self, window):
        self.window = window
        self.visible = False
        self.lock = threading.Lock()
        window.events.shown += lambda: style_overlay(self._hwnd())

    def _hwnd(self) -> int:
        return int(self.window.native.Handle.ToInt64())

    def toggle(self) -> None:
        (self.hide if self.visible else self.show)()

    def show(self) -> None:
        with self.lock:
            hwnd = self._hwnd()
            style_overlay(hwnd)
            scale = user32.GetDpiForWindow(hwnd) / 96 or 1
            x, y, w, h = game_screen_bounds()
            margin = int(self.MARGIN * scale)
            width = int(self.WIDTH * scale)
            height = min(int(self.MAX_HEIGHT * scale), h - 2 * margin)
            self.window.evaluate_js("openPanel()")
            user32.SetWindowPos(hwnd, HWND_TOPMOST, x + w - width - margin, y + (h - height) // 2, width, height,
                                SWP_NOACTIVATE | SWP_SHOWWINDOW)
            self.visible = True

    def hide(self) -> None:
        with self.lock:
            if not self.visible:
                return
            self.window.evaluate_js("closePanel()")
            time.sleep(0.2)
            user32.SetWindowPos(self._hwnd(), HWND_TOPMOST, OFFSCREEN, OFFSCREEN, 10, 10, SWP_NOACTIVATE)
            self.visible = False


class TrophyOverlay:
    """Fila de avisos: vários troféus de uma vez aparecem um depois do outro."""

    def __init__(self, window):
        self.window = window
        self.queue: queue.Queue = queue.Queue()
        self._styled = False
        # tira da barra de tarefas/Alt+Tab assim que a janela existir
        window.events.shown += lambda: self._style(self._hwnd())
        threading.Thread(target=self._worker, daemon=True, name="trophy-overlay").start()

    def show(self, payload: dict) -> None:
        self.queue.put(payload)

    def _hwnd(self) -> int:
        return int(self.window.native.Handle.ToInt64())

    def _style(self, hwnd: int) -> None:
        if not self._styled:
            style_overlay(hwnd)
            self._styled = True

    def _worker(self) -> None:
        while True:
            payload = self.queue.get()
            try:
                hwnd = self._hwnd()
                self._style(hwnd)
                scale = user32.GetDpiForWindow(hwnd) / 96 or 1
                x, y, w, _ = game_screen_bounds()
                width, height, margin = int(WIDTH * scale), int(HEIGHT * scale), int(MARGIN * scale)
                self.window.evaluate_js(f"showTrophy({json.dumps(payload)})")
                user32.SetWindowPos(hwnd, HWND_TOPMOST, x + w - width - margin, y + margin, width, height,
                                    SWP_NOACTIVATE | SWP_SHOWWINDOW)
                time.sleep(SHOW_SECONDS)
                self.window.evaluate_js("hideTrophy()")
                time.sleep(0.45)  # tempo da animação de saída
                user32.SetWindowPos(hwnd, HWND_TOPMOST, OFFSCREEN, OFFSCREEN, width, height, SWP_NOACTIVATE)
            except Exception as exc:
                print("aviso de troféu falhou:", exc)

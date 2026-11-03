"""Instalador (GameDock-Setup.exe) e desinstalação (GameDock.exe --uninstall). Tudo por usuário, sem administrador.

Instalar: copia o app para %LOCALAPPDATA%\\Programs\\GameDock, cria atalhos (Menu Iniciar e, se quiser,
Área de Trabalho), registra em "Configurações → Aplicativos" e, se quiser, inicia com o Windows.
Desinstalar: desfaz tudo isso; os dados (troféus, capturas, perfil) só são apagados se o usuário pedir.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

from . import __version__

APP_NAME = "GameDock"
INSTALL_DIR = Path(os.environ["LOCALAPPDATA"]) / "Programs" / APP_NAME
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\GameDock"
START_MENU = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
MB_YESNO, MB_ICONQUESTION, MB_ICONINFO, MB_DEFBUTTON2, IDYES = 0x4, 0x20, 0x40, 0x100, 6
CREATE_NO_WINDOW = 0x08000000


def _message(text: str, flags: int = MB_ICONINFO) -> int:
    return ctypes.windll.user32.MessageBoxW(None, text, APP_NAME, flags)


def _desktop() -> Path:
    from ctypes import wintypes
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf)  # CSIDL_DESKTOPDIRECTORY
    return Path(buf.value)


def _shortcut(path: Path, target: Path) -> None:
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');$s.TargetPath='{target}';"
          f"$s.WorkingDirectory='{target.parent}';$s.IconLocation='{target},0';"
          "$s.Description='GameDock - todos os seus jogos num só lugar';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                   capture_output=True, creationflags=CREATE_NO_WINDOW, timeout=30)


def _running_gamedocks() -> list[int]:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq GameDock.exe", "/FO", "CSV", "/NH"],
                         capture_output=True, text=True, creationflags=CREATE_NO_WINDOW).stdout
    return [int(line.split('","')[1]) for line in out.splitlines() if line.startswith('"GameDock.exe"')]


def _folder_size_kb(folder: Path) -> int:
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) // 1024


# ---------------------------------------------------------------- instalar

def install(payload: Path, desktop_shortcut: bool, autostart: bool | None, log=print) -> Path:
    """autostart=None mantém como estava (atualização silenciosa)."""
    log("Copiando arquivos…")
    if INSTALL_DIR.exists():
        shutil.rmtree(INSTALL_DIR)
    shutil.copytree(payload, INSTALL_DIR)
    exe = INSTALL_DIR / "GameDock.exe"

    log("Criando atalhos…")
    _shortcut(START_MENU / "GameDock.lnk", exe)
    if desktop_shortcut:
        _shortcut(_desktop() / "GameDock.lnk", exe)
    old_copy = _desktop() / "GameDock.exe"  # versões antigas copiavam o .exe direto na Área de Trabalho
    old_copy.unlink(missing_ok=True)

    log("Registrando em Aplicativos…")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in (("DisplayName", APP_NAME), ("DisplayVersion", __version__), ("Publisher", APP_NAME),
                            ("DisplayIcon", f"{exe},0"), ("InstallLocation", str(INSTALL_DIR)),
                            ("UninstallString", f'"{exe}" --uninstall')):
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
        for name, value in (("NoModify", 1), ("NoRepair", 1), ("EstimatedSize", _folder_size_kb(INSTALL_DIR))):
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)

    from .shell import RUN_KEY, RUN_NAME
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if autostart is None:
            pass
        elif autostart:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, f'"{exe}" --tray')
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except OSError:
                pass
    log("Pronto!")
    return exe


def silent_update(payload: Path) -> None:
    """GameDock-Setup.exe --silent (atualização automática): espera o GameDock fechar, instala e abre de novo."""
    import time
    deadline = time.time() + 60
    while _running_gamedocks() and time.time() < deadline:
        time.sleep(0.5)
    if _running_gamedocks():
        _message("O GameDock não fechou, então a atualização não foi instalada. Feche o GameDock e tente de novo.")
        return
    exe = install(payload, desktop_shortcut=(_desktop() / "GameDock.lnk").exists(), autostart=None, log=lambda _: None)
    subprocess.Popen([str(exe)], cwd=str(exe.parent))


def setup_main() -> None:
    """Janela do GameDock-Setup.exe."""
    payload = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent / "dist")) / "payload"
    if "--silent" in sys.argv:
        return silent_update(payload)
    import tkinter as tk
    bg, fg, muted, accent = "#0c0c10", "#f4f4f6", "#9a9aa6", "#8b7bff"

    root = tk.Tk()
    root.title("Instalar GameDock")
    root.geometry("520x380")
    root.resizable(False, False)
    root.configure(bg=bg)
    try:
        from PIL import ImageTk
        from .shell import tray_image
        logo = ImageTk.PhotoImage(tray_image(72))
        root.iconphoto(True, ImageTk.PhotoImage(tray_image(32)))
        tk.Label(root, image=logo, bg=bg).pack(pady=(34, 10))
    except Exception:
        tk.Label(root, text="▶", font=("Segoe UI", 40), fg=fg, bg=bg).pack(pady=(30, 4))

    tk.Label(root, text="GAMEDOCK", font=("Segoe UI Variable Display", 18, "bold"), fg=fg, bg=bg).pack()
    tk.Label(root, text=f"Todos os seus jogos num só lugar · versão {__version__}", font=("Segoe UI", 10),
             fg=muted, bg=bg).pack(pady=(2, 18))

    desktop = tk.BooleanVar(value=True)
    autostart = tk.BooleanVar(value=False)
    opts = dict(bg=bg, fg=fg, selectcolor="#1e1e26", activebackground=bg, activeforeground=fg,
                font=("Segoe UI", 10), anchor="w", highlightthickness=0, bd=0)
    tk.Checkbutton(root, text="Criar atalho na Área de Trabalho", variable=desktop, **opts).pack(fill="x", padx=110)
    tk.Checkbutton(root, text="Iniciar com o Windows (na bandeja)", variable=autostart, **opts).pack(fill="x", padx=110, pady=(4, 0))

    status = tk.Label(root, text=f"Será instalado em {INSTALL_DIR}", font=("Segoe UI", 9), fg=muted, bg=bg, wraplength=460)
    status.pack(pady=(18, 10))

    def log(text):
        status.config(text=text)
        root.update()

    def run_install():
        running = _running_gamedocks()
        if running:
            answer = _message("O GameDock está aberto. Fechar e continuar a instalação?\n"
                              "(Seus troféus, capturas e perfil não são apagados.)", MB_YESNO | MB_ICONQUESTION)
            if answer != IDYES:
                return
            for pid in running:
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        button.config(state="disabled", text="Instalando…")
        try:
            exe = install(payload, desktop.get(), autostart.get(), log)
        except Exception as exc:
            log(f"Não foi possível instalar: {exc}")
            button.config(state="normal", text="Tentar de novo")
            return
        button.config(state="normal", text="Abrir GameDock",
                      command=lambda: (subprocess.Popen([str(exe)], cwd=str(exe.parent)), root.destroy()))

    button = tk.Button(root, text="Instalar", command=run_install, bg=accent, fg="#0a0a0d", activebackground="#a99bff",
                       font=("Segoe UI", 11, "bold"), relief="flat", bd=0, padx=34, pady=8, cursor="hand2")
    button.pack()
    root.mainloop()


# ---------------------------------------------------------------- desinstalar

def uninstall() -> None:
    if _message("Desinstalar o GameDock deste computador?", MB_YESNO | MB_ICONQUESTION) != IDYES:
        return
    me = os.getpid()
    for pid in _running_gamedocks():
        if pid != me:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)

    from .shell import set_autostart
    set_autostart(False)
    for link in (START_MENU / "GameDock.lnk", _desktop() / "GameDock.lnk"):
        link.unlink(missing_ok=True)
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass

    data = Path(os.environ["APPDATA"]) / "GameDock"
    remove_data = data.exists() and _message(
        "Apagar também seus dados (troféus, tempo de jogo, capturas e perfil)?\n\n"
        "Escolha \"Não\" se pretende instalar de novo depois.", MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2) == IDYES

    # o .exe em execução não pode apagar a própria pasta: um cmd separado espera ele fechar e apaga
    targets = [f'"{INSTALL_DIR}"'] + ([f'"{data}"'] if remove_data else [])
    command = "ping 127.0.0.1 -n 3 > nul & " + " & ".join(f"rmdir /s /q {t}" for t in targets)
    subprocess.Popen(["cmd", "/c", command], creationflags=CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS, close_fds=True)
    _message("GameDock desinstalado." + ("" if remove_data else "\nSeus dados foram mantidos."))

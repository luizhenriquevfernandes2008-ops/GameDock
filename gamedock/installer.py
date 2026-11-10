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


def _shortcut(path: Path, target: Path, description: str = "GameDock - todos os seus jogos num só lugar") -> None:
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{path}');$s.TargetPath='{target}';"
          f"$s.WorkingDirectory='{target.parent}';$s.IconLocation='{target},0';"
          f"$s.Description='{description}';$s.Save()")
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
    from .shell import request_quit
    if _running_gamedocks():
        request_quit()   # fecha com calma (o auto-update já fecha sozinho; isto cobre instalação manual)
    deadline = time.time() + 60
    while _running_gamedocks() and time.time() < deadline:
        time.sleep(0.5)
    if _running_gamedocks():
        _message("O GameDock não fechou, então a atualização não foi instalada. Feche o GameDock e tente de novo.")
        return
    exe = install(payload, desktop_shortcut=(_desktop() / "GameDock.lnk").exists(), autostart=None, log=lambda _: None)
    subprocess.Popen([str(exe)], cwd=str(exe.parent))


EXTRA_EMULATORS = (("ps3", "PS3 · RPCS3 + firmware oficial da Sony"), ("ps2", "PS2 · PCSX2"),
                   ("ps1", "PS1 · DuckStation"), ("nes", "NES · RetroArch"))


def setup_main() -> None:
    """Janela do GameDock-Setup.exe."""
    payload = Path(getattr(sys, "_MEIPASS", Path(__file__).parent.parent / "dist")) / "payload"
    if "--silent" in sys.argv:
        return silent_update(payload)
    import queue
    import threading
    import tkinter as tk
    from tkinter import filedialog

    from . import extras
    from .emulators import EMULATORS
    from .launcher import clean_dll_search
    bg, fg, muted, accent = "#0c0c10", "#f4f4f6", "#9a9aa6", "#8b7bff"

    root = tk.Tk()
    root.title("Instalar GameDock")
    root.geometry("560x680")
    root.resizable(False, False)
    root.configure(bg=bg)
    try:
        from PIL import ImageTk
        from .shell import tray_image
        logo = ImageTk.PhotoImage(tray_image(56))
        root.iconphoto(True, ImageTk.PhotoImage(tray_image(32)))
        tk.Label(root, image=logo, bg=bg).pack(pady=(24, 6))
    except Exception:
        tk.Label(root, text="▶", font=("Segoe UI", 34), fg=fg, bg=bg).pack(pady=(20, 2))

    tk.Label(root, text="GAMEDOCK", font=("Segoe UI Variable Display", 18, "bold"), fg=fg, bg=bg).pack()
    tk.Label(root, text=f"Todos os seus jogos num só lugar · versão {__version__}", font=("Segoe UI", 10),
             fg=muted, bg=bg).pack(pady=(2, 8))

    opts = dict(bg=bg, fg=fg, selectcolor="#1e1e26", activebackground=bg, activeforeground=fg,
                font=("Segoe UI", 10), anchor="w", highlightthickness=0, bd=0)

    def section(title, hint=None):
        tk.Label(root, text=title.upper(), font=("Segoe UI", 8, "bold"), fg=accent, bg=bg, anchor="w").pack(fill="x", padx=70, pady=(12, 2))
        if hint:
            tk.Label(root, text=hint, font=("Segoe UI", 8), fg=muted, bg=bg, anchor="w", justify="left",
                     wraplength=420).pack(fill="x", padx=70, pady=(0, 2))

    def check(text, value=False):
        var = tk.BooleanVar(value=value)
        tk.Checkbutton(root, text=text, variable=var, **opts).pack(fill="x", padx=70)
        return var

    section("GameDock")
    desktop = check("Criar atalho na Área de Trabalho", True)
    autostart = check("Iniciar com o Windows (na bandeja)")

    section("Emuladores (opcional)", "Baixados dos sites oficiais e já configurados com o seu controle. "
                                     "Os jogos são cópias dos seus próprios discos.")
    emus = {emu_id: check(label + ("  (já instalado)" if EMULATORS[emu_id].installed() else ""))
            for emu_id, label in EXTRA_EMULATORS}

    bios_found: dict = {}
    bios_row = tk.Frame(root, bg=bg)
    bios_row.pack(fill="x", padx=70, pady=(6, 0))
    bios_label = tk.Label(bios_row, text="PS1 e PS2 precisam do BIOS copiado do seu console. O GameDock não baixa BIOS.",
                          font=("Segoe UI", 8), fg=muted, bg=bg, anchor="w", justify="left", wraplength=250)

    def pick_bios():
        folder = filedialog.askdirectory(title="Pasta onde estão os BIOS do seu PS1/PS2")
        if not folder:
            return
        bios_found.clear()
        bios_found.update(extras.find_bios(Path(folder)))
        names = [f"{c.upper()}: {', '.join(p.name for p in files)}" for c, files in bios_found.items() if files]
        bios_label.config(text="Encontrei " + " · ".join(names) if names else
                          "Nenhum BIOS nessa pasta (PS1 = 512 KB, PS2 = 4 MB, arquivos .bin/.rom).",
                          fg=fg if names else "#ff8a8a")

    tk.Button(bios_row, text="Escolher pasta dos BIOS…", command=pick_bios, bg="#1e1e26", fg=fg, activebackground="#2a2a34",
              activeforeground=fg, font=("Segoe UI", 9), relief="flat", bd=0, padx=10, pady=4, cursor="hand2").pack(side="left")
    bios_label.pack(side="left", padx=(10, 0), fill="x")

    section("Controle")
    ds4 = check("DS4Windows · controle de PlayStation no PC" +
                ("  (já instalado)" if extras.ds4windows_exe().is_file() else ""))

    status = tk.Label(root, text=f"Será instalado em {INSTALL_DIR}", font=("Segoe UI", 9), fg=muted, bg=bg, wraplength=460)
    status.pack(pady=(16, 10))

    events: queue.Queue = queue.Queue()

    def work(choice):
        log = lambda text: events.put(("log", text))
        errors, ds4_exe = [], None
        try:
            exe = install(payload, choice["desktop"], choice["autostart"], log)
        except Exception as exc:
            events.put(("failed", exc))
            return
        for emu_id in choice["emus"]:
            try:
                extras.install_emulator(emu_id, log)
            except Exception as exc:
                errors.append(f"{EMULATORS[emu_id].name}: {exc}")
        if choice["bios"]:
            log("Copiando os BIOS…")
            try:
                extras.import_bios(choice["bios"])
            except Exception as exc:
                errors.append(f"BIOS: {exc}")
        if choice["ds4"]:
            try:
                ds4_exe = extras.install_ds4windows(log)
            except Exception as exc:
                errors.append(f"DS4Windows: {exc}")
        events.put(("done", exe, errors, ds4_exe))

    def poll():
        try:
            while True:
                event = events.get_nowait()
                if event[0] == "log":
                    status.config(text=event[1], fg=muted)
                elif event[0] == "failed":
                    status.config(text=f"Não foi possível instalar: {event[1]}", fg="#ff8a8a")
                    button.config(state="normal", text="Tentar de novo")
                    return
                else:
                    finish(*event[1:])
                    return
        except queue.Empty:
            pass
        root.after(120, poll)

    def finish(exe, errors, ds4_exe):
        text = "Pronto!"
        if ds4_exe:
            text += " Na primeira vez, o DS4Windows pede para instalar o driver ViGEmBus: aceite."
        if errors:
            text = "Instalado, mas alguns extras falharam (dá para tentar de novo em Configurações → Emuladores):\n" + "\n".join(errors)
        status.config(text=text, fg="#ffb36b" if errors else fg)
        root.protocol("WM_DELETE_WINDOW", root.destroy)

        def open_apps():
            with clean_dll_search():
                subprocess.Popen([str(exe)], cwd=str(exe.parent))
                if ds4_exe:
                    subprocess.Popen([str(ds4_exe)], cwd=str(ds4_exe.parent))
            root.destroy()

        button.config(state="normal", text="Abrir GameDock", command=open_apps)

    def run_install():
        running = _running_gamedocks()
        if running:
            answer = _message("O GameDock está aberto. Fechar e continuar a instalação?\n"
                              "(Seus troféus, capturas e perfil não são apagados.)", MB_YESNO | MB_ICONQUESTION)
            if answer != IDYES:
                return
            from .shell import request_quit
            request_quit()   # primeiro pede para fechar normalmente; só força se não fechar
            for pid in _running_gamedocks():
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, creationflags=CREATE_NO_WINDOW)
        choice = {"desktop": desktop.get(), "autostart": autostart.get(),
                  "emus": [emu_id for emu_id, var in emus.items() if var.get()],
                  "bios": {c: files for c, files in bios_found.items() if files}, "ds4": ds4.get()}
        button.config(state="disabled", text="Instalando…")
        root.protocol("WM_DELETE_WINDOW", lambda: None)   # fechar no meio deixaria a instalação pela metade
        threading.Thread(target=work, args=(choice,), daemon=True).start()
        poll()

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

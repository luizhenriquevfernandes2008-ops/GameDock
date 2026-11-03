"""Extras que o instalador oferece: emuladores, o BIOS do próprio usuário e o DS4Windows, tudo de uma vez.

- Emuladores: o mesmo download/configuração das Configurações do app (emulators.py); o PS3 já leva o
  firmware oficial da Sony.
- BIOS do PS1/PS2: o GameDock NÃO baixa BIOS (é o software da Sony; só vale a cópia do seu próprio
  console). O usuário escolhe a pasta onde guardou os dele e aqui eles são reconhecidos pelo tamanho
  e copiados para o emulador certo.
- DS4Windows: versão fixa do fork do schmaldeo (o repositório original saiu do ar e a busca do GitHub está
  cheia de cópias com vírus), conferida pelo SHA-256 abaixo. Ele precisa do .NET 8 Desktop Runtime
  (baixado da Microsoft se faltar) e, na primeira abertura, pede para instalar o driver ViGEmBus.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

from .emulators import EMU_ROOT, EMULATORS, _download, extract
from .launcher import clean_dll_search

DS4_VERSION = "3.9.9"
DS4_URL = f"https://github.com/schmaldeo/DS4Windows/releases/download/v{DS4_VERSION}/DS4Windows_{DS4_VERSION}_x64.zip"
DS4_SHA256 = "70c4fe79ef63c039e603e298f73dc41a971a9884348288c8a7eb9dc739c4cb9f"
DS4_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "DS4Windows"
DOTNET_DESKTOP_8 = "https://aka.ms/dotnet/8.0/windowsdesktop-runtime-win-x64.exe"   # oficial da Microsoft

# BIOS reconhecido pelo tamanho exato do dump; o PS2 às vezes vem com arquivos irmãos (.rom1, .nvm...)
BIOS_SIZES = {512 * 1024: "ps1", 4 * 1024 * 1024: "ps2"}
BIOS_EXTENSIONS = (".bin", ".rom")
PS2_BIOS_SIBLINGS = (".rom0", ".rom1", ".rom2", ".erom", ".nvm", ".mec")


def _wait_task(emu, log) -> None:
    """Acompanha a tarefa que o emulador abriu em segundo plano (run_task) até acabar."""
    worker = next((t for t in threading.enumerate() if t.name == f"emu-{emu.id}"), None)
    while worker and worker.is_alive():
        task = emu.task
        pct = f" {task['done'] * 100 // task['total']}%" if 0 < task.get("done", 0) < task.get("total", 0) else ""
        log(f"{task.get('label') or emu.name}{pct}")
        time.sleep(0.3)
    if emu.task.get("error"):
        raise RuntimeError(emu.task["error"])


def install_emulator(emu_id: str, log=print) -> None:
    emu = EMULATORS[emu_id]
    emu.install()
    _wait_task(emu, log)
    if emu_id == "ps3" and not emu.firmware_installed():
        emu.install_firmware()
        _wait_task(emu, log)


def find_bios(folder: Path) -> dict[str, list[Path]]:
    """{"ps1": [...], "ps2": [...]}: dumps de BIOS dentro da pasta (e subpastas)."""
    found: dict[str, list[Path]] = {"ps1": [], "ps2": []}
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() in BIOS_EXTENSIONS and path.is_file():
            console = BIOS_SIZES.get(path.stat().st_size)
            if console:
                found[console].append(path)
    return found


def import_bios(found: dict[str, list[Path]]) -> int:
    """Copia os BIOS encontrados para a pasta bios de cada emulador. Devolve quantos foram copiados."""
    copied = 0
    for console, files in found.items():
        emu = EMULATORS[console]
        emu.bios_dir.mkdir(parents=True, exist_ok=True)
        for bios in files:
            shutil.copy2(bios, emu.bios_dir / bios.name)
            if console == "ps2":
                for sibling in bios.parent.glob(f"{bios.stem}.*"):
                    if sibling.suffix.lower() in PS2_BIOS_SIBLINGS:
                        shutil.copy2(sibling, emu.bios_dir / sibling.name)
            copied += 1
        if files and emu.installed():
            emu.configure()
    return copied


def ds4windows_exe() -> Path:
    return DS4_DIR / "DS4Windows.exe"


def dotnet_desktop_8() -> bool:
    shared = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "dotnet" / "shared" / "Microsoft.WindowsDesktop.App"
    return shared.is_dir() and any(d.name.startswith("8.") for d in shared.iterdir())


def install_ds4windows(log=print) -> Path:
    EMU_ROOT.mkdir(parents=True, exist_ok=True)
    archive = EMU_ROOT / f"DS4Windows_{DS4_VERSION}_x64.zip"
    _download(DS4_URL, archive, lambda d, t: log(f"Baixando o DS4Windows… {d * 100 // t}%" if t else "Baixando o DS4Windows…"))
    if hashlib.sha256(archive.read_bytes()).hexdigest() != DS4_SHA256:
        archive.unlink(missing_ok=True)
        raise RuntimeError("O download do DS4Windows veio diferente do esperado (SHA-256 não confere).")
    log("Extraindo o DS4Windows…")
    extract(archive, DS4_DIR)
    archive.unlink(missing_ok=True)
    from .installer import START_MENU, _shortcut
    _shortcut(START_MENU / "DS4Windows.lnk", ds4windows_exe(), "DS4Windows - controle de PlayStation no PC")
    if not dotnet_desktop_8():
        log("Baixando o .NET 8 da Microsoft (o DS4Windows precisa dele)…")
        runtime = EMU_ROOT / "windowsdesktop-runtime-8-win-x64.exe"
        _download(DOTNET_DESKTOP_8, runtime, lambda d, t: None)
        log("Instalando o .NET 8 (o Windows vai pedir permissão)…")
        with clean_dll_search():
            subprocess.run([str(runtime), "/install", "/passive", "/norestart"])
        runtime.unlink(missing_ok=True)
    return ds4windows_exe()

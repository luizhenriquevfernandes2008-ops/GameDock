from __future__ import annotations

import contextlib
import ctypes
import os
import subprocess
import sys
from pathlib import Path

from .models import Game, now_iso

# jogos de emulador: pid do processo que o GameDock abriu -> id do jogo (o monitor de tempo usa isso)
EMULATED: dict[int, str] = {}
EMULATED_SOURCES = ("ps3", "ps2", "ps1", "nes")

ERROR_ELEVATION_REQUIRED = 740
ERROR_CANCELLED = 1223  # o usuário clicou "Não" no aviso de administrador (UAC)


class LaunchError(Exception):
    pass


@contextlib.contextmanager
def clean_dll_search():
    """Abre programas externos sem herdar a pasta de DLLs do GameDock.

    O PyInstaller chama SetDllDirectory(_internal) para o app achar as próprias DLLs, e processos filhos
    herdam isso: o RPCS3 carregava o vcruntime140.dll de dentro do GameDock e se recusava a rodar.
    Durante a criação do processo, a busca volta ao padrão do Windows.
    """
    frozen = getattr(sys, "frozen", False)
    kernel32 = ctypes.windll.kernel32
    if frozen:
        kernel32.SetDllDirectoryW(None)
    try:
        yield
    finally:
        if frozen:
            kernel32.SetDllDirectoryW(getattr(sys, "_MEIPASS", None))


def launch(game: Game) -> None:
    with clean_dll_search():
        _launch(game)


def _launch(game: Game) -> None:
    if game.launch_type == "uri":
        # Steam/Epic/Ubisoft registram protocolos no Windows; o próprio launcher abre o jogo.
        os.startfile(game.launch_target)
    elif game.launch_type == "exe":
        exe = Path(game.launch_target)
        if not exe.exists():
            raise LaunchError(f"Executável não encontrado: {exe}")
        cwd = game.working_dir if game.working_dir and Path(game.working_dir).is_dir() else str(exe.parent)
        args = [*game.launch_args, *game.extra_args]
        try:
            # Processo desacoplado: o jogo continua rodando mesmo se o GameDock fechar.
            proc = subprocess.Popen(
                [str(exe), *args],
                cwd=cwd,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True,
            )
            if game.source in EMULATED_SOURCES:
                EMULATED[proc.pid] = game.id
        except OSError as exc:
            if getattr(exc, "winerror", None) != ERROR_ELEVATION_REQUIRED:
                raise
            # o .exe pede administrador no manifesto (ex: Castlevania LoS): o Popen não sabe pedir,
            # o ShellExecute com "runas" mostra o aviso do Windows (UAC), como no duplo clique
            try:
                os.startfile(str(exe), "runas", subprocess.list2cmdline(args), cwd)
            except OSError as uac:
                if getattr(uac, "winerror", None) == ERROR_CANCELLED:
                    raise LaunchError("O jogo precisa de permissão de administrador e o pedido foi recusado.") from uac
                raise
    elif game.launch_type == "emu":   # emulador que prepara o jogo antes (ex: montar o ISO do PS3)
        from .emulators import launch_game
        try:
            proc = launch_game(game)
        except (RuntimeError, OSError, KeyError) as exc:
            raise LaunchError(str(exc)) from exc
        EMULATED[proc.pid] = game.id
    else:
        raise LaunchError(f"Tipo de launch desconhecido: {game.launch_type}")

    # sessões e tempo de jogo são contados pelo PlaytimeMonitor, que também vê jogos abertos fora do GameDock
    game.last_played = now_iso()

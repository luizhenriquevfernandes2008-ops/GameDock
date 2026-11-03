"""Registro de erros: tudo que o app imprime e todo erro não tratado vai para um arquivo de log.

No .exe (janela, sem console) o print() não ia para lugar nenhum; agora vai para
%LOCALAPPDATA%\\GameDock\\logs\\gamedock.log (gira em 1 MB, guarda 3 arquivos antigos).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG = logging.getLogger("gamedock")


def logs_dir() -> Path:
    folder = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GameDock" / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def log_file() -> Path:
    return logs_dir() / "gamedock.log"


class _ToLog:
    """Substitui sys.stdout/sys.stderr: cada linha impressa vira uma linha do log."""

    def __init__(self, level: int, original):
        self.level, self.original, self.buffer = level, original, ""

    def write(self, text: str) -> int:
        if self.original:
            try:
                self.original.write(text)
            except Exception:
                pass
        self.buffer += text
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            if line.strip():
                LOG.log(self.level, line.rstrip())
        return len(text)

    def flush(self) -> None:
        pass

    def isatty(self) -> bool:
        return False


def setup() -> None:
    handler = RotatingFileHandler(log_file(), maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(threadName)s: %(message)s", "%Y-%m-%d %H:%M:%S"))
    LOG.addHandler(handler)
    LOG.setLevel(logging.INFO)
    sys.stdout = _ToLog(logging.INFO, sys.stdout)
    sys.stderr = _ToLog(logging.ERROR, sys.stderr)

    def on_error(kind, value, tb):
        LOG.error("erro não tratado", exc_info=(kind, value, tb))

    sys.excepthook = on_error
    threading.excepthook = lambda args: LOG.error(f"erro na thread {args.thread.name if args.thread else '?'}",
                                                  exc_info=(args.exc_type, args.exc_value, args.exc_traceback))
    from . import __version__
    LOG.info(f"GameDock {__version__} iniciando ({'exe' if getattr(sys, 'frozen', False) else 'python'})")


def tail(lines: int = 200) -> str:
    path = log_file()
    if not path.is_file():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])

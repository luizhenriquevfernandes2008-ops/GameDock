"""Clipes de vídeo na galeria.

Os clipes são gravados pelo replay buffer do próprio GameDock (veja recorder.py) em Vídeos/GameDock/<jogo>/.
No índice de capturas eles guardam "clip:<caminho relativo a essa pasta>" em vez de um caminho em art/,
e o servidor local entrega os vídeos em /clips/ (com Range, para poder avançar).
"""
from __future__ import annotations

from pathlib import Path

CLIP_PREFIX = "clip:"


def captures_dir() -> Path:
    from .recorder import clips_dir
    return clips_dir()


def clip_file(ref: str) -> Path | None:
    if not ref.startswith(CLIP_PREFIX):
        return None
    folder = captures_dir().resolve()
    path = (folder / ref[len(CLIP_PREFIX):]).resolve()
    return path if path.is_relative_to(folder) and path.is_file() else None

from __future__ import annotations

import re
import winreg
from typing import Iterator

from ..models import Game


class Scanner:
    """Cada launcher (Steam, Epic...) é uma subclasse que sabe onde procurar os jogos."""

    name: str = ""

    def scan(self) -> Iterator[Game]:
        raise NotImplementedError


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def reg_value(root, path: str, value: str):
    try:
        with winreg.OpenKey(root, path) as key:
            return winreg.QueryValueEx(key, value)[0]
    except OSError:
        return None


def reg_subkeys(root, path: str) -> Iterator[tuple[str, dict]]:
    """Itera as subchaves de `path`, devolvendo (nome, {valor: dado})."""
    try:
        parent = winreg.OpenKey(root, path)
    except OSError:
        return
    with parent:
        index = 0
        while True:
            try:
                sub_name = winreg.EnumKey(parent, index)
            except OSError:
                break
            index += 1
            values = {}
            try:
                with winreg.OpenKey(parent, sub_name) as sub:
                    i = 0
                    while True:
                        try:
                            v_name, v_data, _ = winreg.EnumValue(sub, i)
                        except OSError:
                            break
                        values[v_name] = v_data
                        i += 1
            except OSError:
                continue
            yield sub_name, values

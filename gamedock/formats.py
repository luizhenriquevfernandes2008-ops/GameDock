"""Leitores de formatos de arquivo usados pelos launchers (sem dependências externas)."""
from __future__ import annotations

import re
import struct
from pathlib import Path


# ---------------------------------------------------------------- VDF / ACF (Steam)

_VDF_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


def parse_vdf(text: str) -> dict:
    """Formato de chave/valor da Valve:  "chave" "valor"  ou  "chave" { ... }"""
    root: dict = {}
    stack = [root]
    key = None
    for match in _VDF_TOKEN.finditer(text):
        string, brace = match.groups()
        if brace == "{":
            child: dict = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            stack.pop()
        elif key is None:
            key = string
        else:
            stack[-1][key] = string.replace("\\\\", "\\")
            key = None
    return root


def read_vdf(path: Path) -> dict:
    return parse_vdf(path.read_text(encoding="utf-8", errors="replace"))


# ---------------------------------------------------------------- atalhos .url

def read_url_shortcut(path: Path) -> str | None:
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(r"^URL=(.+)$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


# ---------------------------------------------------------------- atalhos .lnk
# Especificação: [MS-SHLLINK] Shell Link Binary File Format.

_HAS_ID_LIST = 0x01
_HAS_LINK_INFO = 0x02
_STRING_FLAGS = ((0x04, "name"), (0x08, "relative_path"), (0x10, "working_dir"),
                 (0x20, "arguments"), (0x40, "icon_location"))
_IS_UNICODE = 0x80


def _cstring(data: bytes, offset: int) -> str:
    end = data.index(b"\0", offset)
    return data[offset:end].decode("cp1252", errors="replace")


def _wstring(data: bytes, offset: int) -> str:
    end = offset
    while data[end:end + 2] != b"\0\0":
        end += 2
    return data[offset:end].decode("utf-16-le", errors="replace")


def read_lnk(path: Path) -> dict | None:
    """Retorna {"target", "arguments", "working_dir", ...} ou None se não der pra ler."""
    try:
        data = path.read_bytes()
        if len(data) < 0x4C or data[:4] != b"\x4c\x00\x00\x00":
            return None
        flags = struct.unpack_from("<I", data, 0x14)[0]
        pos = 0x4C
        result: dict = {"target": None}

        if flags & _HAS_ID_LIST:
            pos += 2 + struct.unpack_from("<H", data, pos)[0]

        if flags & _HAS_LINK_INFO:
            size, header_size, info_flags, _, base_offset = struct.unpack_from("<5I", data, pos)
            if info_flags & 0x1:  # VolumeIDAndLocalBasePath
                if header_size >= 0x24:
                    unicode_offset = struct.unpack_from("<I", data, pos + 0x1C)[0]
                    result["target"] = _wstring(data, pos + unicode_offset)
                else:
                    result["target"] = _cstring(data, pos + base_offset)
            pos += size

        for flag, key in _STRING_FLAGS:
            if flags & flag:
                count = struct.unpack_from("<H", data, pos)[0]
                pos += 2
                if flags & _IS_UNICODE:
                    result[key] = data[pos:pos + count * 2].decode("utf-16-le", errors="replace")
                    pos += count * 2
                else:
                    result[key] = data[pos:pos + count].decode("cp1252", errors="replace")
                    pos += count

        # Nem todo atalho tem LinkInfo, e alguns (ex: Riot) gravam um caminho relativo
        # errado. Testamos candidatos e ficamos com o primeiro que existe de fato.
        candidates = [result["target"]]
        if result.get("relative_path"):
            relative = result["relative_path"]
            candidates.append(str((path.parent / relative).resolve()))
            if result.get("working_dir"):
                candidates.append(str(Path(result["working_dir"]) / Path(relative).name))
        existing = [c for c in candidates if c and Path(c).exists()]
        result["target"] = existing[0] if existing else next((c for c in candidates if c), None)
        return result
    except (OSError, struct.error, ValueError):
        return None

"""Leitor do formato KeyValues *binário* da Valve (usado nos caches de conquistas da Steam).

Cada item é: 1 byte de tipo, nome terminado em \\0, e o valor. Tipo 0 abre um bloco,
que termina com o byte 8.
"""
from __future__ import annotations

import struct
from pathlib import Path

NESTED, STRING, INT32, FLOAT32, POINTER, WSTRING, COLOR, UINT64, END, INT64, END_ALT = 0, 1, 2, 3, 4, 5, 6, 7, 8, 10, 11


def parse(data: bytes) -> dict:
    pos = 0

    def cstring() -> str:
        nonlocal pos
        end = data.index(b"\0", pos)
        text = data[pos:end].decode("utf-8", "replace")
        pos = end + 1
        return text

    def block() -> dict:
        nonlocal pos
        result: dict = {}
        while pos < len(data):
            kind = data[pos]
            pos += 1
            if kind in (END, END_ALT):
                return result
            name = cstring()
            if kind == NESTED:
                result[name] = block()
            elif kind == STRING:
                result[name] = cstring()
            elif kind in (INT32, POINTER, COLOR):
                result[name] = struct.unpack_from("<i", data, pos)[0]
                pos += 4
            elif kind == FLOAT32:
                result[name] = struct.unpack_from("<f", data, pos)[0]
                pos += 4
            elif kind == UINT64:
                result[name] = struct.unpack_from("<Q", data, pos)[0]
                pos += 8
            elif kind == INT64:
                result[name] = struct.unpack_from("<q", data, pos)[0]
                pos += 8
            elif kind == WSTRING:
                end = pos
                while data[end:end + 2] != b"\0\0":
                    end += 2
                result[name] = data[pos:end].decode("utf-16-le", "replace")
                pos = end + 2
            else:
                raise ValueError(f"tipo desconhecido {kind} em {pos}")
        return result

    return block()


def read(path: Path) -> dict:
    return parse(Path(path).read_bytes())

"""Leitor mínimo de ISO 9660 (só leitura): lista pastas e lê arquivos pequenos de dentro de um .iso.

Usado para identificar jogos de PS3 em .iso (PARAM.SFO, ícone) sem montar nem extrair a imagem, e o
SYSTEM.CNF de jogos de PS1/PS2. Também lê imagens "cruas" de CD (.bin, setores de 2352 bytes, Mode 1/Mode 2),
que é como vêm os jogos de PS1.
"""
from __future__ import annotations

import struct
from pathlib import Path

SECTOR = 2048


class IsoImage:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.f = self.path.open("rb")
        self.raw, self.skip = 2048, 0   # tamanho do setor no arquivo e onde começam os dados dentro dele
        for raw, skip in ((2048, 0), (2352, 24), (2352, 16)):   # ISO, CD Mode 2, CD Mode 1
            self.raw, self.skip = raw, skip
            pvd = self._read(16 * SECTOR, SECTOR)
            if pvd[1:6] == b"CD001":
                break
        else:
            self.f.close()
            raise ValueError("não é uma imagem ISO 9660")
        self.root = self._record(pvd[156:156 + 34])

    def close(self) -> None:
        self.f.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _read(self, offset: int, size: int) -> bytes:
        if self.raw == SECTOR:
            self.f.seek(offset)
            return self.f.read(size)
        out = bytearray()   # imagem crua: junta os 2048 bytes de dados de cada setor
        sector, within = divmod(offset, SECTOR)
        while len(out) < size:
            self.f.seek(sector * self.raw + self.skip + within)
            chunk = self.f.read(min(SECTOR - within, size - len(out)))
            if not chunk:
                break
            out += chunk
            sector, within = sector + 1, 0
        return bytes(out)

    @staticmethod
    def _record(raw: bytes) -> dict:
        name_len = raw[32]
        name = raw[33:33 + name_len].decode("latin-1").split(";")[0]
        return {"lba": struct.unpack_from("<I", raw, 2)[0], "size": struct.unpack_from("<I", raw, 10)[0],
                "dir": bool(raw[25] & 2), "name": name}

    def listdir(self, entry: dict) -> list[dict]:
        data = self._read(entry["lba"] * SECTOR, entry["size"])
        items, pos = [], 0
        while pos < len(data):
            length = data[pos]
            if length == 0:   # o resto do setor está vazio: pula para o próximo
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            rec = self._record(data[pos:pos + length])
            if rec["name"] not in ("\x00", "\x01"):   # "." e ".."
                items.append(rec)
            pos += length
        return items

    def find(self, path: str) -> dict | None:
        entry = self.root
        for part in path.strip("/").split("/"):
            entry = next((e for e in self.listdir(entry) if e["name"].upper() == part.upper()), None)
            if entry is None:
                return None
        return entry

    def read(self, path: str, limit: int | None = None) -> bytes | None:
        entry = self.find(path)
        if not entry or entry["dir"]:
            return None
        return self._read(entry["lba"] * SECTOR, min(entry["size"], limit or entry["size"]))

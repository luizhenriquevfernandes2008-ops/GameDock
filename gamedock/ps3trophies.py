"""Troféus de jogos de PS3 no RPCS3, lidos dos arquivos que o próprio emulador grava (como num PS3 de verdade).

dev_hdd0/home/00000001/trophy/<NPCOMMID>/
  TROPCONF.SFM   XML com os troféus: id, nível real (P/G/S/B), secreto, nome e descrição
  TROP###.PNG    ícone de cada troféu
  TROPUSR.DAT    progresso (binário, big-endian): quais foram desbloqueados e quando
O NPCOMMID do jogo está no próprio disco: PS3_GAME/TROPDIR/<NPCOMMID>/.
"""
from __future__ import annotations

import html
import re
import struct
from datetime import datetime, timezone
from pathlib import Path

TROPUSR_MAGIC = 0x818F54AD
GRADES = {"P": "platinum", "G": "gold", "S": "silver", "B": "bronze"}
# CellRtcTick: microssegundos desde 01/01/0001
RTC_UNIX_OFFSET_US = int((datetime(1970, 1, 1, tzinfo=timezone.utc) - datetime(1, 1, 1, tzinfo=timezone.utc)).total_seconds()) * 1_000_000


def parse_tropconf(path: Path) -> list[dict]:
    text = Path(path).read_bytes().decode("utf-8", "replace")
    trophies = []
    for match in re.finditer(r'<trophy\s+([^>]*)>(.*?)</trophy>', text, re.S):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', match.group(1)))
        body = match.group(2)
        field = lambda tag: html.unescape(m.group(1).strip()) if (m := re.search(rf"<{tag}>(.*?)</{tag}>", body, re.S)) else ""
        trophies.append({"id": int(attrs.get("id", "0")), "grade": GRADES.get(attrs.get("ttype", "B"), "bronze"),
                         "hidden": attrs.get("hidden") == "yes", "name": field("name"), "detail": field("detail")})
    return trophies


def _to_unix(value: int) -> int | None:
    if not value:
        return None
    if value > RTC_UNIX_OFFSET_US:            # CellRtcTick
        return (value - RTC_UNIX_OFFSET_US) // 1_000_000
    if value > 10**15:                         # microssegundos desde 1970
        return value // 1_000_000
    return value if value > 10**9 else None    # segundos


def parse_tropusr(path: Path) -> dict[int, int | None]:
    """{id do troféu: horário unix do desbloqueio} só dos desbloqueados."""
    data = Path(path).read_bytes()
    magic, _unk1, tables, _unk2 = struct.unpack_from(">IIII", data, 0)
    if magic != TROPUSR_MAGIC:
        return {}
    unlocked = {}
    for i in range(tables):
        table_type, entry_size, _unk, count, offset, _reserved = struct.unpack_from(">IIIIQQ", data, 48 + i * 32)
        if table_type != 6:   # tabela 6 = estado e horário de cada troféu
            continue
        for n in range(count):
            pos = offset + n * (entry_size + 16)   # cada entrada: cabeçalho de 16 bytes + conteúdo
            trophy_id, state = struct.unpack_from(">II", data, pos + 16)
            timestamp = struct.unpack_from(">Q", data, pos + 32)[0]
            if state == 1:
                unlocked[trophy_id] = _to_unix(timestamp)
    return unlocked


# ---------------------------------------------------------------- qual pasta de troféus é de qual jogo

def _trophy_homes() -> list[Path]:
    from .emulators import EMULATORS
    home = EMULATORS["ps3"].folder / "dev_hdd0" / "home"
    users = sorted(p / "trophy" for p in home.iterdir() if (p / "trophy").is_dir()) if home.is_dir() else []
    return users or [home / "00000001" / "trophy"]


def _cache_path() -> Path:
    from .storage import data_dir
    folder = data_dir() / "trophies" / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / "ps3-npcommid.json"


def _from_disc(game) -> str | None:
    """O disco/pasta do jogo traz PS3_GAME/TROPDIR/<NPCOMMID>/."""
    from .iso9660 import IsoImage
    path = Path(game.install_dir or "")
    try:
        if path.suffix.lower() == ".iso":
            with IsoImage(path) as img:
                entry = img.find("PS3_GAME/TROPDIR")
                names = [e["name"] for e in img.listdir(entry) if e["dir"]] if entry and entry["dir"] else []
        else:
            names = [p.name for base in (path / "PS3_GAME" / "TROPDIR", path / "TROPDIR") if base.is_dir()
                     for p in base.iterdir() if p.is_dir()]
    except (OSError, ValueError, struct.error):
        return None
    return names[0].upper() if names else None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _from_title(game) -> str | None:
    """Sem TROPDIR legível: procura entre os troféus que o RPCS3 já registrou um com o mesmo nome do jogo."""
    wanted = _norm(game.name)
    for home in _trophy_homes():
        for conf in home.glob("*/TROPCONF.SFM"):
            match = re.search(rb"<title-name>(.*?)</title-name>", conf.read_bytes(), re.S)
            if match and _norm(html.unescape(match.group(1).decode("utf-8", "replace"))) == wanted:
                return conf.parent.name
    return None


def npcommid(game) -> str | None:
    import json
    cache_file = _cache_path()
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if cache.get(game.id):
        return cache[game.id]
    found = _from_disc(game) or _from_title(game)
    if found:
        cache[game.id] = found
        cache_file.write_text(json.dumps(cache, indent=1), encoding="utf-8")
    return found


def trophy_dir(game) -> Path | None:
    """Pasta de troféus do jogo no RPCS3 (pode ainda não existir: o RPCS3 cria na primeira vez que o jogo abre)."""
    comm = npcommid(game)
    if not comm:
        return None
    homes = _trophy_homes()
    return next((h / comm for h in homes if (h / comm).is_dir()), homes[0] / comm)

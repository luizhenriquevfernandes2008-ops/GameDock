"""Troféus (conquistas) dos jogos.

De onde vêm:
- Jogos da Steam: o cache local da própria Steam (appcache/stats). O esquema
  (UserGameStatsSchema_<appid>.bin) tem nomes em PT-BR, descrições e ícones; o progresso
  (UserGameStats_<conta>_<appid>.bin) tem um campo de bits por grupo + a hora de cada desbloqueio.
- Outros jogos que existem na Steam: os arquivos locais de conquistas que o jogo grava
  (vários formatos .ini/.json, lidos de forma tolerante). Nome/descrição/ícone vêm da página
  pública de conquistas da comunidade Steam.
- Jogos de PS3 (RPCS3): os arquivos de troféus que o emulador grava, como num PS3 de verdade
  (veja ps3trophies.py). Esses já trazem o nível real de cada troféu, incluindo a platina.
- Raridade (todas): a API pública de porcentagem global de desbloqueio, que vira o nível do troféu
  (ouro/prata/bronze, como no PlayStation). Completar tudo dá a platina.

O estado fica em %APPDATA%/GameDock/trophies/<jogo>.json; desbloqueios novos (comparados com esse
estado) viram eventos — é aí que entram o aviso na tela e a captura.
"""
from __future__ import annotations

import configparser
import json
import os
import re
import struct
import threading
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import binvdf, ps3trophies, retroach
from .art import USER_AGENT, as_url, steam_store_match
from .models import Game
from .scanners.base import slug
from .scanners.steam import SteamScanner
from .storage import _read_json, atomic_write, data_dir

ICON_URL = "https://shared.akamai.steamstatic.com/community_assets/images/apps/{appid}/{icon}"
SCHEMA_TTL = 30 * 24 * 3600
PERCENT_TTL = 7 * 24 * 3600
LANGS = ("brazilian", "portuguese", "english")
# o vigia de troféus e o painel da interface podem sincronizar o mesmo jogo ao mesmo tempo
_SYNC_LOCK = threading.RLock()

# arquivos de conquistas que jogos fora da Steam costumam gravar ({appid} e {game} são substituídos)
LOCAL_FILES = (
    "{appdata}/Goldberg SteamEmu Saves/{appid}/achievements.json",
    "{appdata}/GSE Saves/{appid}/achievements.json",
    "{public}/Documents/OnlineFix/{appid}/Stats/Achievements.ini",
    "{public}/Documents/Steam/CODEX/{appid}/achievements.ini",
    "{public}/Documents/Steam/RUNE/{appid}/achievements.ini",
    "{appdata}/Steam/CODEX/{appid}/achievements.ini",
    "{game}/Profile/*/Stats/achievements.ini",
    "{game}/Profile/*/Stats/Achievements.bin",
)
UNLOCK_KEYS = ("achieved", "haveachieved", "unlocked", "earned", "state")
TIME_KEYS = ("timestamp", "haveachievedtime", "unlocktime", "earned_time", "time")


@dataclass
class Trophy:
    api: str
    name: str
    desc: str = ""
    hidden: bool = False
    icon: str = ""
    icon_gray: str = ""
    percent: float | None = None
    unlocked: bool = False
    unlocked_at: int | None = None
    screenshot: str | None = None   # caminho relativo dentro de art/
    grade: str | None = None        # nível definido pelo próprio jogo (PS3); senão vem da raridade

    @property
    def tier(self) -> str:
        if self.grade:
            return self.grade
        if self.percent is None:
            return "bronze"
        return "gold" if self.percent < 10 else "silver" if self.percent < 35 else "bronze"


@dataclass
class TrophySet:
    game_id: str
    appid: str | None = None
    source: str = ""                   # "steam" | "local" | "ps3"
    trophies: dict[str, Trophy] = field(default_factory=dict)
    platinum_at: int | None = None

    @property
    def unlocked(self) -> int:
        return sum(t.unlocked for t in self.trophies.values())

    def summary(self) -> dict:
        return {"total": len(self.trophies), "unlocked": self.unlocked, "platinum": bool(self.platinum_at)}


# ---------------------------------------------------------------- utilidades

def _store_path(game_id: str) -> Path:
    folder = data_dir() / "trophies"
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{slug(game_id)}.json"


def load(game_id: str) -> TrophySet:
    path = _store_path(game_id)
    if not path.is_file():
        return TrophySet(game_id=game_id)
    data = _read_json(path, None)
    if data is None:
        return TrophySet(game_id=game_id)
    trophies = {k: Trophy(**v) for k, v in data.pop("trophies", {}).items()}
    return TrophySet(**data, trophies=trophies)


def save(tset: TrophySet) -> None:
    atomic_write(_store_path(tset.game_id), json.dumps(asdict(tset), ensure_ascii=False, indent=1))


def _get(url: str, cache: Path, ttl: int) -> bytes | None:
    if cache.is_file() and time.time() - cache.stat().st_mtime < ttl:
        return cache.read_bytes()
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
        cache.write_bytes(body)
        return body
    except OSError:
        return cache.read_bytes() if cache.is_file() else None


def _cache_dir() -> Path:
    folder = data_dir() / "trophies" / "cache"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _localized(value) -> str:
    if isinstance(value, dict):
        return next((value[l] for l in LANGS if value.get(l)), "")
    return value or ""


def steam_appid(game: Game) -> str | None:
    if game.id.startswith("steam:"):
        return game.id.split(":", 1)[1]
    if game.source in ("riot", "battlenet", "ea", "google", "ps3", "ps2", "ps1", "nes"):
        return None
    return steam_store_match(game)


# ---------------------------------------------------------------- esquema

def _steam_stats_dir() -> Path | None:
    root = SteamScanner().steam_root()
    folder = root / "appcache" / "stats" if root else None
    return folder if folder and folder.is_dir() else None


def _schema_from_steam_cache(appid: str) -> dict[str, Trophy]:
    """Esquema local da Steam: {api: Trophy}, com a posição (grupo, bit) guardada em _bits."""
    folder = _steam_stats_dir()
    path = folder / f"UserGameStatsSchema_{appid}.bin" if folder else None
    if not path or not path.is_file():
        return {}
    stats = binvdf.read(path).get(appid, {}).get("stats", {})
    result = {}
    for stat_id, stat in stats.items():
        for bit, info in (stat.get("bits") or {}).items():
            display = info.get("display", {})
            trophy = Trophy(
                api=info.get("name", f"{stat_id}_{bit}"),
                name=_localized(display.get("name")),
                desc=_localized(display.get("desc")),
                hidden=str(display.get("hidden", 0)) == "1",
                icon=ICON_URL.format(appid=appid, icon=display.get("icon", "")) if display.get("icon") else "",
                icon_gray=ICON_URL.format(appid=appid, icon=display.get("icon_gray", "")) if display.get("icon_gray") else "",
            )
            _BITS[(appid, trophy.api)] = (stat_id, int(bit))
            result[trophy.api] = trophy
    return result


_BITS: dict[tuple[str, str], tuple[str, int]] = {}


def _schema_from_community(appid: str) -> dict[str, Trophy]:
    """Página pública de conquistas da comunidade Steam (traz um JSON embutido com tudo)."""
    body = _get(f"https://steamcommunity.com/stats/{appid}/achievements/?l=brazilian",
                _cache_dir() / f"community-{appid}.html", SCHEMA_TTL)
    if not body:
        return {}
    match = re.search(rb'<script type="application/json" id="valve-ssr-data"[^>]*>(.*?)</script>', body, re.S)
    if not match:
        return {}
    found: dict[str, Trophy] = {}

    def walk(node):
        if isinstance(node, str) and node.startswith(("{", "[")) and "api_name" in node:
            try:
                walk(json.loads(node))
            except ValueError:
                pass
        elif isinstance(node, dict):
            if "api_name" in node and "name" in node:
                api = node["api_name"]
                found[api] = Trophy(
                    api=api, name=node.get("name", api), desc=node.get("description", ""),
                    hidden=bool(node.get("hidden")),
                    icon=ICON_URL.format(appid=appid, icon=node["icon_achieved"]) if node.get("icon_achieved") else "",
                    icon_gray=ICON_URL.format(appid=appid, icon=node["icon_unachieved"]) if node.get("icon_unachieved") else "",
                )
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    try:
        walk(json.loads(match.group(1)))
    except ValueError:
        return {}
    return found


def _percentages(appid: str) -> dict[str, float]:
    body = _get(f"https://api.steampowered.com/ISteamUserStats/GetGlobalAchievementPercentagesForApp/v2/?gameid={appid}",
                _cache_dir() / f"percent-{appid}.json", PERCENT_TTL)
    try:
        items = json.loads(body or b"{}").get("achievementpercentages", {}).get("achievements", [])
        return {i["name"]: float(i["percent"]) for i in items}
    except (ValueError, KeyError, TypeError):
        return {}


# ---------------------------------------------------------------- progresso

def _steam_user_files(appid: str) -> list[Path]:
    folder = _steam_stats_dir()
    return sorted(folder.glob(f"UserGameStats_*_{appid}.bin")) if folder else []


def _unlocked_from_steam(appid: str) -> dict[str, int | None]:
    unlocked: dict[str, int | None] = {}
    for path in _steam_user_files(appid):  # uma por conta Steam do PC
        try:
            cache = binvdf.read(path).get("cache", {})
        except (OSError, ValueError):
            continue
        for (app, api), (stat_id, bit) in _BITS.items():
            if app != appid:
                continue
            stat = cache.get(stat_id)
            if isinstance(stat, dict) and int(stat.get("data", 0)) >> bit & 1:
                when = (stat.get("AchievementTimes") or {}).get(str(bit))
                unlocked[api] = max(unlocked.get(api) or 0, when or 0) or None
    return unlocked


def local_files(appid: str, game: Game) -> list[Path]:
    base = {"appdata": os.environ.get("APPDATA", ""), "public": os.environ.get("PUBLIC", r"C:\Users\Public"),
            "appid": appid, "game": str(Path(game.exe).parent) if game.exe else ""}
    found = []
    for pattern in LOCAL_FILES:
        if "{game}" in pattern and not base["game"]:
            continue
        text = pattern.format(**base)
        root, _, rest = text.partition("/*/")
        if rest:
            found += [p for p in Path(root).glob(f"*/{rest}") if p.is_file()]
        elif Path(text).is_file():
            found.append(Path(text))
    return found


def _truthy(value) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes")


def _unlocked_from_local(paths: list[Path], known: set[str]) -> dict[str, int | None]:
    unlocked: dict[str, int | None] = {}
    for path in paths:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        entries: dict[str, dict] = {}
        try:
            data = json.loads(text)  # .ini também começa com "[" ("[ACH_NAME]"), então tenta e vê
        except ValueError:
            data = None
        if isinstance(data, (dict, list)):
            if isinstance(data, list):
                data = {d.get("name", ""): d for d in data if isinstance(d, dict)}
            entries = {k: {kk.lower(): vv for kk, vv in v.items()} for k, v in data.items() if isinstance(v, dict)}
        else:
            parser = configparser.ConfigParser(strict=False, interpolation=None)
            try:
                parser.read_string(text)
            except configparser.Error:
                continue
            entries = {s: {k.lower(): v for k, v in parser[s].items()} for s in parser.sections()}
        lowered = {k.lower(): k for k in known}
        for section, values in entries.items():
            api = lowered.get(section.lower())
            if not api or not any(_truthy(values.get(k)) for k in UNLOCK_KEYS if k in values):
                continue
            when = next((int(float(values[k])) for k in TIME_KEYS if str(values.get(k, "")).strip().isdigit()), None)
            unlocked[api] = when
    return unlocked


# ---------------------------------------------------------------- sincronização

def watch_paths(game: Game, appid: str | None = None) -> list[Path]:
    if game.source == "ps3":
        folder = ps3trophies.trophy_dir(game)
        return [folder / "TROPUSR.DAT"] if folder else []
    appid = appid or steam_appid(game)
    if not appid:
        return []
    return _steam_user_files(appid) if game.id.startswith("steam:") else local_files(appid, game)


def sync(game: Game) -> tuple[TrophySet, list[Trophy], bool]:
    """Atualiza os troféus do jogo. Retorna (estado, desbloqueios novos, platina nova)."""
    with _SYNC_LOCK:
        return _sync(game)


def _sync(game: Game) -> tuple[TrophySet, list[Trophy], bool]:
    tset = load(game.id)
    if game.source == "ps3":
        return _sync_ps3(game, tset)
    if game.source in retroach.CONSOLES:
        return _sync_retro(game, tset)
    appid = steam_appid(game)
    if not appid:
        return tset, [], False
    tset.appid = appid
    is_steam = game.id.startswith("steam:")
    tset.source = "steam" if is_steam else "local"

    schema = _schema_from_steam_cache(appid) if is_steam else {}
    if not schema:
        schema = _schema_from_community(appid)
    if not schema:
        save(tset)
        return tset, [], False
    for api, pct in _percentages(appid).items():
        if api in schema:
            schema[api].percent = pct

    if is_steam:
        unlocked = _unlocked_from_steam(appid)
    else:
        unlocked = _unlocked_from_local(local_files(appid, game), set(schema))

    return _merge(tset, schema, unlocked)


def _sync_ps3(game: Game, tset: TrophySet) -> tuple[TrophySet, list[Trophy], bool]:
    folder = ps3trophies.trophy_dir(game)
    if not folder or not (folder / "TROPCONF.SFM").is_file():
        return tset, [], False   # o jogo ainda não abriu no RPCS3 (ou não tem troféus)
    tset.source, tset.appid = "ps3", folder.name
    schema = {}
    for info in ps3trophies.parse_tropconf(folder / "TROPCONF.SFM"):
        api = f"{info['id']:03d}"
        schema[api] = Trophy(api=api, name=info["name"], desc=info["detail"], hidden=info["hidden"],
                             icon=as_url(folder / f"TROP{api}.PNG", game) or "", grade=info["grade"])
    unlocked: dict[str, int | None] = {}
    if (folder / "TROPUSR.DAT").is_file():
        for attempt in range(3):   # o RPCS3 pode estar no meio da gravação do arquivo
            try:
                unlocked = {f"{k:03d}": v for k, v in ps3trophies.parse_tropusr(folder / "TROPUSR.DAT").items()}
                break
            except (OSError, struct.error):
                time.sleep(0.5)
    return _merge(tset, schema, unlocked)


def _sync_retro(game: Game, tset: TrophySet) -> tuple[TrophySet, list[Trophy], bool]:
    """PS1/PS2/NES: RetroAchievements (o próprio emulador já mostra o aviso na hora, dentro do jogo)."""
    try:
        data = retroach.progress(game)
    except Exception as exc:
        print("retroachievements:", game.name, exc)
        return tset, [], False
    if not data:
        return tset, [], False
    tset.source, tset.appid = "retroachievements", str(data["game_id"])
    schema = {t["api"]: Trophy(api=t["api"], name=t["name"], desc=t["desc"], icon=t["icon"], icon_gray=t["icon_gray"],
                               percent=t["percent"]) for t in data["trophies"]}
    unlocked = {t["api"]: t["unlocked_at"] for t in data["trophies"] if t["unlocked_at"]}
    return _merge(tset, schema, unlocked)


def poll_interval(game: Game) -> int | None:
    """Jogos sem arquivo local para vigiar: de quanto em quanto tempo perguntar ao site (enquanto o jogo roda)."""
    return retroach.POLL_EVERY if game.source in retroach.CONSOLES and retroach.account() else None


def _merge(tset: TrophySet, schema: dict[str, Trophy], unlocked: dict[str, int | None]) -> tuple[TrophySet, list[Trophy], bool]:
    # primeira leitura = linha de base: o que já estava desbloqueado não vira aviso na tela
    baseline = not tset.trophies
    new: list[Trophy] = []
    merged: dict[str, Trophy] = {}
    for api, fresh in schema.items():
        old = tset.trophies.get(api)
        if old:  # mantém o que só o GameDock sabe (captura) e o estado já visto
            fresh.unlocked, fresh.unlocked_at, fresh.screenshot = old.unlocked, old.unlocked_at, old.screenshot
        if api in unlocked and not fresh.unlocked:
            fresh.unlocked = True
            fresh.unlocked_at = unlocked[api] or int(time.time())
            new.append(fresh)
        merged[api] = fresh
    tset.trophies = merged

    new_platinum = False
    platinum = next((t for t in merged.values() if t.grade == "platinum"), None)
    if platinum:  # PS3: a platina é um troféu de verdade (e já tem o próprio aviso)
        if platinum.unlocked and not tset.platinum_at:
            tset.platinum_at = platinum.unlocked_at or int(time.time())
    elif merged and all(t.unlocked for t in merged.values()) and not tset.platinum_at:
        tset.platinum_at = max(t.unlocked_at or 0 for t in merged.values()) or int(time.time())
        new_platinum = not baseline
    save(tset)
    return tset, ([] if baseline else new), new_platinum


def to_dict(tset: TrophySet) -> dict:
    trophies = sorted(tset.trophies.values(),
                      key=lambda t: (not t.unlocked, -(t.unlocked_at or 0), -(t.percent or 0)))
    return {
        **tset.summary(),
        "platinumAt": tset.platinum_at,
        "trophies": [asdict(t) | {"tier": t.tier} for t in trophies],
    }

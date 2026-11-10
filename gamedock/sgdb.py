"""SteamGridDB: capas, fundos e logos em alta resolução feitos pela comunidade.

Precisa de uma chave de API gratuita (https://www.steamgriddb.com/profile/preferences/api),
guardada em config.json como "steamgriddb_key". Este módulo só descobre as URLs;
quem baixa e guarda em cache é o art.py.
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

API = "https://www.steamgriddb.com/api/v2"
KEY_PAGE = "https://www.steamgriddb.com/profile/preferences/api"
CACHE_TTL = 7 * 24 * 3600
SAFE = "nsfw=false&humor=false&epilepsy=false"
QUERIES = {
    # capas verticais 2:3 (600x900 é o padrão da biblioteca da Steam)
    "cover": f"grids/game/{{id}}?dimensions=600x900,660x930&types=static&{SAFE}",
    "hero": f"heroes/game/{{id}}?types=static&{SAFE}",
    "logo": f"logos/game/{{id}}?types=static&{SAFE}",
}


class InvalidKey(Exception):
    pass


def _request(path: str, key: str) -> dict | None:
    req = urllib.request.Request(f"{API}/{path}", headers={
        "Authorization": f"Bearer {key}",
        "User-Agent": "GameDock/0.2 (personal Windows game launcher)",
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise InvalidKey from exc
        return None  # 404 = jogo/arte não existe
    except (OSError, ValueError):
        return None


def _cached(path: str, key: str, cache: Path) -> dict | None:
    if cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    data = _request(path, key)
    if data is not None:
        cache.write_text(json.dumps(data), encoding="utf-8")
    return data


def check_key(key: str) -> bool:
    try:
        return bool(_request("search/autocomplete/portal", key))
    except InvalidKey:
        return False


def game_id(name: str, steam_appid: str | None, key: str, folder: Path, normalize) -> int | None:
    """Pelo appid da Steam quando existe (exato); senão busca pelo nome, aceitando só nome idêntico."""
    if steam_appid:
        data = _cached(f"games/steam/{steam_appid}", key, folder / "sgdb-game.json") or {}
        if data.get("success") and data.get("data"):
            return data["data"]["id"]
    # cache por nome: se o nome buscado mudar, a busca antiga (que não achou nada) não vale mais
    search_cache = folder / f"sgdb-search-{re.sub(r'[^a-z0-9]+', '-', name.lower()).strip('-')}.json"
    data = _cached(f"search/autocomplete/{quote(name)}", key, search_cache) or {}
    wanted = normalize(name)
    return next((g["id"] for g in data.get("data") or [] if normalize(g.get("name", "")) == wanted), None)


def art_urls(name: str, steam_appid: str | None, key: str, folder: Path, normalize, kinds: set[str]) -> dict[str, str]:
    """{"cover"|"hero"|"logo": url} para os tipos pedidos. Silencioso se a chave for inválida."""
    try:
        gid = game_id(name, steam_appid, key, folder, normalize)
        if gid is None:
            return {}
        urls = {}
        for kind in kinds:
            data = _cached(QUERIES[kind].format(id=gid), key, folder / f"sgdb-{kind}.json") or {}
            items = data.get("data") or []
            if kind == "hero":  # 1920 de largura basta; 4K pesa demais para a interface
                items = sorted(items, key=lambda i: i.get("width", 0) > 1920)
            if items:
                urls[kind] = items[0]["url"]  # a API já ordena pelos votos da comunidade
        return urls
    except InvalidKey:
        return {}

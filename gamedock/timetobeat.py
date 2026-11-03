"""Tempo para zerar, pelo IGDB (base de jogos da Twitch, API oficial e grátis).

O HowLongToBeat não tem API pública, então a fonte é o IGDB ("game_time_to_beats": correndo, normal e 100%).
Precisa de um Client ID + Client Secret da Twitch (dev.twitch.tv/console, "Register Your Application",
categoria "Application Integration") — igual à chave do SteamGridDB. Sem a chave, vale o tempo que o
usuário digitar na página do jogo.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from .models import Game
from .storage import data_dir, load_config, save_config

KEY_PAGE = "https://dev.twitch.tv/console/apps/create"
TTL = 30 * 24 * 3600


def _credentials() -> tuple[str, str] | None:
    config = load_config()
    cid, secret = (config.get("igdb_client_id") or "").strip(), (config.get("igdb_client_secret") or "").strip()
    return (cid, secret) if cid and secret else None


def _token(cid: str, secret: str, force: bool = False) -> str:
    config = load_config()
    cached = config.get("igdb_token") or {}
    if not force and cached.get("id") == cid and cached.get("expires", 0) > time.time() + 60:
        return cached["token"]
    query = urllib.parse.urlencode({"client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    req = urllib.request.Request(f"https://id.twitch.tv/oauth2/token?{query}", method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())
    config["igdb_token"] = {"id": cid, "token": data["access_token"], "expires": time.time() + data.get("expires_in", 0)}
    save_config(config)
    return data["access_token"]


def _query(endpoint: str, body: str) -> list:
    creds = _credentials()
    if not creds:
        return []
    for attempt in range(2):
        req = urllib.request.Request(f"https://api.igdb.com/v4/{endpoint}", data=body.encode(), method="POST", headers={
            "Client-ID": creds[0], "Authorization": f"Bearer {_token(*creds, force=attempt > 0)}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            if exc.code != 401 or attempt:
                raise
    return []


def check_keys(cid: str, secret: str) -> bool:
    try:
        _token(cid.strip(), secret.strip(), force=True)
        return True
    except Exception:
        return False


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _igdb_id(game: Game) -> int | None:
    if game.id.startswith("steam:"):
        appid = game.id.split(":", 1)[1]
        rows = _query("external_games", f'fields game; where external_game_source = 1 & uid = "{appid}"; limit 1;')
        if rows and rows[0].get("game"):
            return rows[0]["game"]
    name = re.sub(r"\s*\(.*?\)", "", game.display_name).replace('"', "")
    rows = _query("games", f'search "{name}"; fields id,name; limit 10;')
    exact = [r for r in rows if _norm(r.get("name", "")) == _norm(name)]
    return (exact or rows or [{}])[0].get("id")


def get(game: Game) -> dict:
    """{"hastily", "normally", "completely"} em horas (ou None), "source": "igdb" | "user" | None."""
    manual = {"user": game.beat_hours}
    if not _credentials():
        return {"source": "user" if game.beat_hours else None, "configured": False, **manual}
    cache_file = data_dir() / "trophies" / "cache" / "timetobeat.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(game.id)
    if not hit or time.time() - hit.get("at", 0) > TTL:
        try:
            igdb = _igdb_id(game)
            rows = _query("game_time_to_beats", f"fields hastily,normally,completely,count; where game_id = {igdb};") if igdb else []
        except Exception as exc:
            return {"source": None, "configured": True, "error": str(exc), **manual}
        row = rows[0] if rows else {}
        hit = {"at": time.time(), "igdb": igdb, **{k: round(row[k] / 3600, 1) if row.get(k) else None
                                                     for k in ("hastily", "normally", "completely")}, "count": row.get("count")}
        cache[game.id] = hit
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    found = any(hit.get(k) for k in ("hastily", "normally", "completely"))
    return {"source": "igdb" if found else ("user" if game.beat_hours else None), "configured": True,
            **{k: hit.get(k) for k in ("hastily", "normally", "completely", "count")}, **manual}

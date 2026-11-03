"""RetroAchievements: troféus de verdade para jogos de PS1, PS2 e NES (retroachievements.org, grátis).

- Login: usuário + senha viram um "token" (a senha não é guardada). O token vai para os emuladores
  (PCSX2, DuckStation, RetroArch), que desbloqueiam e mostram o aviso na hora, dentro do jogo.
- O GameDock lê o progresso pela Web API do site (precisa da "Web API Key" do perfil do usuário) e põe os
  troféus na página do jogo, no perfil e no XP. A raridade vem de quantos jogadores já desbloquearam.
- Qual jogo do site é qual jogo da biblioteca: pelo nome + console, entre os jogos jogados recentemente.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

from . import __version__
from .models import Game
from .storage import data_dir, load_config, save_config

SITE = "https://retroachievements.org"
MEDIA = "https://media.retroachievements.org/Badge/{badge}{suffix}.png"
CONSOLES = {"ps1": 12, "ps2": 21, "nes": 7}
USER_AGENT = f"GameDock/{__version__}"
POLL_EVERY = 30   # segundos entre leituras enquanto um jogo com RetroAchievements está aberto


def _request(url: str, data: dict | None = None) -> dict | list:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read() or b"{}")


def account() -> dict | None:
    ra = load_config().get("retroachievements") or {}
    return ra if ra.get("user") and ra.get("token") else None


def login(user: str, password: str, api_key: str) -> dict:
    try:
        result = _request(f"{SITE}/dorequest.php", {"r": "login2", "u": user.strip(), "p": password})
    except Exception as exc:
        return {"ok": False, "error": f"Sem resposta do RetroAchievements ({exc})."}
    if not result.get("Success"):
        return {"ok": False, "error": result.get("Error") or "Usuário ou senha incorretos."}
    ra = {"user": result.get("User", user.strip()), "token": result["Token"], "api_key": api_key.strip()}
    if ra["api_key"]:
        try:
            _web("API_GetUserProfile.php", {"u": ra["user"]}, ra)
        except Exception:
            return {"ok": False, "error": "Login ok, mas a Web API Key foi recusada. Copie de retroachievements.org/settings."}
    config = load_config()
    config["retroachievements"] = ra
    save_config(config)
    _apply_to_emulators(ra)
    return {"ok": True, "user": ra["user"]}


def logout() -> None:
    config = load_config()
    config.pop("retroachievements", None)
    save_config(config)
    _apply_to_emulators(None)


def _apply_to_emulators(ra: dict | None) -> None:
    from .emulators import EMULATORS
    for emu in EMULATORS.values():
        if emu.achievements_support and emu.installed():
            try:
                emu.configure_achievements({"user": ra["user"], "token": ra["token"]} if ra else None)
            except Exception as exc:
                print("retroachievements:", emu.id, exc)


def _web(endpoint: str, params: dict, ra: dict | None = None) -> dict | list:
    ra = ra or account()
    if not ra or not ra.get("api_key"):
        raise RuntimeError("sem Web API Key")
    query = urllib.parse.urlencode({"z": ra["user"], "y": ra["api_key"], **params})
    return _request(f"{SITE}/API/{endpoint}?{query}")


def _norm(text: str) -> str:
    text = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", text)
    text = re.sub(r"^the\s+|,\s*the$", "", text.strip(), flags=re.I)
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _game_id(game: Game) -> int | None:
    cache_file = data_dir() / "trophies" / "cache" / "ra-games.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    if cache.get(game.id):
        return cache[game.id]
    recent = _web("API_GetUserRecentlyPlayedGames.php", {"u": account()["user"], "c": 50})
    wanted, console = _norm(game.name), CONSOLES.get(game.source)
    for item in recent if isinstance(recent, list) else []:
        if item.get("ConsoleID") == console and _norm(item.get("Title", "")) == wanted:
            cache[game.id] = int(item["GameID"])
            cache_file.write_text(json.dumps(cache), encoding="utf-8")
            return cache[game.id]
    return None


def progress(game: Game) -> dict | None:
    """{"game_id", "trophies": [{api, name, desc, icon, icon_gray, percent, unlocked_at}]} ou None."""
    if game.source not in CONSOLES or not account() or not account().get("api_key"):
        return None
    gid = _game_id(game)
    if not gid:
        return None
    data = _web("API_GetGameInfoAndUserProgress.php", {"g": gid, "u": account()["user"]})
    players = max(1, int(data.get("NumDistinctPlayers") or data.get("NumDistinctPlayersCasual") or 1))
    trophies = []
    for ach in (data.get("Achievements") or {}).values():
        earned = ach.get("DateEarnedHardcore") or ach.get("DateEarned")
        when = int(time.mktime(time.strptime(earned, "%Y-%m-%d %H:%M:%S"))) if earned else None
        badge = ach.get("BadgeName") or "00000"
        trophies.append({"api": str(ach["ID"]), "name": ach.get("Title", ""), "desc": ach.get("Description", ""),
                         "icon": MEDIA.format(badge=badge, suffix=""), "icon_gray": MEDIA.format(badge=badge, suffix="_lock"),
                         "percent": round(100 * int(ach.get("NumAwarded") or 0) / players, 1), "unlocked_at": when,
                         "order": int(ach.get("DisplayOrder") or 0)})
    trophies.sort(key=lambda t: t["order"])
    return {"game_id": gid, "trophies": trophies}

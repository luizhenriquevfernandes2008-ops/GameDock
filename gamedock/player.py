"""Perfil local do jogador: nível, XP e destaques, calculados dos dados que já estão no PC.

Não existe conta: nome e avatar ficam em profile.json; o resto (horas, troféus, sessões)
vem de library.json, trophies/ e sessions.jsonl. Se um dia houver login (ex: "entrar com a Steam"),
é só enviar este mesmo resumo para um servidor.
"""
from __future__ import annotations

import json
import os

from . import achievements
from .storage import _read_json, atomic_write, data_dir, read_sessions

TROPHY_XP = {"bronze": 15, "silver": 30, "gold": 90, "platinum": 0}  # a platina conta pelo PLATINUM_XP
PLATINUM_XP = 300
XP_PER_HOUR = 10


def xp_to_next(level: int) -> int:
    """XP para ir do nível `level` ao seguinte: 100, 150, 200, ..."""
    return 100 + 50 * (level - 1)


def level_for(xp: int) -> tuple[int, int, int]:
    """(nível, XP dentro do nível, XP necessário para o próximo)."""
    level = 1
    while xp >= xp_to_next(level):
        xp -= xp_to_next(level)
        level += 1
    return level, xp, xp_to_next(level)


def _path():
    return data_dir() / "profile.json"


DEFAULTS = {
    "name": os.environ.get("USERNAME", "Jogador"),
    "bio": "",
    "avatar_mode": "initial",   # initial | game | custom
    "avatar": None,             # id do jogo, quando avatar_mode == "game"
    "banner_mode": "default",   # default | game | custom
    "banner_game": None,
    "banner_pos": 35,           # recorte vertical do banner, 0 (topo) a 100 (base)
    "color": None,              # "#rrggbb" ou None (automática)
    "showcase_game": None,
    "showcase_trophies": [],    # [{"game": id, "api": nome interno}], até 3
    "level_seen": None,
}
EDITABLE = ("name", "bio", "avatar_mode", "avatar", "banner_mode", "banner_game", "banner_pos", "color",
            "showcase_game", "showcase_trophies")
# moldura do avatar pelo nível
FRAMES = ((50, "platinum"), (25, "gold"), (10, "silver"), (1, "bronze"))


def frame_for(level: int) -> str:
    return next(name for minimum, name in FRAMES if level >= minimum)


def profile_dir():
    folder = data_dir() / "art" / "_profile"   # dentro de art/ para o servidor local servir
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def custom_image(kind: str):
    """Foto ou banner enviado pelo usuário (kind = "avatar" | "banner")."""
    return next((p for p in sorted(profile_dir().glob(f"{kind}.*")) if p.suffix.lower() in IMAGE_EXT), None)


IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")


def load_settings() -> dict:
    settings = DEFAULTS | _read_json(_path(), {})
    if settings["avatar"] and "avatar_mode" not in _read_json(_path(), {}):
        settings["avatar_mode"] = "game"  # perfis da versão anterior só tinham "avatar" = jogo
    return settings


def save_settings(settings: dict) -> None:
    atomic_write(_path(), json.dumps(settings, ensure_ascii=False, indent=1))


def update_settings(changes: dict) -> dict:
    settings = load_settings()
    for key in EDITABLE:
        if key not in changes:
            continue
        value = changes[key]
        if key == "name":
            value = (value or "").strip()[:32] or settings["name"]
        elif key == "bio":
            value = (value or "").strip()[:120]
        elif key == "banner_pos":
            value = max(0, min(100, int(value)))
        elif key == "showcase_trophies":
            value = [{"game": t["game"], "api": t["api"]} for t in (value or [])][:3]
        settings[key] = value
    save_settings(settings)
    return settings


def compute(library) -> dict:
    # ocultos e os marcados como "não é um jogo" não entram nas horas, troféus nem XP
    games = [g for g in library.games.values() if not g.hidden and g.track]
    by_id = {g.id: g for g in games}
    counts = {"bronze": 0, "silver": 0, "gold": 0, "platinum": 0}
    platinums, recent, rarest = 0, [], None
    xp = 0
    for game in games:
        tset = achievements.load(game.id)
        if tset.platinum_at:
            platinums += 1
            xp += PLATINUM_XP
        for t in tset.trophies.values():
            if not t.unlocked:
                continue
            counts[t.tier] += 1
            xp += TROPHY_XP[t.tier]
            item = {"game": game.display_name, "gameId": game.id, "name": t.name, "icon": t.icon,
                    "tier": t.tier, "percent": t.percent, "at": t.unlocked_at or 0}
            recent.append(item)
            if t.percent is not None and (rarest is None or t.percent < rarest["percent"]):
                rarest = item
    seconds = sum(g.playtime_seconds for g in games)
    xp += int(seconds / 3600 * XP_PER_HOUR)
    from .challenges import load_state, META, GOAL_XP   # desafios cumpridos também dão XP
    done = load_state()
    xp += sum(m[4] for m in META if m[0] in done["meta"]) + GOAL_XP * sum(len(v) for v in done["goals"].values())
    level, into, needed = level_for(xp)

    sessions = [s for s in read_sessions() if s["game"] in by_id]
    longest = max(sessions, key=lambda s: s["end"] - s["start"], default=None)
    settings = load_settings()
    top = sorted(games, key=lambda g: g.playtime_seconds, reverse=True)[:5]
    return {
        "name": settings["name"],
        "bio": settings["bio"],
        "avatarMode": settings["avatar_mode"],
        "avatar": settings["avatar"] if settings["avatar"] in by_id else None,
        "bannerMode": settings["banner_mode"],
        "bannerGame": settings["banner_game"] if settings["banner_game"] in by_id else None,
        "bannerPos": settings["banner_pos"],
        "color": settings["color"],
        "showcaseGame": settings["showcase_game"] if settings["showcase_game"] in by_id else None,
        "showcaseTrophies": settings["showcase_trophies"],
        "frame": frame_for(level),
        "level": level, "xp": xp, "xpInto": into, "xpNeeded": needed,
        "hours": round(seconds / 3600, 1),
        "games": sum(g.installed for g in games),
        "sessions": sum(g.play_count for g in games),
        "trophies": {**counts, "total": sum(counts.values()), "platinum": platinums},
        "top": [{"id": g.id, "name": g.display_name, "seconds": g.playtime_seconds} for g in top if g.playtime_seconds],
        "recent": sorted(recent, key=lambda r: r["at"], reverse=True)[:8],
        "rarest": rarest,
        "longestSession": ({"game": by_id[longest["game"]].display_name, "seconds": longest["end"] - longest["start"]}
                           if longest else None),
        "since": min((g.added_at for g in games), default=None),
    }


def check_level_up(library) -> int | None:
    """Novo nível desde a última vez que olhamos? (o primeiro cálculo só registra, não comemora)"""
    level = compute(library)["level"]
    settings = load_settings()
    seen = settings.get("level_seen")
    if seen != level:
        settings["level_seen"] = level
        save_settings(settings)
    return level if seen is not None and level > seen else None

"""Desafios do GameDock: sequência de dias jogando, metas da semana e troféus do próprio GameDock.

Tudo calculado dos dados que já existem (sessões, troféus, capturas, status dos jogos). O que já foi
conquistado fica gravado em challenges.json (com a data), para o XP não sumir e para avisar só uma vez.
Também monta o calendário de jogo (último ano, um quadrado por dia) e o mapa "dia da semana × hora".
"""
from __future__ import annotations

import json
import random
import time
from datetime import date, datetime, timedelta

from . import achievements, captures
from .storage import _read_json, atomic_write, data_dir, read_sessions

GOAL_XP = 50

# troféus do GameDock: (id, nome, descrição, nível, XP, como medir, meta)
META = [
    ("first_session", "Primeiro play", "Jogue sua primeira sessão pelo GameDock", "bronze", 20, "sessions", 1),
    ("sessions_100", "Frequentador", "100 sessões de jogo", "silver", 60, "sessions", 100),
    ("hours_10", "Aquecendo", "10 horas jogadas", "bronze", 30, "hours", 10),
    ("hours_100", "Dedicado", "100 horas jogadas", "silver", 100, "hours", 100),
    ("hours_500", "Lenda do sofá", "500 horas jogadas", "gold", 300, "hours", 500),
    ("trophies_10", "Colecionador", "10 troféus desbloqueados", "bronze", 30, "trophies", 10),
    ("trophies_100", "Estante cheia", "100 troféus desbloqueados", "silver", 120, "trophies", 100),
    ("platinum_1", "Platinado", "Sua primeira platina", "gold", 150, "platinums", 1),
    ("platinum_10", "Caçador de platinas", "10 jogos platinados", "platinum", 500, "platinums", 10),
    ("beaten_5", "Zerador", "Marque 5 jogos como Zerado", "silver", 80, "beaten", 5),
    ("capture_1", "Fotógrafo", "Faça sua primeira captura de tela", "bronze", 15, "captures", 1),
    ("clip_1", "Diretor", "Grave seu primeiro clipe", "bronze", 20, "clips", 1),
    ("streak_7", "Semana firme", "Jogue 7 dias seguidos", "silver", 80, "best_streak", 7),
    ("streak_30", "Sem pausa", "Jogue 30 dias seguidos", "gold", 250, "best_streak", 30),
    ("retro_10", "Nostalgia", "10 horas em jogos de emulador", "silver", 80, "retro_hours", 10),
    ("variety_10", "Eclético", "Jogue 10 jogos diferentes", "silver", 60, "games_played", 10),
]

# metas semanais possíveis: (tipo, texto, alvo)
GOALS = [
    ("hours", "Jogue {n} horas", 5), ("hours", "Jogue {n} horas", 10), ("trophies", "Ganhe {n} troféus", 3),
    ("trophies", "Ganhe {n} troféus", 6), ("games", "Jogue {n} jogos diferentes", 3), ("days", "Jogue em {n} dias diferentes", 4),
    ("captures", "Faça {n} capturas", 3), ("long", "Faça uma sessão de {n} h seguidas", 2),
]


def _state_path():
    return data_dir() / "challenges.json"


def load_state() -> dict:
    return {"meta": {}, "goals": {}} | _read_json(_state_path(), {})


def _save_state(state: dict) -> None:
    atomic_write(_state_path(), json.dumps(state, ensure_ascii=False, indent=1))


def _daily_seconds(sessions: list[dict]) -> dict[date, int]:
    days: dict[date, int] = {}
    for s in sessions:
        start, end = datetime.fromtimestamp(s["start"]), datetime.fromtimestamp(s["end"])
        while start < end:
            midnight = start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            chunk = min(end, midnight)
            days[start.date()] = days.get(start.date(), 0) + int((chunk - start).total_seconds())
            start = chunk
    return days


def streaks(days: dict[date, int]) -> tuple[int, int]:
    """(sequência atual, melhor sequência), contando dias com pelo menos 5 minutos de jogo."""
    played = sorted(d for d, sec in days.items() if sec >= 300)
    best = run = 0
    prev = None
    for d in played:
        run = run + 1 if prev and d - prev == timedelta(days=1) else 1
        best = max(best, run)
        prev = d
    today = date.today()
    current = run if prev and today - prev <= timedelta(days=1) else 0
    return current, best


def _week_bounds(today: date | None = None) -> tuple[date, date]:
    today = today or date.today()
    start = today - timedelta(days=today.weekday())
    return start, start + timedelta(days=7)


def _week_goals(week_start: date) -> list[tuple]:
    rng = random.Random(week_start.toordinal())   # as mesmas 3 metas a semana toda
    picked, kinds = [], set()
    for goal in rng.sample(GOALS, len(GOALS)):
        if goal[0] not in kinds:
            picked.append(goal)
            kinds.add(goal[0])
        if len(picked) == 3:
            break
    return picked


def _stats(library) -> dict:
    games = [g for g in library.games.values() if not g.hidden and g.track]
    ids = {g.id for g in games}
    sessions = [s for s in read_sessions() if s["game"] in ids]
    unlocked_times, platinums = [], 0
    for g in games:
        tset = achievements.load(g.id)
        platinums += bool(tset.platinum_at)
        unlocked_times += [t.unlocked_at or 0 for t in tset.trophies.values() if t.unlocked]
    caps = captures.all_captures()
    days = _daily_seconds(sessions)
    current, best = streaks(days)
    return {
        "games": games, "sessions_list": sessions, "days": days, "unlocked_times": unlocked_times, "caps": caps,
        "sessions": len(sessions) or sum(g.play_count for g in games),
        "hours": sum(g.playtime_seconds for g in games) / 3600,
        "trophies": len(unlocked_times), "platinums": platinums,
        "beaten": sum(g.status in ("beaten", "platinum") for g in games),
        "captures": sum(c.get("kind") != "clip" for c in caps), "clips": sum(c.get("kind") == "clip" for c in caps),
        "streak": current, "best_streak": best,
        "retro_hours": sum(g.playtime_seconds for g in games if g.source in ("ps1", "ps2", "ps3", "nes")) / 3600,
        "games_played": sum(1 for g in games if g.playtime_seconds > 0 or g.play_count),
    }


def compute(library) -> dict:
    """Estado completo + o que acabou de ser conquistado ("new": para o aviso na tela)."""
    stats = _stats(library)
    state = load_state()
    new = []
    meta = []
    for mid, name, desc, tier, xp, metric, target in META:
        value = stats[metric]
        done_at = state["meta"].get(mid)
        if not done_at and value >= target:
            done_at = state["meta"][mid] = int(time.time())
            new.append({"kind": "meta", "name": name, "desc": desc, "tier": tier, "xp": xp})
        meta.append({"id": mid, "name": name, "desc": desc, "tier": tier, "xp": xp, "done": bool(done_at),
                     "at": done_at, "progress": min(1.0, value / target), "value": round(value, 1), "target": target})

    week_start, week_end = _week_bounds()
    lo, hi = time.mktime(week_start.timetuple()), time.mktime(week_end.timetuple())
    week_sessions = [s for s in stats["sessions_list"] if s["end"] > lo and s["start"] < hi]
    week_days = {d: sec for d, sec in stats["days"].items() if week_start <= d < week_end}
    measures = {
        "hours": sum(week_days.values()) / 3600,
        "trophies": sum(lo <= t < hi for t in stats["unlocked_times"]),
        "games": len({s["game"] for s in week_sessions}),
        "days": sum(sec >= 300 for sec in week_days.values()),
        "captures": sum(lo <= c["ts"] < hi for c in stats["caps"]),
        "long": max(((min(s["end"], hi) - max(s["start"], lo)) / 3600 for s in week_sessions), default=0),
    }
    key = week_start.isoformat()
    done_goals = state["goals"].setdefault(key, {})
    goals = []
    for kind, text, target in _week_goals(week_start):
        value = measures[kind]
        gid = f"{kind}-{target}"
        if gid not in done_goals and value >= target:
            done_goals[gid] = int(time.time())
            new.append({"kind": "goal", "name": text.format(n=target), "desc": "Meta da semana cumprida", "tier": "gold",
                        "xp": GOAL_XP})
        goals.append({"id": gid, "text": text.format(n=target), "value": round(value, 1), "target": target,
                      "done": gid in done_goals, "progress": min(1.0, value / target)})
    if new:
        _save_state(state)
    xp = sum(m["xp"] for m in meta if m["done"]) + GOAL_XP * sum(len(v) for v in state["goals"].values())
    return {"streak": stats["streak"], "bestStreak": stats["best_streak"], "meta": meta, "goals": goals,
            "weekEnds": week_end.isoformat(), "xp": xp, "new": new}


def calendar(library, days: int = 371) -> dict:
    """Quadradinhos do último ano (segundos por dia) + mapa dia-da-semana × hora (segundos)."""
    ids = {g.id for g in library.games.values() if not g.hidden and g.track}
    sessions = [s for s in read_sessions() if s["game"] in ids]
    daily = _daily_seconds(sessions)
    today = date.today()
    first = today - timedelta(days=days - 1)
    first -= timedelta(days=(first.weekday() + 1) % 7)   # a grade começa num domingo
    grid = [{"date": (first + timedelta(days=i)).isoformat(), "seconds": daily.get(first + timedelta(days=i), 0)}
            for i in range((today - first).days + 1)]
    hours = [[0] * 24 for _ in range(7)]
    for s in sessions:
        t = s["start"]
        while t < s["end"]:
            dt = datetime.fromtimestamp(t)
            nxt = min(s["end"], t + (60 - dt.minute) * 60 - dt.second)
            hours[(dt.weekday() + 1) % 7][dt.hour] += int(nxt - t)   # 0 = domingo
            t = nxt if nxt > t else t + 60
    return {"days": grid, "hours": hours, "total": sum(daily.values())}

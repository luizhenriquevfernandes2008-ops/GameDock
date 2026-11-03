"""Retrospectiva da semana ou do mês (estilo "Wrapped"): tempo, jogos, troféus e capturas do período.

Usa sessions.jsonl (sessões cortadas no começo/fim do período), os horários de desbloqueio dos troféus
e o índice de capturas. Jogos ocultos ou marcados como "não é um jogo" ficam de fora.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from . import achievements, captures
from .storage import read_sessions

WEEKDAYS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
MONTHS = ("janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
          "outubro", "novembro", "dezembro")


def period_range(period: str, offset: int = 0) -> tuple[datetime, datetime]:
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "month":
        year, month = today.year, today.month - offset
        while month < 1:
            month += 12
            year -= 1
        start = today.replace(year=year, month=month, day=1)
        end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        return start, end
    start = today - timedelta(days=today.weekday()) - timedelta(weeks=offset)  # segunda-feira
    return start, start + timedelta(days=7)


def _label(period: str, start: datetime, end: datetime) -> str:
    if period == "month":
        return f"{MONTHS[start.month - 1].capitalize()} de {start.year}"
    last = end - timedelta(days=1)
    fmt = lambda d: f"{d.day} {MONTHS[d.month - 1][:3]}"
    return f"Semana de {fmt(start)} a {fmt(last)}"


def _clip(sessions, start: float, end: float):
    for s in sessions:
        a, b = max(s["start"], start), min(s["end"], end)
        if b > a:
            yield s["game"], a, b


def compute(library, period: str = "week", offset: int = 0, live: dict | None = None) -> dict:
    start, end = period_range(period, offset)
    t0, t1 = start.timestamp(), end.timestamp()
    games = {g.id: g for g in library.games.values() if not g.hidden and g.track}
    sessions = [s for s in read_sessions() if s["game"] in games]
    sessions += [s for s in (live or {}).values() if s["game"] in games]  # sessão em andamento

    per_game: dict[str, float] = {}
    days = {(start + timedelta(days=i)).date(): 0.0 for i in range((end - start).days)}
    count, longest = 0, None
    for game_id, a, b in _clip(sessions, t0, t1):
        count += 1
        per_game[game_id] = per_game.get(game_id, 0) + (b - a)
        if longest is None or b - a > longest[1]:
            longest = (game_id, b - a)
        cursor = datetime.fromtimestamp(a)
        while cursor.timestamp() < b:  # divide entre os dias (meia-noite)
            midnight = cursor.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            chunk_end = min(b, midnight.timestamp())
            if cursor.date() in days:
                days[cursor.date()] += chunk_end - cursor.timestamp()
            cursor = midnight
    total = sum(per_game.values())

    weekday = [0.0] * 7
    for day, seconds in days.items():
        weekday[day.weekday()] += seconds

    prev_start, prev_end = period_range(period, offset + 1)
    previous = sum(b - a for _, a, b in _clip(sessions, prev_start.timestamp(), prev_end.timestamp()))

    trophies = []
    for game in games.values():
        for t in achievements.load(game.id).trophies.values():
            if t.unlocked and t.unlocked_at and t0 <= t.unlocked_at < t1:
                trophies.append({"name": t.name, "icon": t.icon, "tier": t.tier, "percent": t.percent,
                                 "game": game.display_name, "at": t.unlocked_at})
    trophies.sort(key=lambda t: t["percent"] if t["percent"] is not None else 100)

    shots = [c for c in captures.all_captures() if t0 <= c["ts"] < t1]
    top = sorted(per_game.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "period": period, "offset": offset, "label": _label(period, start, end),
        "isCurrent": offset == 0,
        "total": int(total), "previous": int(previous), "sessions": count,
        "daysPlayed": sum(1 for s in days.values() if s > 0), "daysTotal": len(days),
        "days": [{"date": d.isoformat(), "seconds": int(s)} for d, s in days.items()],
        "top": [{"id": gid, "name": games[gid].display_name, "seconds": int(sec)} for gid, sec in top[:5]],
        "longest": {"game": games[longest[0]].display_name, "seconds": int(longest[1])} if longest else None,
        "busiestDay": WEEKDAYS[weekday.index(max(weekday))] if total else None,
        "trophies": trophies[:12],
        "trophyCounts": {tier: sum(1 for t in trophies if t["tier"] == tier) for tier in ("gold", "silver", "bronze")},
        "trophyTotal": len(trophies),
        "captures": [{"path": c["path"], "game": c["game"], "ts": c["ts"]} for c in shots[-4:]],
        "captureTotal": len(shots),
    }

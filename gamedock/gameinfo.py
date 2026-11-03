"""Informações de "loja" para a página do jogo: descrição, gêneros, notas, imagens e trailers.

- Jogos que existem na Steam: appdetails (em PT-BR) + avaliações dos jogadores via GetItems.
- Outros (ex: Riot): resumo da Wikipedia em português (ou inglês, se não houver).
Tudo fica em cache por 7 dias em art/<jogo>/info.json.
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.request
from urllib.parse import quote

from . import achievements
from .art import USER_AGENT, art_dir
from .models import Game

TTL = 7 * 24 * 3600
ABOUT_MAX = 1600


def _get_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def html_to_text(raw: str) -> str:
    """Texto corrido a partir do HTML da loja (tira imagens, listas viram linhas)."""
    text = re.sub(r"(?is)<(script|style).*?</\1>", "", raw or "")
    text = re.sub(r"(?i)<br\s*/?>|</p>|</h\d>|</li>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "• ", text)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    if len(text) > ABOUT_MAX:
        text = text[:ABOUT_MAX].rsplit(" ", 1)[0] + "…"
    return text


def _steam(appid: str) -> dict:
    data = _get_json(f"https://store.steampowered.com/api/appdetails?appids={appid}&l=brazilian&cc=BR")
    d = (data.get(appid) or {}).get("data") or {}
    if not d:
        return {}
    info = {
        "source": "steam",
        "storeUrl": f"https://store.steampowered.com/app/{appid}",
        "short": html.unescape(d.get("short_description", "")),
        "about": html_to_text(d.get("about_the_game", "")),
        "developers": d.get("developers") or [],
        "publishers": d.get("publishers") or [],
        "released": (d.get("release_date") or {}).get("date"),
        "genres": [g["description"] for g in d.get("genres") or []],
        "metacritic": (d.get("metacritic") or {}).get("score"),
        "metacriticUrl": (d.get("metacritic") or {}).get("url"),
        "screenshots": [{"thumb": s["path_thumbnail"], "full": s["path_full"]} for s in d.get("screenshots") or []][:16],
        "trailers": [{"name": m.get("name", ""), "thumb": m.get("thumbnail"), "hls": m.get("hls_h264"),
                      "mp4": (m.get("mp4") or {}).get("max")}
                     for m in d.get("movies") or [] if m.get("hls_h264") or m.get("mp4")][:4],
        "website": d.get("website"),
        "age": int(d.get("required_age") or 0),
    }
    # avaliações dos jogadores (a API antiga de reviews passou a redirecionar para a página da loja)
    try:
        query = json.dumps({"ids": [{"appid": int(appid)}], "context": {"language": "brazilian", "country_code": "BR"},
                            "data_request": {"include_reviews": True, "include_basic_info": True}})
        item = _get_json("https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json="
                         + quote(query))["response"]["store_items"][0]
        summary = (item.get("reviews") or {}).get("summary_filtered") or {}
        if summary.get("review_count"):
            info["reviews"] = {"label": summary.get("review_score_label"), "percent": summary.get("percent_positive"),
                               "count": summary.get("review_count")}
        franchises = (item.get("basic_info") or {}).get("franchises") or []
        info["franchise"] = franchises[0]["name"] if franchises else None
    except (OSError, ValueError, KeyError, IndexError):
        pass
    return info


def _wikipedia(game: Game) -> dict:
    for lang in ("pt", "en"):
        try:
            summary = _get_json(f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/"
                                + quote(game.name.replace(" ", "_")))
        except (OSError, ValueError):
            continue
        description = (summary.get("description") or "").lower()
        if summary.get("type") == "standard" and ("jogo" in description or "game" in description):
            return {"source": "wikipedia", "short": summary.get("extract", ""), "about": "",
                    "storeUrl": (summary.get("content_urls") or {}).get("desktop", {}).get("page"),
                    "subtitle": summary.get("description"), "screenshots": [], "trailers": [], "genres": []}
    return {}


NEWS_TTL = 6 * 3600


def _clean_news(text: str) -> str:
    """As notícias da Steam vêm em BBCode/HTML misturados: sobra só o texto."""
    text = re.sub(r"\[(img|previewyoutube|video)[^\]]*\].*?\[/\1\]", "", text or "", flags=re.S | re.I)
    text = re.sub(r"\{STEAM_CLAN_IMAGE\}\S*", "", text)
    text = re.sub(r"\[/?(p|h\d|list|olist|\*|br|hr)[^\]]*\]", " ", text, flags=re.I)  # blocos viram espaço
    text = re.sub(r"\[/?[a-z0-9*]+(=[^\]]*)?\]", "", text, flags=re.I)
    text = html_to_text(text).replace("\n", " ")
    return re.sub(r"\s{2,}", " ", text).strip()


def _latin(text: str) -> bool:
    """A Steam mistura feeds do mundo todo; ficam os escritos em alfabeto latino (PT, EN, ES...)."""
    letters = [c for c in text if c.isalpha()]
    return not letters or sum(c.isascii() or "À" <= c <= "ɏ" for c in letters) / len(letters) > 0.8


def get_news(game: Game) -> list[dict]:
    appid = achievements.steam_appid(game)
    if not appid:
        return []
    cache = art_dir(game) / "news.json"
    if cache.is_file() and time.time() - cache.stat().st_mtime < NEWS_TTL:
        return json.loads(cache.read_text(encoding="utf-8"))
    try:
        data = _get_json(f"https://api.steampowered.com/ISteamNews/GetNewsForApp/v2/?appid={appid}"
                         "&count=12&maxlength=320&format=json")
    except (OSError, ValueError):
        return json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else []
    news = [{"title": html.unescape(item.get("title", "")), "url": item.get("url"), "date": item.get("date"),
             "feed": item.get("feedlabel") or "", "text": _clean_news(item.get("contents", ""))}
            for item in data.get("appnews", {}).get("newsitems", [])]
    news = [n for n in news if _latin(n["title"] + n["text"])][:6]
    cache.write_text(json.dumps(news, ensure_ascii=False), encoding="utf-8")
    return news


def get_info(game: Game) -> dict:
    cache = art_dir(game) / "info.json"
    if cache.is_file() and time.time() - cache.stat().st_mtime < TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    appid = achievements.steam_appid(game)
    try:
        info = _steam(appid) if appid else {}
        if not info:
            info = _wikipedia(game)
    except (OSError, ValueError):
        # sem internet: usa o cache antigo, se houver
        return json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {}
    cache.write_text(json.dumps(info, ensure_ascii=False), encoding="utf-8")
    return info

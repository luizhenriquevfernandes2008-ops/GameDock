"""Jogos grátis da semana na Epic Games Store (API pública da loja, a mesma da página "Grátis").

Grátis agora = tem promoção ativa e o preço com desconto é 0. Em breve = promoção futura de 100%.
O GameDock avisa (uma vez por jogo) quando aparece um grátis novo.
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime

from .storage import data_dir, load_config, save_config

URL = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions?locale=pt-BR&country=BR&allowCountries=BR"
TTL = 3 * 3600


def _when(text: str) -> int:
    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp())


def _image(element: dict, *kinds: str) -> str | None:
    images = {i.get("type"): i.get("url") for i in element.get("keyImages") or []}
    return next((images[k] for k in kinds if images.get(k)), None)


def _slug(element: dict) -> str | None:
    for mapping in (element.get("catalogNs") or {}).get("mappings") or []:
        if mapping.get("pageSlug"):
            return mapping["pageSlug"]
    return (element.get("productSlug") or "").split("/")[0] or None


def fetch() -> dict:
    cache = data_dir() / "freegames.json"
    if cache.is_file() and time.time() - cache.stat().st_mtime < TTL:
        try:
            return json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        req = urllib.request.Request(URL, headers={"User-Agent": "GameDock"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (OSError, ValueError):
        return json.loads(cache.read_text(encoding="utf-8")) if cache.is_file() else {"now": [], "next": []}
    now, upcoming = [], []
    for e in data.get("data", {}).get("Catalog", {}).get("searchStore", {}).get("elements", []):
        promos = e.get("promotions") or {}
        slug = _slug(e)
        item = {"id": e.get("id"), "title": e.get("title"), "description": (e.get("description") or "")[:300],
                "image": _image(e, "OfferImageWide", "DieselStoreFrontWide", "Thumbnail"),
                "tall": _image(e, "OfferImageTall", "Thumbnail"),
                "url": f"https://store.epicgames.com/pt-BR/p/{slug}" if slug else "https://store.epicgames.com/pt-BR/free-games",
                "price": (e.get("price") or {}).get("totalPrice", {}).get("fmtPrice", {}).get("originalPrice")}
        active = [o for group in promos.get("promotionalOffers") or [] for o in group.get("promotionalOffers") or []]
        future = [o for group in promos.get("upcomingPromotionalOffers") or [] for o in group.get("promotionalOffers") or []]
        discount = (e.get("price") or {}).get("totalPrice", {}).get("discountPrice", 1)
        if active and discount == 0:
            now.append(item | {"until": _when(active[0]["endDate"])})
        elif future and future[0].get("discountSetting", {}).get("discountPercentage") == 0:
            upcoming.append(item | {"from": _when(future[0]["startDate"]), "until": _when(future[0]["endDate"])})
    result = {"now": now, "next": sorted(upcoming, key=lambda i: i["from"]), "at": int(time.time())}
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


def new_ones() -> list[dict]:
    """Grátis de agora que ainda não foram avisados (e marca como avisados)."""
    config = load_config()
    if not config.get("free_games_notify", True):
        return []
    seen = set(config.get("free_games_seen") or [])
    fresh = [g for g in fetch().get("now", []) if g["id"] not in seen]
    if fresh:
        config["free_games_seen"] = sorted(seen | {g["id"] for g in fresh})[-100:]
        save_config(config)
    return fresh

"""Fontes de download no formato usado pelo Hydra Launcher.

Uma fonte é uma URL (ou arquivo local) com um JSON assim:

    {"name": "Minha fonte",
     "downloads": [{"title": "Jogo X v1.2", "uris": ["magnet:?xt=...", "https://..."],
                    "uploadDate": "2024-05-01T00:00:00Z", "fileSize": "12.3 GB"}]}

O GameDock não vem com nenhuma fonte: quem adiciona é o usuário (Downloads > Fontes).
O conteúdo das fontes é tratado como dado não confiável: só aceitamos links magnet/http(s),
limitamos o tamanho e a interface escapa todo texto.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from .storage import _read_json, _write_json, data_dir

MAX_BYTES = 80 * 1024 * 1024     # fontes grandes têm ~20 MB; acima disso é outra coisa
MAX_ITEMS = 200_000
REFRESH_AFTER = 6 * 3600         # atualiza as fontes ao abrir o app se passaram 6 h
ALLOWED_SCHEMES = ("magnet:", "http://", "https://")

_LOCK = threading.RLock()
_cache: dict[str, list[dict]] = {}   # id da fonte -> itens já normalizados (em memória)


def _dir() -> Path:
    path = data_dir() / "sources"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _index_path() -> Path:
    return _dir() / "index.json"


def list_sources() -> list[dict]:
    with _LOCK:
        return _read_json(_index_path(), [])


def _save_index(items: list[dict]) -> None:
    with _LOCK:
        _write_json(_index_path(), items)


# ---------------------------------------------------------------- títulos

_BRACKETS = re.compile(r"\[[^\]]*\]|\([^)]*\)|\{[^}]*\}")
_NOISE = re.compile(
    r"\b(v|ver\.?|version|build|update|patch)\s*[\d.]+[a-z]?\b|\bb\d{4,}\b|\b\d+(\.\d+){1,}\b|"
    r"\+\s*\d*\s*(dlcs?|bonus(es)?|ost|update|fix|multiplayer)\b.*|"
    r"\b(multi\s*\d+|repack|portable|all\s+dlcs?|win64|x64|x86|steam\s*rip|"
    r"elamigos|fitgirl|dodi|kaos|xatab|empress|codex|plaza|skidrow|tenoke|razor1911|"
    r"online[\s-]?fix|by\s+\w+)\b",
    re.IGNORECASE,
)
# palavras de edição só saem do FIM do nome ("Ultimate Chicken Horse" continua inteiro)
_TAIL = re.compile(r"[\s:–—-]+(goty|deluxe|digital|ultimate|complete|definitive|premium|gold|special|"
                   r"enhanced|edition|dlcs?|bonus|ost|windows|pc|gog|steam|game of the year)$", re.IGNORECASE)


def clean_title(title: str) -> str:
    """"Jogo X: Deluxe Edition (v1.2 + 3 DLCs) [Repack]" -> "Jogo X"; usado para agrupar e mostrar."""
    text = _BRACKETS.sub(" ", title)
    if " " not in text.strip():   # nome no estilo "Jogo.X.v1.2-GRUPO"
        text = re.sub(r"-[A-Za-z0-9]+$", "", text.strip())
        text = re.sub(r"[._]v?\d+([._]\d+)+.*$", "", text).replace("_", " ").replace(".", " ")
    text = _NOISE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" -–—:|,+/")
    previous = None
    while previous != text:
        previous = text
        text = _TAIL.sub("", text).strip(" -–—:|,+/")
    return text or title.strip()


def norm(text: str) -> str:
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[’'`]", "", text)
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _key(title: str) -> str:
    return norm(clean_title(title)).replace(" ", "")


# ---------------------------------------------------------------- leitura

def _fetch(url: str) -> bytes:
    if re.match(r"^[a-zA-Z]:[\\/]", url) or url.startswith("file://"):
        path = Path(url[7:] if url.startswith("file://") else url)
        if path.stat().st_size > MAX_BYTES:
            raise ValueError("arquivo grande demais para ser uma fonte")
        return path.read_bytes()
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError("use um link http(s) ou o caminho de um arquivo .json")
    req = urllib.request.Request(url, headers={"User-Agent": "GameDock", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=40) as resp:
        data = resp.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("a fonte é grande demais")
    return data


def _size_bytes(text) -> int | None:
    if isinstance(text, (int, float)):
        return int(text)
    m = re.match(r"\s*([\d.,]+)\s*([KMGT]?)i?B", str(text or ""), re.IGNORECASE)
    if not m:
        return None
    try:
        value = float(m.group(1).replace(",", "."))
    except ValueError:
        return None
    return int(value * 1024 ** "BKMGT".index((m.group(2) or "B").upper()))


def _date(text) -> str | None:
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        m = re.search(r"\d{4}-\d{2}-\d{2}", str(text))
        return m.group(0) if m else None


def parse(raw: bytes) -> tuple[str, list[dict]]:
    """Valida e normaliza uma fonte. Itens sem link aceito são descartados."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValueError("o link não devolveu um JSON válido") from exc
    if not isinstance(data, dict) or not isinstance(data.get("downloads"), list):
        raise ValueError('formato desconhecido: falta a lista "downloads"')
    name = str(data.get("name") or "Fonte sem nome")[:80]
    items = []
    for entry in data["downloads"][:MAX_ITEMS]:
        if not isinstance(entry, dict):
            continue
        title = str(entry.get("title") or "").strip()[:300]
        uris = [u.strip() for u in entry.get("uris") or [] if isinstance(u, str)]
        uris = [u for u in uris if u.lower().startswith(ALLOWED_SCHEMES) and len(u) < 8000][:8]
        if not title or not uris:
            continue
        items.append({"title": title, "uris": uris, "size": str(entry.get("fileSize") or "")[:30],
                      "bytes": _size_bytes(entry.get("fileSize")), "date": _date(entry.get("uploadDate"))})
    if not items:
        raise ValueError("a fonte não tem nenhum download com link magnet ou http(s)")
    return name, items


def _store(source_id: str, items: list[dict]) -> None:
    for item in items:
        item["key"] = _key(item["title"])
        item["clean"] = clean_title(item["title"])
        item["norm"] = norm(item["title"])
    with _LOCK:
        _cache[source_id] = items
    (_dir() / f"{source_id}.json").write_text(json.dumps(items, ensure_ascii=False), encoding="utf-8")


def _items(source_id: str) -> list[dict]:
    with _LOCK:
        if source_id not in _cache:
            _cache[source_id] = _read_json(_dir() / f"{source_id}.json", [])
        return _cache[source_id]


def add_source(url: str) -> dict:
    url = (url or "").strip().strip('"')
    if not url:
        return {"ok": False, "error": "cole o link da fonte"}
    sources = list_sources()
    if any(s["url"] == url for s in sources):
        return {"ok": False, "error": "essa fonte já foi adicionada"}
    try:
        name, items = parse(_fetch(url))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": str(exc)}
    source_id = hashlib.sha1(url.encode()).hexdigest()[:12]
    _store(source_id, items)
    entry = {"id": source_id, "url": url, "name": name, "count": len(items), "updated": time.time(), "error": None}
    with _LOCK:
        sources = [s for s in list_sources() if s["id"] != source_id] + [entry]
        _save_index(sources)
    return {"ok": True, "source": entry}


def remove_source(source_id: str) -> list[dict]:
    with _LOCK:
        _save_index([s for s in list_sources() if s["id"] != source_id])
        _cache.pop(source_id, None)
        (_dir() / f"{source_id}.json").unlink(missing_ok=True)
    return list_sources()


def refresh(force: bool = False) -> list[dict]:
    """Baixa de novo as fontes (todas ou só as antigas). Se uma falhar, fica a cópia anterior."""
    for source in list_sources():
        if not force and time.time() - source.get("updated", 0) < REFRESH_AFTER:
            continue
        try:
            name, items = parse(_fetch(source["url"]))
            _store(source["id"], items)
            changes = {"name": name, "count": len(items), "updated": time.time(), "error": None}
        except (OSError, ValueError) as exc:
            changes = {"error": str(exc)[:200], "updated": time.time()}
        with _LOCK:
            current = list_sources()
            for s in current:
                if s["id"] == source["id"]:
                    s.update(changes)
            _save_index(current)
    return list_sources()


# ---------------------------------------------------------------- busca

def _link_kind(uri: str) -> str:
    return "torrent" if uri.lower().startswith("magnet:") or uri.lower().split("?")[0].endswith(".torrent") else "direto"


def search(query: str, limit: int = 60) -> list[dict]:
    """Busca em todas as fontes. Resultado agrupado pelo nome limpo do jogo, cada grupo com as opções
    (fonte, título original, tamanho, data, tipo de link), mais recentes primeiro."""
    words = norm(query).split()
    if not words:
        return []
    exact = "".join(words)
    groups: dict[str, dict] = {}
    for source in list_sources():
        for item in _items(source["id"]):
            text = item.get("norm") or norm(item["title"])
            if not all(w in text for w in words):
                continue
            group = groups.setdefault(item["key"], {"key": item["key"], "name": item["clean"], "options": []})
            group["options"].append({
                "source": source["name"], "sourceId": source["id"], "title": item["title"], "size": item["size"],
                "bytes": item.get("bytes"), "date": item["date"], "uris": item["uris"],
                "kinds": sorted({_link_kind(u) for u in item["uris"]}),
            })

    def rank(group: dict) -> tuple:
        key = group["key"]
        return (key != exact, not key.startswith(exact), len(key), group["name"].lower())

    result = sorted(groups.values(), key=rank)[:limit]
    for group in result:
        group["options"].sort(key=lambda o: o["date"] or "", reverse=True)
        group["sources"] = sorted({o["source"] for o in group["options"]})
    return result


def stats() -> dict:
    sources = list_sources()
    return {"sources": sources, "total": sum(s.get("count", 0) for s in sources)}


# ---------------------------------------------------------------- capas (loja da Steam)

_ART_LOCK = threading.Lock()
STEAM_IMG = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{appid}/{file}"


def steam_art(name: str) -> dict:
    """Capa e fundo de um resultado da busca, pela loja da Steam (só com nome idêntico, para não errar o jogo).
    Fica em cache: cada nome é procurado uma vez."""
    key = _key(name)
    cache_path = _dir() / "art.json"
    with _ART_LOCK:
        cache = _read_json(cache_path, {})
    if key in cache and time.time() - cache[key].get("t", 0) < 30 * 86400:
        return cache[key]
    from urllib.parse import quote
    url = f"https://store.steampowered.com/api/storesearch/?term={quote(clean_title(name))}&l=english&cc=US"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "GameDock"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            items = json.loads(resp.read()).get("items", [])
    except (OSError, ValueError):
        return {}
    match = next((i for i in items if _key(i.get("name", "")) == key), None)
    result = {"t": time.time()}
    if match:
        appid = match["id"]
        result.update(appid=appid, cover=STEAM_IMG.format(appid=appid, file="library_600x900.jpg"),
                      hero=STEAM_IMG.format(appid=appid, file="library_hero.jpg"),
                      header=STEAM_IMG.format(appid=appid, file="header.jpg"))
    with _ART_LOCK:
        cache = _read_json(cache_path, {})
        cache[key] = result
        _write_json(cache_path, cache)
    return result

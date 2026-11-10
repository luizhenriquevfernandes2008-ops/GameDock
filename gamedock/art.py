"""Arte dos jogos (capa, fundo, logo, ícone) para a interface.

Ordem de busca para jogos da Steam:
  1. cache local da própria Steam (appcache/librarycache) — instantâneo e offline
  2. CDN da Steam — baixa uma vez e guarda em %APPDATA%/GameDock/art
Jogos da Riot: o Riot Client publica um "tema" por jogo (card, fundo, logo) numa
configuração pública; usamos o mesmo tema. O card é horizontal e tem o logo impresso,
então a interface recorta o lado dos personagens e sobrepõe o logo oficial.
Para os demais jogos, usamos o ícone do atalho (.ico) ou extraímos o do .exe.
A interface gera uma capa tipográfica quando não há imagem.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote, urljoin

from . import sgdb
from .models import Game
from .scanners.base import slug
from .scanners.steam import SteamScanner
from .storage import data_dir, load_config

STEAM_FILES = {
    "cover": "library_600x900.jpg",
    "hero": "library_hero.jpg",
    "logo": "logo.png",
}
# a Wikimedia exige um User-Agent descritivo
USER_AGENT = "GameDock/0.2 (personal Windows game launcher; python-urllib)"
STEAM_ASSETS = "https://shared.steamstatic.com/store_item_assets/"
EDITION_SUFFIX = re.compile(
    r"\s(ultimate|definitive|complete|deluxe|gold|goty|game of the year|enhanced|remastered|anniversary|"
    r"directors cut|director s cut|special|premium|standard|digital|collectors)( edition)?$|\sedition$"
)
STEAM_CDN = (
    "https://shared.steamstatic.com/store_item_assets/steam/apps/{appid}/{file}",
    "https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/{file}",
)
RIOT_CONFIG = "https://clientconfig.rpg.riotgames.com/api/v1/config/public?namespace=keystone.products"
RIOT_THEME = "https://{host}.secure.dyn.riotcdn.net/channels/public/rccontent/theme/"
# usados se a configuração pública da Riot estiver fora do ar
RIOT_FALLBACK_MANIFESTS = {
    "league_of_legends": RIOT_THEME.format(host="lol") + "manifest_default.json",
    "valorant": RIOT_THEME.format(host="valorant") + "03/manifest.json",
    "lion": RIOT_THEME.format(host="lion") + "manifest.json",
    "teamfighttactics": RIOT_THEME.format(host="teamfighttactics") + "manifest.json",
    "bacon": RIOT_THEME.format(host="bacon") + "manifest.json",
}
RIOT_PRODUCTS_BY_NAME = {
    "league of legends": "league_of_legends",
    "valorant": "valorant",
    "2xko": "lion",
    "teamfight tactics": "teamfighttactics",
    "legends of runeterra": "bacon",
}
# lado do card horizontal onde ficam os personagens (o outro lado tem o logo impresso)
RIOT_COVER_FOCUS = {"league_of_legends": "left"}
CACHE_TTL = 7 * 24 * 3600
MAX_HERO_BYTES = 1_500_000

MIME = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".ico": "image/x-icon",
        ".svg": "image/svg+xml", ".webp": "image/webp"}

_memory: dict[str, dict] = {}
_lock = threading.Lock()


def art_dir(game: Game) -> Path:
    path = data_dir() / "art" / slug(game.id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def as_url(path: Path | None, game: Game) -> str | None:
    """URL servida pelo servidor local da interface (/art/...).

    Arquivos de fora do cache (ex: imagens do cache da própria Steam, ícone .ico da Riot)
    são copiados para a pasta do jogo, porque o servidor só expõe a pasta art/.
    O ?v=<mtime> faz o navegador buscar de novo quando a imagem muda.
    """
    if not path or not path.is_file():
        return None
    root = (data_dir() / "art").resolve()
    full = path.resolve()
    if not full.is_relative_to(root):
        copy = art_dir(game) / f"ext-{path.name}"
        if not copy.is_file() or copy.stat().st_mtime < full.stat().st_mtime:
            shutil.copy2(full, copy)
        full = copy.resolve()
    return f"/art/{quote(full.relative_to(root).as_posix())}?v={int(full.stat().st_mtime)}"


# ---------------------------------------------------------------- Steam

def _steam_cache() -> Path | None:
    root = SteamScanner().steam_root()
    cache = root / "appcache" / "librarycache" if root else None
    return cache if cache and cache.is_dir() else None


def _steam_local(appid: str, file: str) -> Path | None:
    cache = _steam_cache()
    if cache is None:
        return None
    folder = cache / appid
    if (folder / file).is_file():
        return folder / file
    if folder.is_dir():  # a Steam às vezes guarda em subpastas com hash
        for found in folder.rglob(file):
            return found
    legacy = cache / f"{appid}_{file}"  # layout antigo: 221100_library_600x900.jpg
    return legacy if legacy.is_file() else None


def _download(urls: list[str], dest: Path) -> Path | None:
    missing = dest.with_name(dest.name + ".missing")
    if dest.is_file():
        return dest
    if missing.exists():  # já tentamos e não existe; não insistir a cada abertura
        return None
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=8) as resp:
                dest.write_bytes(resp.read())
                return dest
        except OSError:
            continue
    missing.touch()
    return None


def _steam_asset_urls(appid: str, game: Game) -> dict[str, str]:
    """Imagens novas da Steam ficam em caminhos com hash; esta API pública (sem chave) informa quais."""
    query = json.dumps({"ids": [{"appid": int(appid)}], "context": {"language": "english", "country_code": "US"},
                        "data_request": {"include_assets": True}})
    url = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/?input_json=" + quote(query)
    data = _fetch_json(url, art_dir(game) / "steam-assets.json") or {}
    items = data.get("response", {}).get("store_items") or [{}]
    assets = items[0].get("assets") or {}
    template = assets.get("asset_url_format")
    if not template:
        return {}
    keys = {"cover": "library_capsule", "hero": "library_hero", "logo": "library_logo"}
    urls = {kind: STEAM_ASSETS + template.replace("${FILENAME}", assets[key])
            for kind, key in keys.items() if assets.get(key)}
    if assets.get("library_capsule", "").startswith("portrait"):
        # capa gerada automaticamente pela Steam (banner horizontal + borrão): só como último recurso
        urls["auto_cover"] = urls.pop("cover")
    if "hero" not in urls:
        # jogos antigos não têm arte de biblioteca: a 1ª captura de tela oficial vira o fundo
        details = _fetch_json(f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=screenshots",
                              art_dir(game) / "steam-details.json") or {}
        shots = (details.get(appid) or {}).get("data", {}).get("screenshots") or []
        if shots and shots[0].get("path_full"):
            urls["hero"] = shots[0]["path_full"]
    return urls


def _steam_art(game: Game, appid: str) -> dict[str, Path | None]:
    result = {}
    asset_urls = None
    for kind, file in STEAM_FILES.items():
        local = _steam_local(appid, file)
        dest = art_dir(game) / file
        if local is None:
            if asset_urls is None:
                asset_urls = _steam_asset_urls(appid, game)
            # se a Steam passou a informar outra imagem (arte nova), o cache antigo é descartado
            source = dest.with_name(dest.name + ".src")
            current = asset_urls.get(kind)
            recorded = source.read_text(encoding="utf-8") if source.is_file() else None
            if current and recorded != current:
                dest.unlink(missing_ok=True)
                dest.with_name(dest.name + ".missing").unlink(missing_ok=True)
            urls = ([current] if current else []) + [u.format(appid=appid, file=file) for u in STEAM_CDN]
            local = _download(urls, dest)
            if local and current and recorded != current:
                source.write_text(current, encoding="utf-8")
        result[kind] = local
    if result["cover"] is None:
        auto = (asset_urls if asset_urls is not None else _steam_asset_urls(appid, game)).get("auto_cover")
        result["auto_cover"] = auto  # URL; baixada só se não houver capa melhor
    return result


# ---------------------------------------------------------------- Wikipedia (capa da caixa)

def wikipedia_cover(game: Game, names: list[str]) -> Path | None:
    """Capa da caixa a partir do artigo do jogo na Wikipedia em inglês.

    Só aceita um artigo cujo título seja idêntico a um dos nomes (sem "(video game)"),
    que seja descrito como jogo, e cuja imagem principal seja vertical (capa, não screenshot)."""
    folder = art_dir(game)
    found = [p for p in folder.glob("cover-wiki.*") if p.suffix != ".missing"]
    if found:
        return found[0]
    missing = folder / "cover-wiki.missing"
    query = "|".join(names)
    if missing.exists():
        try:
            # já procurou com estes nomes (marcador antigo, vazio: procurou com game.name)
            if missing.read_text(encoding="utf-8") in (query, "" if query == game.name else query):
                return None
        except OSError:
            return None
    wanted = {_title_key(n) for n in names if n}
    search = _fetch_json(
        "https://en.wikipedia.org/w/api.php?action=query&list=search&format=json&srlimit=6&srsearch="
        + quote(f"{names[0]} video game"),
        folder / f"wiki-search-{slug(names[0])}.json",
    ) or {}
    for hit in search.get("query", {}).get("search", []):
        title = hit.get("title", "")
        if _title_key(re.sub(r"\s*\(.*?\)\s*$", "", title)) not in wanted:
            continue
        summary = _fetch_json("https://en.wikipedia.org/api/rest_v1/page/summary/" + quote(title.replace(" ", "_")),
                              folder / f"wiki-{slug(title)}.json") or {}
        image = summary.get("originalimage") or {}
        is_game = "game" in (summary.get("description") or "").lower()
        if is_game and image.get("source") and image.get("height", 0) > image.get("width", 0):
            url = image["source"].split("?", 1)[0]  # sem parâmetros de rastreio (o servidor recusa bots com eles)
            return _download([url], folder / f"cover-wiki{Path(url).suffix.lower() or '.jpg'}")
    missing.write_text(query, encoding="utf-8")
    return None


def _normalize_title(name: str) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).strip()
    previous = None
    while previous != text:  # remove sufixos de edição: "ultimate edition", "goty", "remastered"...
        previous = text
        text = EDITION_SUFFIX.sub("", text).strip()
    return text


# etiquetas no fim do nome que não fazem parte do título: "SILENT HILL 3 (Reloaded)",
# "Jogo [GOG]", "Jogo (2003)", "Jogo (Repack)"
NAME_TAGS = re.compile(r"\s*(\([^()]*\)|\[[^\[\]]*\])\s*$")


def search_name(game: Game) -> str:
    """Nome usado para procurar arte: o que o usuário deu ao jogo (se renomeou), sem etiquetas."""
    name = (game.custom_name or game.name or "").strip()
    previous = None
    while previous != name:
        previous = name
        name = NAME_TAGS.sub("", name).strip()
    return name or game.name


def _title_key(name: str) -> str:
    """Chave de comparação: normalizado e sem espaços ("MoonSweepers" == "Moon Sweepers").
    Números continuam contando: "Lords of Shadow" != "Lords of Shadow 2"."""
    return _normalize_title(name).replace(" ", "")


def steam_store_match(game: Game) -> str | None:
    """Procura o jogo na loja da Steam pelo nome. Só aceita nome idêntico (ignorando pontuação e
    sufixos de edição), para não confundir "Lords of Shadow" com "Lords of Shadow 2"."""
    name = search_name(game)
    cache = art_dir(game) / "steam-match.json"
    if cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL:
        try:
            cached = json.loads(cache.read_text(encoding="utf-8"))
        except ValueError:
            cached = {}
        if cached.get("query") == name:   # nome mudou (renomeado / regra nova): busca de novo
            return cached.get("appid")
    wanted = _title_key(name)
    url = f"https://store.steampowered.com/api/storesearch/?term={quote(name)}&l=english&cc=US"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=10) as resp:
            items = json.loads(resp.read()).get("items", [])
    except (OSError, ValueError):
        return None  # sem internet: tenta de novo na próxima vez
    appid = next((str(i["id"]) for i in items if _title_key(i.get("name", "")) == wanted), None)
    cache.write_text(json.dumps({"appid": appid, "query": name}), encoding="utf-8")
    return appid


# ---------------------------------------------------------------- Riot

def riot_product(game: Game) -> str | None:
    for arg in game.launch_args:
        if arg.lower().startswith("--launch-product="):
            return arg.split("=", 1)[1]
    return RIOT_PRODUCTS_BY_NAME.get(game.name.lower())


def _fetch_json(url: str, cache: Path) -> dict | None:
    """JSON com cache em disco por CACHE_TTL; se a rede falhar, usa o cache antigo."""
    fresh = cache.is_file() and time.time() - cache.stat().st_mtime < CACHE_TTL
    if not fresh:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=10) as resp:
                cache.write_bytes(resp.read())
        except OSError:
            pass
    try:
        return json.loads(cache.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _riot_manifest_url(product: str) -> str | None:
    config = _fetch_json(RIOT_CONFIG, data_dir() / "art" / "riot-products.json") or {}
    entry = config.get(f"keystone.products.{product}.patchlines.live")
    urls = re.findall(r'"(?:default_)?theme_manifest"\s*:\s*"(https://[^"]+)"', json.dumps(entry or {}))
    if urls:
        # manifest padrão (sem idioma/região) primeiro
        return min(urls, key=lambda u: (not u.endswith(("/manifest.json", "/manifest_default.json")), len(u)))
    return RIOT_FALLBACK_MANIFESTS.get(product)


def _riot_art(game: Game, product: str) -> dict[str, Path | None]:
    folder = art_dir(game)
    manifest_url = _riot_manifest_url(product)
    manifest = _fetch_json(manifest_url, folder / "theme.json") if manifest_url else None
    if not manifest:
        return {}
    files = {
        "cover": manifest.get("game_library", {}).get("game_card_image"),
        "hero": manifest.get("product_details", {}).get("background_image") or manifest.get("splash_image"),
        "logo": manifest.get("logo_image"),
    }
    result = {}
    for kind, rel in files.items():
        if not rel:
            result[kind] = None
            continue
        url = urljoin(manifest_url, rel)
        path = _download([url], folder / f"{kind}{Path(rel).suffix.lower()}")
        if kind == "hero" and path and path.stat().st_size > MAX_HERO_BYTES:
            path = shrink_image(path)
        result[kind] = path
    return result


# ---------------------------------------------------------------- PowerShell (System.Drawing)

def _powershell(command: str) -> None:
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
            timeout=30, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _ps_path(path: Path) -> str:
    return str(path).replace("'", "''")


_SHRINK = (
    "Add-Type -AssemblyName System.Drawing;"
    "$s=[System.Drawing.Image]::FromFile('{src}');"
    "$b=New-Object System.Drawing.Bitmap $s,1920,([int]($s.Height*1920/$s.Width));"
    "$e=[System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders()|Where-Object {{$_.MimeType -eq 'image/jpeg'}};"
    "$p=New-Object System.Drawing.Imaging.EncoderParameters 1;"
    "$p.Param[0]=New-Object System.Drawing.Imaging.EncoderParameter ([System.Drawing.Imaging.Encoder]::Quality),([long]85);"
    "$b.Save('{dst}',$e,$p);$s.Dispose()"
)


def shrink_image(path: Path) -> Path:
    """Reduz fundos enormes (ex: 4K de 4 MB) para 1920 px, para a interface não travar."""
    out = path.with_name(path.stem + "-1920.jpg")
    if not out.is_file():
        _powershell(_SHRINK.format(src=_ps_path(path), dst=_ps_path(out)))
    return out if out.is_file() else path


# ---------------------------------------------------------------- ícones

_EXTRACT_ICON = (
    "Add-Type -AssemblyName System.Drawing;"
    "$i=[System.Drawing.Icon]::ExtractAssociatedIcon('{exe}');"
    "$i.ToBitmap().Save('{out}',[System.Drawing.Imaging.ImageFormat]::Png)"
)


def _exe_icon(game: Game) -> Path | None:
    exe = game.exe and Path(game.exe)
    if not exe or not exe.is_file():
        return None
    out = art_dir(game) / "icon.png"
    if not out.is_file():
        _powershell(_EXTRACT_ICON.format(exe=_ps_path(exe), out=_ps_path(out)))
    return out if out.is_file() else None


def _icon(game: Game) -> Path | None:
    if game.icon and Path(game.icon).suffix.lower() in MIME and Path(game.icon).is_file():
        return Path(game.icon)
    return _exe_icon(game)


def _tracked_download(url: str, dest: Path) -> Path | None:
    """Baixa `url` em `dest`; se a melhor imagem mudar (outra URL), substitui o cache."""
    source = dest.with_name(dest.name + ".src")
    if dest.is_file() and source.is_file() and source.read_text(encoding="utf-8") == url:
        return dest
    dest.unlink(missing_ok=True)
    dest.with_name(dest.name + ".missing").unlink(missing_ok=True)
    path = _download([url], dest)
    if path:
        source.write_text(url, encoding="utf-8")
    return path


def custom_cover(game: Game) -> Path | None:
    return next((p for p in art_dir(game).glob("custom-cover.*") if p.suffix.lower() in MIME), None)


def set_custom_cover(game: Game, source: Path | None) -> None:
    """Copia a imagem escolhida para o cache do jogo (None = volta para a capa automática)."""
    for old in art_dir(game).glob("custom-cover.*"):
        old.unlink(missing_ok=True)
    if source:
        shutil.copy2(source, art_dir(game) / f"custom-cover{source.suffix.lower()}")
    with _lock:
        _memory.pop(game.id, None)


def clear_memory() -> None:
    """Esquece a arte já montada (ex: depois de salvar a chave do SteamGridDB)."""
    with _lock:
        _memory.clear()


# ---------------------------------------------------------------- API

def get_art(game: Game) -> dict:
    """Retorna {"cover", "hero", "logo", "icon"} como data URIs (ou None), mais dicas de layout:
    "coverFocus" (left/center/right) e "coverLogo" (sobrepor o logo na capa)."""
    with _lock:
        if game.id in _memory:
            return _memory[game.id]
    paths: dict[str, Path | None] = {"cover": None, "hero": None, "logo": None}
    layout = {"coverFocus": "center", "coverLogo": False}
    product = riot_product(game)
    appid = None
    if game.source == "steam" and game.id.startswith("steam:"):
        appid = game.id.split(":", 1)[1]
        paths.update(_steam_art(game, appid))
    elif product:
        paths.update(_riot_art(game, product))
        layout = {"coverFocus": RIOT_COVER_FOCUS.get(product, "right"), "coverLogo": bool(paths["logo"])}
    elif appid := steam_store_match(game):
        # jogo de fora da Steam, mas que existe na loja: usa a arte oficial de lá
        paths.update(_steam_art(game, appid))
    auto_cover = paths.pop("auto_cover", None)
    if game.source == "ps3":   # jogos de PS3 trazem fundo (PIC1) e ícone (ICON0) dentro do próprio jogo
        from .emulators import game_art
        local = game_art(game)
        paths["hero"] = paths["hero"] or local.get("hero")

    # SteamGridDB (com chave): capa em alta para quem não tem capa oficial da biblioteca da Steam
    # (Riot, jogos antigos, jogos locais) e preenche fundo/logo que faltarem.
    key = load_config().get("steamgriddb_key")
    official_cover = bool(paths["cover"]) and not product
    wanted = {k for k in ("hero", "logo") if not paths[k]} | (set() if official_cover else {"cover"})
    if key and wanted:
        urls = sgdb.art_urls(search_name(game), appid, key, art_dir(game), _title_key, wanted)
        for kind, url in urls.items():
            if path := _tracked_download(url, art_dir(game) / f"sgdb-{kind}{Path(url).suffix.lower()}"):
                paths[kind] = shrink_image(path) if kind == "hero" and path.stat().st_size > MAX_HERO_BYTES else path
        if "cover" in urls and paths["cover"]:
            layout = {"coverFocus": "center", "coverLogo": False}  # capa vertical de verdade: sem recorte

    if game.source in ("ps1", "ps2", "ps3", "nes") and (not paths["cover"] or not paths["hero"]):
        # jogos de console: capa da caixa e tela do jogo da coleção do RetroArch (libretro-thumbnails)
        from . import retroart
        found = retroart.art(game, art_dir(game))
        paths["cover"] = paths["cover"] or found.get("cover")
        paths["hero"] = paths["hero"] or found.get("hero")
    if not paths["cover"] and not product:
        # sem capa de biblioteca: capa da caixa (Wikipedia) > capa automática da Steam
        paths["cover"] = wikipedia_cover(game, [search_name(game)]) or (
            _download([auto_cover], art_dir(game) / "steam-portrait.png") if auto_cover else None)
    # capa escolhida pelo usuário (página do jogo → Trocar capa) vence tudo
    if custom := custom_cover(game):
        paths["cover"] = custom
        layout = {"coverFocus": "center", "coverLogo": False}
    paths["icon"] = None if paths["cover"] else _icon(game)
    art = {kind: as_url(path, game) for kind, path in paths.items()} | layout
    with _lock:
        _memory[game.id] = art
    return art

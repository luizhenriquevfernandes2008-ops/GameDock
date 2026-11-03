"""Backup local dos saves de cada jogo (a base para o backup na nuvem da fase final).

Onde cada jogo guarda o save vem do manifesto público do Ludusavi (github.com/mtkennerly/ludusavi-manifest):
um YAML de ~17 MB com milhares de jogos. Ler tudo com o PyYAML levaria minutos, então o GameDock faz um
índice (nome do jogo → trecho do arquivo, id da Steam → nome) e só interpreta o trecho do jogo pedido.

Jogos de emulador: o save fica dentro do emulador (RPCS3: savedata do jogo; PCSX2/DuckStation: memory cards;
RetroArch: .srm e save states).

Backups: Documentos\\GameDock\\Saves\\<jogo>\\<data>.zip, com um gamedock-save.json dentro dizendo de onde veio
cada arquivo (para restaurar no lugar certo). Guarda os últimos N; um backup igual ao anterior não é repetido.
Restaurar sempre faz antes um backup do save atual ("antes de restaurar").
"""
from __future__ import annotations

import ctypes
import glob
import hashlib
import json
import os
import re
import subprocess
import time
import urllib.request
import zipfile
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

from .models import Game
from .scanners.base import slug

MANIFEST_URL = "https://raw.githubusercontent.com/mtkennerly/ludusavi-manifest/master/data/manifest.yaml"
MANIFEST_TTL = 7 * 24 * 3600
KEEP_DEFAULT = 10
CREATE_NO_WINDOW = 0x08000000
# onde os emuladores de Steam (cópias fora da Steam) guardam o que seria a "nuvem Steam" do jogo
STEAM_EMU_SAVES = (("<winPublic>/Documents/OnlineFix", "Saves/"), ("<winAppData>/Goldberg SteamEmu Saves", "remote/"),
                   ("<winAppData>/GSE Saves", "remote/"), ("<winPublic>/Documents/Steam/CODEX", "remote/"),
                   ("<winAppData>/Steam/CODEX", "remote/"))


def _folder() -> Path:
    path = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GameDock" / "saves"
    path.mkdir(parents=True, exist_ok=True)
    return path


def backups_root() -> Path:
    path = _known_folder("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}") / "GameDock" / "Saves"   # Documentos
    path.mkdir(parents=True, exist_ok=True)
    return path


def _known_folder(guid: str) -> Path:
    """Pastas especiais do Windows (Documentos pode estar no OneDrive, por exemplo)."""
    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]
    g = GUID()
    ctypes.windll.ole32.CLSIDFromString(guid, ctypes.byref(g))
    out = ctypes.c_wchar_p()
    if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) != 0:
        return Path.home() / "Documents"
    path = Path(out.value)
    ctypes.windll.ole32.CoTaskMemFree(out)
    return path


# ---------------------------------------------------------------- manifesto + índice

def _manifest() -> Path | None:
    path = _folder() / "manifest.yaml"
    if path.is_file() and time.time() - path.stat().st_mtime < MANIFEST_TTL:
        return path
    try:
        req = urllib.request.Request(MANIFEST_URL, headers={"User-Agent": "GameDock"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if len(data) > 1_000_000:
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(path)
            (_folder() / "index.json").unlink(missing_ok=True)
    except OSError:
        pass
    return path if path.is_file() else None


_INDEX: dict | None = None


def _index() -> dict | None:
    """{"names": {nome: [início, fim]}, "steam": {appid: nome}, "dirs": {pasta: nome}} (em bytes do arquivo)."""
    global _INDEX
    manifest = _manifest()
    if not manifest:
        return None
    cache = _folder() / "index.json"
    if _INDEX and _INDEX.get("mtime") == manifest.stat().st_mtime:
        return _INDEX
    if cache.is_file():
        try:
            data = json.loads(cache.read_text(encoding="utf-8"))
            if data.get("mtime") == manifest.stat().st_mtime:
                _INDEX = data
                return data
        except ValueError:
            pass
    names, steam, dirs = {}, {}, {}
    raw = manifest.read_bytes()
    starts = [m.start() for m in re.finditer(rb"^(?=[^\s#-])", raw, re.M)]   # linhas sem recuo = um jogo
    starts.append(len(raw))
    for begin, end in zip(starts, starts[1:]):
        header = raw[begin:raw.index(b"\n", begin)].decode("utf-8", "replace").rstrip(":\r ")
        if header.startswith('"') and header.endswith('"'):
            header = json.loads(header) if "\\" in header else header[1:-1]
        names[header] = [begin, end]
        block = raw[begin:end]
        if m := re.search(rb"\n  steam:\s*\n    id: (\d+)", block):
            steam[m.group(1).decode()] = header
        if m := re.search(rb"\n  installDir:\s*\n((?:    .+\n)+)", block):
            for line in m.group(1).decode("utf-8", "replace").splitlines():
                folder = line.strip().rstrip(": {}").strip('"')
                dirs.setdefault(folder.lower(), header)
    _INDEX = {"mtime": manifest.stat().st_mtime, "names": names, "steam": steam, "dirs": dirs}
    cache.write_text(json.dumps(_INDEX), encoding="utf-8")
    return _INDEX


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def find_entry(game: Game) -> tuple[str, dict] | None:
    index = _index()
    if not index:
        return None
    name = None
    if game.id.startswith("steam:"):
        name = index["steam"].get(game.id.split(":", 1)[1])
    if not name and game.name in index["names"]:
        name = game.name
    if not name:
        wanted = _norm(game.name)
        name = next((n for n in index["names"] if _norm(n) == wanted), None)
    if not name and game.install_dir:
        name = index["dirs"].get(Path(game.install_dir).name.lower())
    if not name:
        return None
    import yaml
    begin, end = index["names"][name]
    with open(_manifest(), "rb") as f:
        f.seek(begin)
        block = f.read(end - begin)
    data = yaml.safe_load(block) or {}
    return name, next(iter(data.values()), {}) or {}


# ---------------------------------------------------------------- caminhos

def _steam_roots() -> list[Path]:
    from .scanners.steam import SteamScanner
    scanner = SteamScanner()
    main = scanner.steam_root()
    roots = [main] if main else []
    try:
        roots += [Path(p) for p in scanner.library_folders(main) if main and Path(p) not in roots]
    except Exception:
        pass
    return [r for r in roots if r]


def _placeholders(game: Game) -> dict[str, list[str]]:
    home = Path.home()
    local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
    base = game.install_dir or (str(Path(game.exe).parent) if game.exe else "")
    values = {
        "<home>": [str(home)],
        "<osUserName>": [os.environ.get("USERNAME", "*")],
        "<winAppData>": [os.environ.get("APPDATA", str(home / "AppData/Roaming"))],
        "<winLocalAppData>": [str(local)],
        "<winLocalAppDataLow>": [str(local.parent / "LocalLow")],
        "<winDocuments>": [str(_known_folder("{FDD39AD0-238F-46AF-ADB4-6C85480369C7}"))],
        "<winPublic>": [os.environ.get("PUBLIC", r"C:\Users\Public")],
        "<winProgramData>": [os.environ.get("PROGRAMDATA", r"C:\ProgramData")],
        "<winDir>": [os.environ.get("WINDIR", r"C:\Windows")],
        "<storeUserId>": ["*"],
        "<storeGameId>": [game.id.split(":", 1)[1]] if game.id.startswith("steam:") else ["*"],
        "<base>": [base] if base else [],
        "<game>": [Path(base).name] if base else [],
        "<root>": [str(r) for r in _steam_roots()] if game.source == "steam" else ([str(Path(base).parent)] if base else []),
    }
    return values


def _applies(rules: list | None, game: Game) -> bool:
    if not rules:
        return True
    for rule in rules:
        if rule.get("os") not in (None, "windows"):
            continue
        store = rule.get("store")
        if store and store != game.source:
            continue
        return True
    return False


def _expand(pattern: str, values: dict[str, list[str]]) -> list[str]:
    results = [pattern]
    for key, options in values.items():
        if key not in pattern:
            continue
        if not options:
            return []
        results = [r.replace(key, o) for r in results for o in options]
    return [r for r in results if "<" not in r]


def locations(game: Game) -> dict:
    """{"entry": nome no manifesto | None, "paths": [arquivos/pastas que existem], "registry": [chaves HKCU]}."""
    if game.source in ("ps3", "ps2", "ps1", "nes"):
        return {"entry": None, "paths": [str(p) for p in _emulator_paths(game)], "registry": []}
    found = find_entry(game)
    if not found:
        return {"entry": None, "paths": [], "registry": []}
    name, entry = found
    values = _placeholders(game)
    paths: list[str] = []
    for pattern, info in (entry.get("files") or {}).items():
        info = info or {}
        tags = info.get("tags") or []
        if tags and "save" not in tags:
            continue
        candidates = [pattern] if _applies(info.get("when"), game) else []
        if game.source != "steam" and (m := re.match(r"<root>/userdata/<storeUserId>/(\d+)/remote/(.*)", pattern)):
            # cópia fora da Steam: o save "da nuvem Steam" fica na pasta do emulador de Steam que o jogo usa
            appid, rest = m.groups()
            candidates += [f"{root}/{appid}/{sub}{rest}" for root, sub in STEAM_EMU_SAVES]
        for expanded in (e for c in candidates for e in _expand(c, values)):
            for hit in glob.glob(expanded.replace("/", os.sep), recursive=True):
                if hit not in paths:
                    paths.append(hit)
    registry = []
    for key, info in (entry.get("registry") or {}).items():
        tags = (info or {}).get("tags") or []
        if key.upper().startswith("HKEY_CURRENT_USER") and (not tags or "save" in tags):
            registry.append(key.replace("/", "\\"))
    # um caminho dentro de outro já listado não precisa entrar duas vezes
    paths.sort(key=len)
    unique = [p for i, p in enumerate(paths) if not any(p.startswith(q + os.sep) for q in paths[:i])]
    return {"entry": name, "paths": unique, "registry": [k for k in registry if _reg_exists(k)]}


def _reg_exists(key: str) -> bool:
    import winreg
    sub = key.split("\\", 1)[1] if "\\" in key else ""
    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, sub))
        return True
    except OSError:
        return False


def _emulator_paths(game: Game) -> list[Path]:
    from .emulators import EMULATORS
    emu = EMULATORS.get(game.source)
    if not emu:
        return []
    try:
        return [p for p in emu.save_paths(game) if p.exists()]
    except Exception:
        return []


# ---------------------------------------------------------------- backup / restauração

def _files(paths: list[str]) -> list[Path]:
    files = []
    for p in map(Path, paths):
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files += [f for f in p.rglob("*") if f.is_file()]
    return files


def _arcname(path: Path) -> str:
    drive, rest = os.path.splitdrive(str(path))
    return "files/" + drive.rstrip(":").replace("\\", "") + "/" + rest.lstrip("\\/").replace("\\", "/")


def game_folder(game: Game) -> Path:
    folder = backups_root() / slug(game.display_name or game.id)
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def list_backups(game: Game) -> list[dict]:
    folder = backups_root() / slug(game.display_name or game.id)
    if not folder.is_dir():
        return []
    result = []
    for z in sorted(folder.glob("*.zip"), reverse=True):
        try:
            with zipfile.ZipFile(z) as zf:
                meta = json.loads(zf.read("gamedock-save.json"))
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            continue
        result.append({"name": z.name, "at": meta.get("at"), "size": z.stat().st_size, "files": meta.get("count", 0),
                       "reason": meta.get("reason", "")})
    return result


def backup(game: Game, reason: str = "manual", keep: int = KEEP_DEFAULT) -> dict:
    loc = locations(game)
    files = _files(loc["paths"])
    if not files and not loc["registry"]:
        return {"ok": False, "error": "Não achei o save desse jogo neste PC." if loc["entry"] or loc["paths"]
                else "Esse jogo não está na lista de locais de save (ou ainda não tem save)."}
    fingerprint = hashlib.sha1(json.dumps(sorted((str(f), f.stat().st_size, int(f.stat().st_mtime)) for f in files)
                                          + loc["registry"]).encode()).hexdigest()
    folder = game_folder(game)
    previous = list_backups(game)
    if reason == "auto" and previous:
        try:
            with zipfile.ZipFile(folder / previous[0]["name"]) as zf:
                if json.loads(zf.read("gamedock-save.json")).get("fingerprint") == fingerprint:
                    return {"ok": True, "skipped": True, "path": str(folder / previous[0]["name"])}
        except (OSError, KeyError, ValueError, zipfile.BadZipFile):
            pass
    dest = folder / f"{datetime.now():%Y-%m-%d_%H-%M-%S}{'-antes-de-restaurar' if reason == 'restore' else ''}.zip"
    meta = {"game": game.id, "name": game.display_name, "at": int(time.time()), "reason": reason, "count": len(files),
            "fingerprint": fingerprint, "files": {}, "registry": []}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            arc = _arcname(f)
            try:
                zf.write(f, arc)
                meta["files"][arc] = str(f)
            except OSError:
                pass   # arquivo em uso pelo jogo
        for i, key in enumerate(loc["registry"]):
            reg = _folder() / f"reg-{i}.reg"
            subprocess.run(["reg", "export", key, str(reg), "/y"], capture_output=True, creationflags=CREATE_NO_WINDOW)
            if reg.is_file():
                zf.write(reg, f"registry/{i}.reg")
                meta["registry"].append(key)
                reg.unlink()
        zf.writestr("gamedock-save.json", json.dumps(meta, ensure_ascii=False, indent=1))
    for old in list_backups(game)[max(1, keep):]:
        if "antes-de-restaurar" not in old["name"]:
            (folder / old["name"]).unlink(missing_ok=True)
    return {"ok": True, "path": str(dest), "files": len(meta["files"])}


def restore(game: Game, name: str) -> dict:
    source = game_folder(game) / Path(name).name
    if not source.is_file():
        return {"ok": False, "error": "Backup não encontrado."}
    safety = backup(game, reason="restore")
    with zipfile.ZipFile(source) as zf:
        meta = json.loads(zf.read("gamedock-save.json"))
        for arc, original in meta["files"].items():
            target = Path(original)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(arc) as src, open(target, "wb") as out:
                out.write(src.read())
            ts = meta.get("at", time.time())
            os.utime(target, (ts, ts))
        for i, _key in enumerate(meta.get("registry", [])):
            reg = _folder() / f"restore-{i}.reg"
            reg.write_bytes(zf.read(f"registry/{i}.reg"))
            subprocess.run(["reg", "import", str(reg)], capture_output=True, creationflags=CREATE_NO_WINDOW)
            reg.unlink()
    return {"ok": True, "restored": len(meta["files"]), "safety": safety.get("path")}


def summary(game: Game) -> dict:
    """Para a página do jogo: onde está o save, de quando é, quanto ocupa e os backups."""
    loc = locations(game)
    files = _files(loc["paths"])
    newest = max((f.stat().st_mtime for f in files), default=None)
    return {"entry": loc["entry"], "paths": loc["paths"], "registry": loc["registry"], "files": len(files),
            "size": sum(f.stat().st_size for f in files), "modified": int(newest) if newest else None,
            "backups": list_backups(game), "folder": str(backups_root() / slug(game.display_name or game.id))}

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

from .models import DETECTED_FIELDS, Game, now_iso


def data_dir() -> Path:
    if os.environ.get("GAMEDOCK_HOME"):
        path = Path(os.environ["GAMEDOCK_HOME"])
    else:
        appdata = Path(os.environ.get("APPDATA", Path.home()))
        path = appdata / "GameDock"
        legacy = appdata / "GameHub"  # nome antigo do projeto (v0.1)
        if legacy.is_dir() and not path.exists():
            legacy.rename(path)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _read_json(path: Path, default):
    if not path.exists():
        return default
    for attempt in range(5):
        try:
            with path.open(encoding="utf-8") as f:
                return json.load(f)
        except (PermissionError, json.JSONDecodeError):
            if attempt == 4:
                raise
            time.sleep(0.05)  # outra thread está trocando o arquivo neste instante


def atomic_write(path: Path, text: str) -> None:
    """Escreve num .tmp e troca de uma vez: o arquivo nunca fica pela metade.

    No Windows a troca falha (acesso negado) se outra thread estiver lendo o arquivo
    naquele milissegundo; nesse caso tentamos de novo.
    """
    tmp = path.with_name(f"{path.name}.{threading.get_ident()}.tmp")  # um .tmp por thread
    tmp.write_text(text, encoding="utf-8")
    for attempt in range(20):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.05)


def _write_json(path: Path, data) -> None:
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------- histórico de sessões

_SESSIONS_LOCK = threading.Lock()


def append_session(game_id: str, start: float, end: float) -> None:
    """Uma linha por sessão em sessions.jsonl (só cresce; ~100 bytes por sessão)."""
    line = json.dumps({"game": game_id, "start": int(start), "end": int(end)})
    with _SESSIONS_LOCK, (data_dir() / "sessions.jsonl").open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_sessions(game_id: str | None = None) -> list[dict]:
    path = data_dir() / "sessions.jsonl"
    if not path.is_file():
        return []
    sessions = []
    with _SESSIONS_LOCK:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue  # linha cortada (PC desligou no meio da escrita)
            if game_id is None or item.get("game") == game_id:
                sessions.append(item)
    return sessions


# ---------------------------------------------------------------- config

DEFAULT_CONFIG = {
    # pastas onde cada subpasta é considerada um jogo (jogos sem launcher)
    "scan_folders": [],
    # scanners desligados, ex: ["shortcuts"]
    "disabled_scanners": [],
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    config.update(_read_json(data_dir() / "config.json", {}))
    return config


def save_config(config: dict) -> None:
    _write_json(data_dir() / "config.json", config)


# ---------------------------------------------------------------- biblioteca

# A interface e o monitor de tempo de jogo leem-modificam-salvam o library.json em threads
# diferentes; sem esta trava, um podia sobrescrever a mudança do outro (ex: um favorito).
LIBRARY_LOCK = threading.RLock()


def _launch_file_gone(game: Game) -> bool | None:
    """True se o .exe/ISO do jogo não existe mais; None quando não dá pra saber (URI, nome solto)."""
    target = game.launch_target or ""
    if game.launch_type not in ("exe", "emu") or not (target[1:3] == ":\\" or target.startswith("\\\\")):
        return None
    try:
        return not Path(target).exists()
    except OSError:
        return None


class Library:
    def __init__(self, path: Path | None = None):
        self.path = path or data_dir() / "library.json"
        raw = _read_json(self.path, {"games": []})
        self.games: dict[str, Game] = {g["id"]: Game.from_dict(g) for g in raw["games"]}

    def save(self) -> None:
        games = sorted(self.games.values(), key=lambda g: g.display_name.lower())
        _write_json(self.path, {"version": 1, "updated_at": now_iso(), "games": [g.to_dict() for g in games]})

    def visible(self, include_hidden: bool = False, include_missing: bool = False) -> list[Game]:
        result = [
            g for g in self.games.values()
            if (include_hidden or not g.hidden) and (include_missing or g.installed)
        ]
        return sorted(result, key=lambda g: (not g.favorite, g.display_name.lower()))

    def merge_scan(self, detected: list[Game], scanned_sources: set[str]) -> dict[str, list[Game]]:
        """Junta o resultado de um scan com a biblioteca, preservando as configs do usuário."""
        report = {"new": [], "updated": [], "missing": []}
        seen = set()
        seen_signatures = {g.launch_signature for g in detected}

        for game in detected:
            seen.add(game.id)
            existing = self.games.get(game.id)
            if existing is None:
                self.games[game.id] = game
                report["new"].append(game)
                continue
            changed = not existing.installed
            for name in DETECTED_FIELDS:
                value = getattr(game, name)
                if getattr(existing, name) != value:
                    setattr(existing, name, value)
                    changed = True
            existing.installed = True
            if changed:
                report["updated"].append(existing)

        # jogo que estava na biblioteca e sumiu do scan: provavelmente foi desinstalado.
        # Não apagamos (mantém favoritos/estatísticas caso volte), só marcamos.
        for game in self.games.values():
            if game.id in seen:
                continue
            gone = _launch_file_gone(game)
            if game.manual:
                # adicionado à mão: nenhum scanner o encontra, então vale o arquivo existir ou não
                if gone is not None and game.installed == gone:
                    game.installed = not gone
                    report["missing" if gone else "updated"].append(game)
                continue
            # o .exe/ISO sumiu do disco: desinstalado, mesmo que a fonte não tenha sido varrida
            # (ex: ISO de PS3 apagado com o emulador fechado ou sem nenhum outro jogo de PS3)
            if game.source not in scanned_sources and not gone:
                continue
            # jogo local achado por atalho: apagar o atalho não desinstala o jogo
            # (a menos que outro jogo do scan já aponte para o mesmo exe — aí este é uma duplicata velha)
            if (game.source in ("local", "folder") and Path(game.launch_target).is_file()
                    and game.launch_signature not in seen_signatures):
                if not game.installed:
                    game.installed = True
                    report["updated"].append(game)
                continue
            if game.installed:
                game.installed = False
                report["missing"].append(game)

        return report

    def find(self, query: str) -> list[Game]:
        """Busca por id exato, nome exato ou pedaço do nome."""
        if query in self.games:
            return [self.games[query]]
        q = query.lower()
        games = self.visible(include_hidden=True)
        exact = [g for g in games if g.display_name.lower() == q]
        if exact:
            return exact
        return [g for g in games if q in g.display_name.lower() or q in g.id.lower()]

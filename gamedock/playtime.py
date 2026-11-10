"""Tempo de jogo: vigia os processos e soma o tempo de cada jogo aberto.

Funciona mesmo quando o jogo é aberto fora do GameDock (basta o GameDock estar rodando):
um jogo está "rodando" quando algum processo tem o .exe dentro da pasta de instalação dele.
O tempo é somado a cada verificação (e salvo a cada minuto), então nada se perde se o PC desligar.
Para jogos da Steam, o tempo que a Steam já registrou entra como ponto de partida.
"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from .formats import read_vdf
from .launcher import EMULATED
from .models import now_iso
from .processes import running_executables
from .scanners.steam import SteamScanner
from .storage import LIBRARY_LOCK, Library, append_session, atomic_write, data_dir

INTERVAL = 5          # segundos entre verificações
END_AFTER_MISSES = 2  # sessão termina após ~10 s sem o processo (evita cortar em reinícios rápidos)
SAVE_EVERY = 60
# o GameDock fechou (atualização, Sair) com jogo aberto: a sessão fica guardada aqui e é retomada
# na próxima abertura, sem contar uma "jogada" nova. Fechado por mais que isso, a sessão é encerrada.
RESUME_MAX_GAP = 15 * 60


def _active_file() -> Path:
    return data_dir() / "active_sessions.json"
# executáveis que ficam na pasta do jogo mas não são o jogo
IGNORED_EXE = re.compile(r"crash|report|unins|setup|redist|easyanticheat|^eac|battleye|beservice|"
                         r"webhelper|cefsharp|updater|dxsetup|vcredist|prereq", re.I)


def _norm_dir(path: str | None) -> str | None:
    if not path:
        return None
    text = str(Path(path)).lower().rstrip("\\/")
    # uma raiz curta demais (ex: "d:") faria qualquer processo do disco contar como o jogo
    return text + "\\" if len(Path(text).parts) >= 2 else None


def game_roots(game) -> list[str]:
    roots = {_norm_dir(game.install_dir)}
    if game.exe and game.source not in ("riot", "battlenet", "ea"):  # nesses o exe é o launcher
        roots.add(_norm_dir(str(Path(game.exe).parent)))
    return [r for r in roots if r]


# ---------------------------------------------------------------- Steam

def steam_playtimes() -> dict[str, tuple[int, int]]:
    """{appid: (segundos jogados, último jogo em unix)} somando todas as contas Steam do PC."""
    root = SteamScanner().steam_root()
    result: dict[str, tuple[int, int]] = {}
    if not root:
        return result
    for config in (root / "userdata").glob("*/config/localconfig.vdf"):
        try:
            data = read_vdf(config)
        except OSError:
            continue
        node = data
        for key in ("UserLocalConfigStore", "Software", "Valve", "Steam", "apps"):
            node = next((v for k, v in node.items() if k.lower() == key.lower()), {}) if isinstance(node, dict) else {}
        for appid, info in node.items():
            if not isinstance(info, dict):
                continue
            seconds = int(info.get("Playtime", 0) or 0) * 60
            last = int(info.get("LastPlayed", 0) or 0)
            old = result.get(appid, (0, 0))
            result[appid] = (max(old[0], seconds), max(old[1], last))
    return result


def import_steam_playtime() -> None:
    """Jogou pela Steam com o GameDock fechado? Puxa o total da Steam se for maior."""
    times = steam_playtimes()
    with LIBRARY_LOCK:
        library = Library()
        changed = False
        for game in library.games.values():
            if not game.id.startswith("steam:"):
                continue
            seconds, last = times.get(game.id.split(":", 1)[1], (0, 0))
            if seconds > game.playtime_seconds:
                game.playtime_seconds = seconds
                changed = True
            if last:
                iso = datetime.fromtimestamp(last).isoformat(timespec="seconds")
                if not game.last_played or iso > game.last_played:
                    game.last_played = iso
                    changed = True
        if changed:
            library.save()


# ---------------------------------------------------------------- monitor

class PlaytimeMonitor(threading.Thread):
    def __init__(self, on_start=None, on_stop=None):
        super().__init__(daemon=True, name="playtime")
        self.on_start = on_start or (lambda game_id: None)
        self.on_stop = on_stop or (lambda game_id, seconds: None)
        self.active: dict[str, float] = {}   # game_id -> início da sessão
        self.misses: dict[str, int] = {}
        self.pending: dict[str, float] = {}  # segundos ainda não salvos
        self._halt = threading.Event()
        self.resume: dict[str, float] = {}   # sessões abertas antes do GameDock fechar
        self.resume_saved_at = 0.0

    def running_now(self) -> set[str]:
        return set(self.active)

    def session_started_at(self, game_id: str) -> float | None:
        return self.active.get(game_id)

    def stop(self) -> None:
        """Chamado quando o GameDock fecha: salva o tempo pendente e guarda as sessões abertas."""
        self._halt.set()
        self._flush()
        try:
            if self.active:
                atomic_write(_active_file(), json.dumps({"saved_at": time.time(), "active": self.active}))
            else:
                _active_file().unlink(missing_ok=True)
        except OSError:
            pass

    def _load_resume(self) -> None:
        try:
            data = json.loads(_active_file().read_text(encoding="utf-8"))
            _active_file().unlink(missing_ok=True)
        except (OSError, ValueError):
            return
        self.resume_saved_at = float(data.get("saved_at") or 0)
        self.resume = {k: float(v) for k, v in (data.get("active") or {}).items()}
        if time.time() - self.resume_saved_at > RESUME_MAX_GAP:
            self._close_resumed()

    def _close_resumed(self) -> None:
        """Sessões guardadas cujo jogo não está mais aberto: terminam quando o GameDock fechou."""
        for game_id, started in self.resume.items():
            if self.resume_saved_at > started:
                append_session(game_id, started, self.resume_saved_at)
        self.resume.clear()

    def run(self) -> None:
        self._load_resume()
        import_steam_playtime()
        last_tick = time.monotonic()
        last_save = last_tick
        while not self._halt.wait(INTERVAL):
            now = time.monotonic()
            # se o PC dormiu, o intervalo real é enorme: não conta como tempo de jogo
            elapsed = min(now - last_tick, INTERVAL * 3)
            last_tick = now
            try:
                self._tick(elapsed)
                if now - last_save >= SAVE_EVERY:
                    self._flush()
                    last_save = now
            except Exception as exc:  # o monitor nunca pode morrer por um erro pontual
                print("playtime:", exc)

    def _tick(self, elapsed: float) -> None:
        library = Library()
        processes = running_executables()
        executables = [p.lower() for p in processes.values()]
        running = set()
        for pid, game_id in list(EMULATED.items()):   # jogos de emulador abertos pelo GameDock
            if pid in processes:
                running.add(game_id)
            else:
                EMULATED.pop(pid, None)
        for game in library.games.values():
            if not game.track:  # "não é um jogo": não conta tempo nem sessões
                continue
            roots = game_roots(game)
            if roots and any(exe.startswith(root) and not IGNORED_EXE.search(Path(exe).stem)
                             for exe in executables for root in roots):
                running.add(game.id)

        for game_id in running:
            self.misses.pop(game_id, None)
            if game_id not in self.active and game_id in self.resume:
                # mesma sessão de antes do GameDock fechar: o tempo fechado também foi jogado
                self.active[game_id] = self.resume.pop(game_id)
                self.pending[game_id] = self.pending.get(game_id, 0) + max(0.0, time.time() - self.resume_saved_at)
            elif game_id not in self.active:
                self.active[game_id] = time.time()
                self._session_started(game_id)
            else:
                self.pending[game_id] = self.pending.get(game_id, 0) + elapsed

        if self.resume:
            self._close_resumed()   # depois da 1ª verificação, o que não está rodando já acabou

        for game_id in list(self.active):
            if game_id in running:
                continue
            self.misses[game_id] = self.misses.get(game_id, 0) + 1
            if self.misses[game_id] >= END_AFTER_MISSES:
                started = self.active.pop(game_id)
                self.misses.pop(game_id, None)
                self._flush()
                # o processo sumiu há ~END_AFTER_MISSES verificações: a sessão acabou antes de agora
                ended = time.time() - INTERVAL * (END_AFTER_MISSES - 1)
                append_session(game_id, started, ended)
                self.on_stop(game_id, ended - started)

    def _session_started(self, game_id: str) -> None:
        with LIBRARY_LOCK:
            library = Library()
            game = library.games.get(game_id)
            if game:
                game.play_count += 1
                game.last_played = now_iso()
                library.save()
        self.on_start(game_id)

    def _flush(self) -> None:
        if not self.pending:
            return
        with LIBRARY_LOCK:
            library = Library()
            for game_id, seconds in self.pending.items():
                if game_id in library.games:
                    library.games[game_id].playtime_seconds += int(round(seconds))
            library.save()
        self.pending.clear()

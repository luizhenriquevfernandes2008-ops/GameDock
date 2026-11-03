"""O que acontece "ao vivo" enquanto você joga: tempo de jogo, troféus, aviso na tela e capturas.

Fluxo de um troféu novo:
  arquivo de conquistas do jogo muda → sync() acha o desbloqueio → captura de tela (antes do aviso,
  para sair limpa) → aviso por cima do jogo → o launcher atualiza a contagem.
Captura manual: a tecla de atalho (Print Screen por padrão) captura o monitor do jogo e mostra um aviso discreto.
Painel (Alt+G): sessão atual, troféus e capturas do jogo, por cima dele, sem tirar o foco.
Clipes: os vídeos que a Xbox Game Bar salva entram na galeria (veja clips.py).
Depois de troféus e sessões, o nível do perfil é recalculado; subiu → aviso de "nível X".
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from . import achievements, captures, challenges, clips, freegames, gamemode, player, quickset, recorder, saves
from .art import art_dir, as_url, get_art
from .overlay import GamePanel, TrophyOverlay, capture_screen
from .playtime import PlaytimeMonitor
from .scanners.base import slug
from .storage import Library, data_dir, load_config

WATCH_EVERY = 2.0
CLIPS_EVERY = 10.0
PANEL_HOTKEY = (0x1, 0x47)  # Alt+G


class LiveService:
    def __init__(self, main_window, overlay_window, panel_window=None):
        self.main = main_window
        self.overlay = TrophyOverlay(overlay_window)
        self.panel = GamePanel(panel_window) if panel_window else None
        self.replay = recorder.ReplayBuffer()
        self.clip_hotkey: captures.Hotkey | None = None
        self.monitor = PlaytimeMonitor(on_start=self._started, on_stop=self._stopped)
        self.watching: dict[str, tuple] = {}   # game_id -> assinatura (arquivo, mtime) dos arquivos vigiados
        self.lock = threading.Lock()
        self.hotkey: captures.Hotkey | None = None
        self.discord = quickset.DiscordPresence()
        self.last_poll: dict[str, float] = {}     # jogo -> última leitura do RetroAchievements
        self.break_told: dict[str, int] = {}      # jogo -> quantos lembretes de pausa já mostrou na sessão
        self.free_checked = 0.0

    def start(self) -> None:
        self.monitor.start()
        self.set_hotkey(load_config().get("capture_hotkey", captures.DEFAULT_HOTKEY))
        if self.panel:
            self.panel_hotkey = captures.Hotkey("Alt+G", self.panel.toggle, combo=PANEL_HOTKEY)
            self.panel_hotkey.start()
        threading.Thread(target=self._watch_loop, daemon=True, name="trophy-watch").start()
        self.set_clip_hotkey(load_config().get("clip_hotkey", captures.DEFAULT_CLIP_HOTKEY))
        threading.Thread(target=self._initial_sync, daemon=True, name="trophy-sync").start()
        threading.Thread(target=self._tick_loop, daemon=True, name="live-tick").start()

    def running(self) -> list[str]:
        return sorted(self.monitor.running_now())

    # ------------------------------------------------------------ sessões

    def _started(self, game_id: str) -> None:
        game = Library().games.get(game_id)
        if game:
            achievements.sync(game)  # garante a linha de base antes de vigiar
            with self.lock:
                self.watching[game_id] = self._signature(game)
        self.break_told.pop(game_id, None)
        if gamemode.enabled():
            threading.Thread(target=self._safe, args=(gamemode.activate,), daemon=True, name="game-mode").start()
        self._start_replay()
        self._update_discord()
        self._notify("onRunning", self.running())

    def _stopped(self, game_id: str, seconds: float) -> None:
        with self.lock:
            self.watching.pop(game_id, None)
        if not self.monitor.running_now():
            self.replay.stop()   # nenhum jogo aberto: o gravador para
            threading.Thread(target=self._safe, args=(gamemode.deactivate,), daemon=True, name="game-mode").start()
        self._update_discord()
        game = Library().games.get(game_id)
        if game:  # pega desbloqueios gravados no fechamento do jogo (sem aviso: o jogo já fechou)
            self._handle(game, popup=False)
            if game.launch_type == "emu":   # como num console: fechou o jogo, volta para o GameDock
                try:
                    self.main.restore()
                except Exception:
                    pass
            if load_config().get("save_auto", True):   # backup do save logo depois de jogar
                threading.Thread(target=self._backup_saves, args=(game,), daemon=True, name="save-backup").start()
        self._notify("onRunning", self.running())
        self._notify("onLibraryChanged", None)
        self._check_level()

    # ------------------------------------------------------------ capturas

    def set_hotkey(self, name: str | None) -> bool:
        """Troca a tecla de captura. False = o Windows recusou (outro programa já usa a tecla)."""
        if self.hotkey:
            self.hotkey.stop()
            self.hotkey = None
        self.hotkey = captures.start_hotkey(name, self.capture_now)
        return self.hotkey is None or self.hotkey.ok

    def current_game(self):
        """O jogo aberto mais recentemente (se houver), para saber de quem é a captura."""
        running = self.monitor.running_now()
        if not running:
            return None
        newest = max(running, key=lambda gid: self.monitor.session_started_at(gid) or 0)
        return Library().games.get(newest)

    def capture_now(self) -> None:
        game = self.current_game()
        folder = art_dir(game) if game else data_dir() / "art" / "_pc"
        shot = folder / "captures" / f"{time.strftime('%Y-%m-%d_%H-%M-%S')}.jpg"
        if not capture_screen(shot):
            return
        rel = shot.relative_to(data_dir() / "art").as_posix()
        captures.add(game.id if game else None, rel, "manual")
        config = load_config()
        if config.get("trophy_popup", True):
            self.overlay.show({"kind": "capture", "name": "Captura salva",
                               "game": game.display_name if game else "Área de trabalho",
                               "icon": as_url(shot, game) if game else f"/art/{rel}",
                               "sound": config.get("sound_enabled", True), "volume": config.get("sound_volume", 0.6)})
        self._notify("onCapture", {"gameId": game.id if game else None})

    # ------------------------------------------------------------ clipes (replay buffer próprio)

    def _start_replay(self) -> None:
        config = load_config()
        if not config.get("clip_enabled") or not recorder.ffmpeg_ready():
            return
        self.replay.seconds = int(config.get("clip_seconds", 30))
        self.replay.fps = int(config.get("clip_fps", 60))
        self.replay.with_audio = bool(config.get("clip_audio", True))
        try:
            self.replay.start()
        except Exception as exc:
            print("gravador:", exc)

    def restart_replay(self) -> None:
        """Depois de mudar as configurações de gravação."""
        self.replay.stop()
        if self.monitor.running_now():
            self._start_replay()

    def set_clip_hotkey(self, name: str | None) -> bool:
        if self.clip_hotkey:
            self.clip_hotkey.stop()
            self.clip_hotkey = None
        self.clip_hotkey = captures.start_hotkey(name, lambda: self.record_clip())
        return self.clip_hotkey is None or self.clip_hotkey.ok

    def record_clip(self) -> dict:
        config = load_config()
        if not config.get("clip_enabled"):
            return {"ok": False, "error": "Ligue a gravação de clipes nas Configurações → Clipes."}
        if not self.replay.running:
            return {"ok": False, "error": self.replay.error() or "A gravação começa quando um jogo abre."}
        game = self.current_game()
        try:
            path = self.replay.save(game.display_name if game else None)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        rel = path.relative_to(recorder.clips_dir()).as_posix()
        captures.add(game.id if game else None, clips.CLIP_PREFIX + rel, "clip")
        if config.get("trophy_popup", True):
            self.overlay.show({"kind": "capture", "name": "Clipe salvo", "game": game.display_name if game else "",
                               "icon": "", "sound": config.get("sound_enabled", True),
                               "volume": config.get("sound_volume", 0.6)})
        self._notify("onCapture", {"gameId": game.id if game else None})
        return {"ok": True, "path": str(path)}

    # ------------------------------------------------------------ painel do Alt+G

    def panel_data(self) -> dict:
        game = self.current_game()
        config = load_config()
        base = {"clipsEnabled": bool(config.get("clip_enabled")) and self.replay.running,
                "captureKey": config.get("capture_hotkey", captures.DEFAULT_HOTKEY)}
        if not game:
            return base | {"game": None}
        art = get_art(game)
        tset = achievements.load(game.id)
        unlocked = sorted((t for t in tset.trophies.values() if t.unlocked), key=lambda t: t.unlocked_at or 0, reverse=True)
        locked = sorted((t for t in tset.trophies.values() if not t.unlocked and not t.hidden),
                        key=lambda t: t.percent or 0, reverse=True)
        shots = [c for c in reversed(captures.all_captures()) if c["game"] == game.id][:3]
        from .emulators import EMULATORS
        emu = EMULATORS.get(game.source)
        return base | {
            "game": {"id": game.id, "name": game.display_name, "cover": art.get("cover"), "hero": art.get("hero"),
                     "logo": art.get("logo"), "emulated": bool(emu),
                     "savestates": bool(emu and emu.savestate_keys)},
            "sessionStart": self.monitor.session_started_at(game.id),
            "playtime": game.playtime_seconds,
            "trophies": {**tset.summary(),
                         "recent": [{"name": t.name, "icon": t.icon, "tier": t.tier} for t in unlocked[:4]],
                         "next": [{"name": t.name, "desc": t.desc, "icon": t.icon_gray or t.icon, "percent": t.percent}
                                  for t in locked[:3]]},
            "captures": [{"url": "/clips/" + c["path"][len(clips.CLIP_PREFIX):] if c["kind"] == "clip"
                          else f"/art/{c['path']}", "kind": c["kind"]} for c in shots],
        }

    def panel_capture(self) -> None:
        if self.panel:
            self.panel.hide()
            time.sleep(0.35)  # o painel não pode sair na captura
        self.capture_now()

    def panel_clip(self) -> dict:
        result = self.record_clip()
        if result["ok"] and self.panel:
            self.panel.hide()
        return result

    def _emulator_window(self):
        """(jogo, emulador, pid) do jogo de emulador aberto agora."""
        from .emulators import EMULATORS
        from .launcher import EMULATED
        game = self.current_game()
        emu = EMULATORS.get(game.source) if game else None
        pid = next((p for p, gid in EMULATED.items() if game and gid == game.id), None)
        return game, emu, pid

    def panel_savestate(self, action: str) -> dict:
        """Salvar/carregar estado: o painel some e o GameDock aperta a tecla do emulador (o jogo está em foco)."""
        game, emu, _pid = self._emulator_window()
        if not emu or not emu.savestate_keys:
            return {"ok": False, "error": "Esse jogo não tem save state pelo GameDock."}
        if self.panel:
            self.panel.hide()
        time.sleep(0.3)
        press_key(emu.savestate_keys["save" if action == "save" else "load"])
        config = load_config()
        if config.get("trophy_popup", True):
            self.overlay.show({"kind": "state", "name": "Estado salvo" if action == "save" else "Estado carregado",
                               "game": game.display_name, "icon": "", "sound": config.get("sound_enabled", True),
                               "volume": config.get("sound_volume", 0.6)})
        return {"ok": True}

    def panel_quit_game(self) -> dict:
        """Fecha o emulador como o X da janela (o emulador salva o que precisar antes)."""
        game, emu, pid = self._emulator_window()
        if not emu or not pid:
            return {"ok": False, "error": "Só jogos de emulador fecham por aqui."}
        if self.panel:
            self.panel.hide()
        close_windows(pid)
        return {"ok": True}

    def _check_level(self) -> None:
        try:
            done = challenges.compute(Library())["new"]
        except Exception as exc:
            print("desafios:", exc)
            done = []
        config = load_config()
        for item in done:   # desafio do GameDock / meta da semana cumprida
            self.overlay.show({"kind": "challenge" if item["kind"] == "meta" else "goal", "name": item["name"],
                               "game": f"{item['desc']} · +{item['xp']} XP", "icon": "",
                               "sound": config.get("sound_enabled", True), "volume": config.get("sound_volume", 0.6)})
            self._notify("onChallenge", item)
        try:
            level = player.check_level_up(Library())
        except Exception as exc:
            print("nível:", exc)
            return
        if level:
            config = load_config()
            self.overlay.show({"kind": "level", "level": level, "name": f"Nível {level}",
                               "game": "Você subiu de nível no GameDock", "icon": "",
                               "sound": config.get("sound_enabled", True), "volume": config.get("sound_volume", 0.6)})
            self._notify("onLevelUp", {"level": level})

    # ------------------------------------------------------------ troféus

    def _signature(self, game) -> tuple:
        sig = []
        for path in achievements.watch_paths(game):
            try:
                sig.append((str(path), path.stat().st_mtime_ns, path.stat().st_size))
            except OSError:
                pass
        return tuple(sig)

    def _watch_loop(self) -> None:
        while True:
            time.sleep(WATCH_EVERY)
            try:
                self._watch_once()
            except Exception as exc:  # o vigia nunca pode morrer: perderia todos os troféus da sessão
                print("vigia de troféus:", exc)

    def _watch_once(self) -> None:
        with self.lock:
            items = list(self.watching.items())
        for game_id, old_sig in items:
            game = Library().games.get(game_id)
            if not game:
                continue
            sig = self._signature(game)
            if sig != old_sig:
                with self.lock:
                    if game_id in self.watching:
                        self.watching[game_id] = sig
                self._handle(game, popup=True)

    @staticmethod
    def _safe(fn) -> None:
        try:
            fn()
        except Exception as exc:
            print(f"{getattr(fn, '__name__', fn)}:", exc)

    def _backup_saves(self, game) -> None:
        try:
            result = saves.backup(game, "auto", int(load_config().get("save_keep", saves.KEEP_DEFAULT)))
            if result.get("ok") and not result.get("skipped"):
                self._notify("onSaveBackup", {"gameId": game.id, "name": game.display_name})
        except Exception as exc:
            print("backup de save:", game.name, exc)

    def _update_discord(self) -> None:
        config = load_config()
        client_id = (config.get("discord_client_id") or "").strip()
        if not config.get("discord_presence") or not client_id:
            self.discord.close()
            return
        game = self.current_game()
        activity = None
        if game:
            started = self.monitor.session_started_at(game.id)
            activity = {"details": game.display_name, "state": f"{SOURCE_NAMES.get(game.source, 'PC')} · via GameDock",
                        "timestamps": {"start": int(started)} if started else {}}
            if game.id.startswith("steam:"):
                appid = game.id.split(":", 1)[1]
                activity["assets"] = {"large_image": f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/library_600x900.jpg",
                                      "large_text": game.display_name}
        threading.Thread(target=self.discord.update, args=(client_id, activity), daemon=True, name="discord").start()

    def _tick_loop(self) -> None:
        """A cada 15 s: RetroAchievements dos jogos abertos, lembrete de pausa, jogos grátis (a cada 3 h)."""
        while True:
            time.sleep(15)
            try:
                self._tick()
            except Exception as exc:
                print("rotina ao vivo:", exc)

    def _tick(self) -> None:
        now = time.time()
        config = load_config()
        library = Library()
        for game_id in self.monitor.running_now():
            game = library.games.get(game_id)
            if not game:
                continue
            every = achievements.poll_interval(game)
            if every and now - self.last_poll.get(game_id, 0) >= every:
                self.last_poll[game_id] = now
                self._handle(game, popup=False)   # o emulador já mostrou o aviso dentro do jogo
            minutes = int(config.get("break_reminder") or 0)
            started = self.monitor.session_started_at(game_id)
            if minutes and started:
                due = int((now - started) // (minutes * 60))
                if due > self.break_told.get(game_id, 0):
                    self.break_told[game_id] = due
                    hours = (now - started) / 3600
                    played = f"{hours:.1f} h".replace(".", ",") if hours >= 1 else f"{int(hours * 60)} min"
                    self.overlay.show({"kind": "break", "name": f"Você está jogando há {played}",
                                       "game": f"{game.display_name} · alongue, beba uma água", "icon": "",
                                       "sound": config.get("sound_enabled", True), "volume": config.get("sound_volume", 0.6)})
        if now - self.free_checked > 3 * 3600:
            self.free_checked = now
            try:
                fresh = freegames.new_ones()
            except Exception:
                fresh = []
            if fresh:
                self._notify("onFreeGames", fresh)

    def _initial_sync(self) -> None:
        time.sleep(4)  # deixa a primeira tela do launcher carregar antes
        for game in Library().visible():
            try:
                achievements.sync(game)
            except Exception as exc:
                print("troféus:", game.name, exc)
        self._notify("onLibraryChanged", None)
        self._check_level()  # a primeira vez só registra o nível atual

    def _handle(self, game, popup: bool) -> None:
        try:
            tset, new, platinum = achievements.sync(game)
        except Exception as exc:
            print("troféus:", game.name, exc)
            return
        if not new and not platinum:
            return
        config = load_config()
        for trophy in new:
            if popup and config.get("trophy_screenshot", True):
                shot = art_dir(game) / "trophies" / f"{slug(trophy.api)}-{int(time.time())}.jpg"
                if capture_screen(shot):
                    trophy.screenshot = shot.relative_to(data_dir() / "art").as_posix()
                    tset.trophies[trophy.api] = trophy
                    achievements.save(tset)
                    captures.add(game.id, trophy.screenshot, "trophy", trophy.name)
            if popup and config.get("trophy_popup", True):
                self.overlay.show(self.payload(game, trophy, config))
        if platinum and popup and config.get("trophy_popup", True):
            self.overlay.show(self.payload(game, None, config))
        self._notify("onTrophy", {"gameId": game.id, "summary": tset.summary(),
                                  "names": [t.name for t in new], "platinum": platinum})
        self._check_level()

    def payload(self, game, trophy, config) -> dict:
        sound = {"sound": config.get("sound_enabled", True), "volume": config.get("sound_volume", 0.6)}
        if trophy is None:
            return {"tier": "platinum", "name": game.display_name, "game": "Todos os troféus conquistados",
                    "icon": "", "percent": None, **sound}
        return {"tier": trophy.tier, "name": trophy.name, "game": game.display_name,
                "icon": trophy.icon, "percent": trophy.percent, **sound}

    def test_popup(self) -> None:
        """Botão "Testar aviso" das configurações: mostra um troféu de exemplo (não salva nada)."""
        config = load_config()
        self.overlay.show({"tier": "gold", "name": "Assim que se faz", "game": "GameDock · teste do aviso",
                           "icon": "", "percent": 4.2, "sound": config.get("sound_enabled", True),
                           "volume": config.get("sound_volume", 0.6)})

    # ------------------------------------------------------------ interface

    def _notify(self, fn: str, data) -> None:
        try:
            self.main.evaluate_js(f"window.{fn} && window.{fn}({json.dumps(data)})")
        except Exception:
            pass  # janela fechando


SOURCE_NAMES = {"steam": "Steam", "epic": "Epic Games", "gog": "GOG", "ubisoft": "Ubisoft", "riot": "Riot Games",
                "ps3": "PlayStation 3", "ps2": "PlayStation 2", "ps1": "PlayStation", "nes": "NES"}


def press_key(vk: int) -> None:
    """Aperta e solta uma tecla para a janela em foco (o jogo)."""
    import ctypes
    user32 = ctypes.windll.user32
    scan = user32.MapVirtualKeyW(vk, 0)
    user32.keybd_event(vk, scan, 0, 0)
    time.sleep(0.06)
    user32.keybd_event(vk, scan, 2, 0)   # KEYEVENTF_KEYUP


def close_windows(pid: int) -> None:
    """Manda WM_CLOSE para as janelas de um processo (como clicar no X)."""
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and user32.IsWindowVisible(hwnd):
            user32.PostMessageW(hwnd, 0x0010, 0, 0)   # WM_CLOSE
        return True

    user32.EnumWindows(each, 0)


def trophies_for_ui(game) -> dict:
    tset, _, _ = achievements.sync(game)
    data = achievements.to_dict(tset)
    for trophy in data["trophies"]:
        shot = trophy.pop("screenshot", None)
        trophy["screenshot"] = as_url(data_dir() / "art" / shot, game) if shot else None
    return data

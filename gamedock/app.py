"""Interface gráfica: janela nativa (WebView2) desenhada em HTML/CSS em gamedock/web."""
from __future__ import annotations

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime, timedelta
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote

import webview

from . import (__version__, achievements, backup, captures, challenges, clips, disk, downloads, dualsense, emulators, freegames,
               gameinfo, gamemode, logs, mediaedit, player, quickset, recap, recorder, retroach, saves, sgdb, shell,
               sources, timetobeat, updater)
from .art import as_url, clear_memory, custom_cover, get_art, set_custom_cover, shrink_image
from .launcher import LaunchError, clean_dll_search, launch
from .live import LiveService, trophies_for_ui
from .models import STATUSES, now_iso
from .scanners import build_scanners, run_scanners
from .storage import LIBRARY_LOCK, Library, data_dir, load_config, read_sessions, save_config

WEB_DIR = Path(__file__).parent / "web"
ART_ROOT = data_dir() / "art"

# preferências simples que a interface lê e grava direto (chave do config.json: valor padrão)
PREFS = {
    "game_mode": False, "game_mode_quiet": True, "game_mode_reopen": True, "game_mode_close": [],
    "break_reminder": 0,            # minutos (0 = desligado)
    "discord_presence": False, "discord_client_id": "", "discord_webhook": "",
    "save_auto": True, "save_keep": 10,
    "free_games_notify": True,
    "emu_perf_overlay": False,
    "theme": "dark", "animated_bg": True, "text_scale": 100, "colorblind": False, "reduce_motion": False,
    "console_mode": False, "start_fullscreen": False,
    "theme_music": True, "trailer_music": False,
    "lightbar_sync": False, "lightbar_color": None,
    "onboarding_done": False,
    "update_repo": "", "update_auto": True,
}


def _ds4windows_running() -> bool:
    from .processes import running_executables
    return any(Path(p).stem.lower() == "ds4windows" for p in running_executables().values())


def _seconds_per_day(sessions: list[dict], days: int) -> list[dict]:
    """Segundos jogados em cada um dos últimos `days` dias; sessão que passa da meia-noite é dividida."""
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    first = today - timedelta(days=days - 1)
    totals = {(first + timedelta(days=i)).date(): 0 for i in range(days)}
    for s in sessions:
        start, end = datetime.fromtimestamp(s["start"]), datetime.fromtimestamp(s["end"])
        while start < end:
            midnight = start.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
            chunk_end = min(end, midnight)
            if start.date() in totals:
                totals[start.date()] += int((chunk_end - start).total_seconds())
            start = chunk_end
    return [{"date": d.isoformat(), "seconds": sec} for d, sec in totals.items()]


class Api:
    """Tudo aqui vira `window.pywebview.api.<método>()` no JavaScript.

    Atributos que começam com _ não são expostos (o pywebview tentaria serializá-los).
    """

    def __init__(self):
        self._window: webview.Window | None = None
        self._live: LiveService | None = None
        self._shell: Shell | None = None
        self._ffmpeg_install: dict = {"running": False}
        self._scan_lock = threading.Lock()
        self._maximized = False

    # ------------------------------------------------------------ dados

    def version(self) -> str:
        return __version__

    def list_games(self) -> list[dict]:
        library = Library()
        running = set(self._live.running()) if self._live else set()
        return [
            {
                "id": g.id,
                "name": g.display_name,
                "source": g.source,
                "favorite": g.favorite,
                "playCount": g.play_count,
                "playtime": g.playtime_seconds,
                "lastPlayed": g.last_played,
                "running": g.id in running,
                "trophies": achievements.load(g.id).summary(),
                "status": g.status,
            }
            for g in library.visible()
        ]

    def get_art(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        return get_art(game) if game else {}

    def get_trophies(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        return trophies_for_ui(game) if game else {"total": 0, "unlocked": 0, "trophies": []}

    def test_trophy_popup(self) -> None:
        if self._live:
            self._live.test_popup()

    # ------------------------------------------------------------ página do jogo

    def get_game_details(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        if not game:
            return {}
        sessions = read_sessions(game_id)
        started = self._live.monitor.session_started_at(game_id) if self._live else None
        if started:  # sessão em andamento também aparece no gráfico
            sessions.append({"game": game_id, "start": int(started), "end": int(time.time())})
        lengths = [s["end"] - s["start"] for s in sessions]
        return {
            "status": game.status,
            "statusAt": game.status_at,
            "notes": game.notes,
            "beatHours": game.beat_hours,
            "emulated": game.source in emulators.EMULATORS,
            "movable": game.source in disk.MOVABLE,
            "days": _seconds_per_day(sessions, days=30),
            "sessions": len(sessions),
            "longest": max(lengths, default=0),
            "average": int(sum(lengths) / len(lengths)) if lengths else 0,
            "firstSession": min((s["start"] for s in sessions), default=None),
            "addedAt": game.added_at,
            "playtime": game.playtime_seconds,
            "installDir": game.install_dir or (str(Path(game.exe).parent) if game.exe else None),
            "canArgs": game.launch_type == "exe",
            "args": subprocess.list2cmdline(game.extra_args),
            "customName": game.custom_name or "",
            "originalName": game.name,
            "hidden": game.hidden,
            "customCover": bool(custom_cover(game)),
            "rating": game.rating,
            "track": game.track,
        }

    def get_game_info(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        return gameinfo.get_info(game) if game else {}

    def get_game_news(self, game_id: str) -> list[dict]:
        game = Library().games.get(game_id)
        return gameinfo.get_news(game) if game else []

    def set_game_options(self, game_id: str, options: dict) -> dict:
        with LIBRARY_LOCK:
            library = Library()
            game = library.games[game_id]
            if "name" in options:
                game.custom_name = (options["name"] or "").strip() or None
            if "args" in options:
                game.extra_args = shlex.split(options["args"] or "", posix=False)
            if "hidden" in options:
                game.hidden = bool(options["hidden"])
            if "track" in options:
                game.track = bool(options["track"])
            if "rating" in options:
                rating = options["rating"]
                game.rating = int(rating) if rating and 1 <= int(rating) <= 5 else None
            if "status" in options:
                status = options["status"] if options["status"] in STATUSES else None
                if status != game.status:
                    game.status, game.status_at = status, now_iso()
            if "notes" in options:
                game.notes = (options["notes"] or "")[:5000]
            if "beat_hours" in options:
                value = options["beat_hours"]
                game.beat_hours = float(value) if value not in (None, "") and float(value) > 0 else None
            library.save()
        return {"ok": True, "name": game.display_name, "status": game.status}

    # ------------------------------------------------------------ biblioteca: sortear, tempo para zerar, disco

    def random_game(self, only_backlog: bool = True) -> dict | None:
        """Sorteia um jogo: do backlog ("Quero jogar"), ou dos nunca/pouco jogados se não houver backlog."""
        import random
        games = [g for g in Library().visible() if g.track]
        pool = [g for g in games if g.status == "backlog"] if only_backlog else []
        pool = pool or [g for g in games if g.status not in ("beaten", "platinum", "abandoned") and g.playtime_seconds < 2 * 3600]
        pool = pool or games
        pick = random.choice(pool) if pool else None
        return {"id": pick.id, "name": pick.display_name, "pool": [g.id for g in pool]} if pick else None

    def get_time_to_beat(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        return timetobeat.get(game) if game else {}

    def set_igdb_keys(self, client_id: str, secret: str) -> dict:
        if client_id.strip() and not timetobeat.check_keys(client_id, secret):
            return {"ok": False, "error": "A Twitch recusou essas chaves. Confira o Client ID e o Client Secret."}
        config = load_config()
        config["igdb_client_id"], config["igdb_client_secret"] = client_id.strip(), secret.strip()
        save_config(config)
        return {"ok": True}

    def get_disk(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        if not game:
            return {}
        info = disk.info(game)
        if info["size"] is None and info["path"]:   # calcula em segundo plano; a interface pergunta de novo
            threading.Thread(target=disk.size, args=(game,), daemon=True).start()
        return info

    def move_game(self, game_id: str) -> dict:
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if not picked:
            return {"ok": False, "cancelled": True}
        return disk.move(game_id, picked[0])

    def get_free_games(self) -> dict:
        return freegames.fetch()

    # ------------------------------------------------------------ saves

    # ------------------------------------------------------------ fontes e downloads (sources.py, downloads.py)

    def sources_list(self) -> dict:
        return sources.stats()

    def sources_add(self, url: str) -> dict:
        return sources.add_source(url)

    def sources_remove(self, source_id: str) -> dict:
        sources.remove_source(source_id)
        return sources.stats()

    def sources_refresh(self) -> dict:
        sources.refresh(force=True)
        return sources.stats()

    def store_search(self, query: str) -> list[dict]:
        owned = {sources.norm(g.display_name).replace(" ", "") for g in Library().games.values()}
        groups = sources.search(query)
        for group in groups:
            group["owned"] = group["key"] in owned
        return groups

    def store_art(self, name: str) -> dict:
        return sources.steam_art(name)

    def downloads_list(self) -> dict:
        _wire_downloads(self)
        m = downloads.manager()
        config = load_config()
        return {"jobs": m.list(), "summary": m.summary(), "dir": downloads.downloads_dir(),
                "torrent": downloads.lt is not None, "seed": bool(config.get("downloads_seed")),
                "limit": int(config.get("downloads_limit_kb") or 0), "sevenZip": bool(downloads.seven_zip())}

    def download_start(self, option: dict, uri: str, folder: str | None = None) -> dict:
        return downloads.manager().start(option, uri, folder)

    def download_action(self, job_id: str, action: str, arg: str | None = None) -> dict:
        m = downloads.manager()
        if action == "pause":
            m.pause(job_id)
        elif action == "resume":
            m.resume(job_id)
        elif action == "cancel":
            m.cancel(job_id, delete_files=True)
        elif action == "remove":
            m.cancel(job_id, delete_files=False)
        elif action == "extract":
            return m.extract(job_id)
        elif action == "clean":
            return {"ok": True, "freed": m.delete_archives(job_id)}
        elif action == "setup":
            return m.run_setup(job_id, arg or "")
        elif action == "open":
            job = m.jobs.get(job_id)
            if job and Path(job["dir"]).is_dir():
                os.startfile(job["dir"])
        elif action == "library":
            return self._add_download_to_library(job_id)
        return {"ok": True}

    def _add_download_to_library(self, job_id: str) -> dict:
        job = downloads.manager().jobs.get(job_id)
        if not job:
            return {"ok": False, "error": "download não encontrado"}
        root = job.get("root") or str(Path(job["dir"]).parent)
        config = load_config()
        if root not in config.get("scan_folders", []):
            config["scan_folders"] = config.get("scan_folders", []) + [root]   # cada subpasta vira um jogo
            save_config(config)
        report = self.scan()
        return {"ok": True, "new": report["new"]}

    def download_choose_folder(self) -> str | None:
        picked = self._window.create_file_dialog(webview.FOLDER_DIALOG, directory=downloads.downloads_dir())
        return picked[0] if picked else None

    def downloads_settings(self, changes: dict) -> dict:
        config = load_config()
        if "dir" in changes and changes["dir"]:
            config["downloads_dir"] = changes["dir"]
        if "seed" in changes:
            config["downloads_seed"] = bool(changes["seed"])
        if "limit" in changes:
            config["downloads_limit_kb"] = max(0, int(changes["limit"] or 0))
            session = downloads.manager()._session
            if session is not None:
                session.apply_settings({"download_rate_limit": config["downloads_limit_kb"] * 1024})
        save_config(config)
        return self.downloads_list()

    def disk_free(self, folder: str | None = None) -> dict:
        path = Path(folder or downloads.downloads_dir())
        while not path.exists() and path.parent != path:
            path = path.parent
        usage = shutil.disk_usage(path)
        return {"free": usage.free, "total": usage.total, "drive": path.anchor}

    def get_saves(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        if not game:
            return {}
        try:
            return saves.summary(game)
        except Exception as exc:
            return {"error": str(exc), "paths": [], "backups": []}

    def backup_saves(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        return saves.backup(game, "manual", int(load_config().get("save_keep", saves.KEEP_DEFAULT))) if game else {"ok": False}

    def restore_saves(self, game_id: str, name: str) -> dict:
        game = Library().games.get(game_id)
        if not game:
            return {"ok": False}
        if self._live and game_id in self._live.running():
            return {"ok": False, "error": "Feche o jogo antes de restaurar o save."}
        return saves.restore(game, name)

    def open_saves_folder(self, game_id: str | None = None) -> None:
        game = Library().games.get(game_id) if game_id else None
        os.startfile(saves.game_folder(game) if game else saves.backups_root())

    def open_save_location(self, path: str) -> None:
        target = Path(path)
        if target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)] if target.is_file() else ["explorer", str(target)])

    # ------------------------------------------------------------ preferências simples (config.json)

    def get_prefs(self) -> dict:
        config = load_config()
        return {key: config.get(key, default) for key, default in PREFS.items()}

    def set_prefs(self, changes: dict) -> dict:
        config = load_config()
        for key, value in changes.items():
            if key in PREFS:
                config[key] = value
        save_config(config)
        if "lightbar_sync" in changes or "lightbar_color" in changes:
            threading.Thread(target=self.sync_lightbar, daemon=True).start()
        if "emu_perf_overlay" in changes:
            for emu in emulators.EMULATORS.values():
                if emu.installed() and hasattr(emu, "base_config"):
                    try:
                        emu.configure()
                    except Exception as exc:
                        print("emulador:", emu.id, exc)
        if "console_mode" in changes and shell.autostart_enabled():
            shell.set_autostart(True)   # o comando de iniciar com o Windows muda (bandeja x tela cheia)
        return self.get_prefs()

    def list_running_apps(self) -> list[dict]:
        return gamemode.running_apps()

    # ------------------------------------------------------------ sobre, registro de erros e atualização

    def get_about(self) -> dict:
        return {"version": __version__, "logFile": str(logs.log_file()), "update": updater.check(),
                "updateRepo": updater.repo() or "", "frozen": getattr(sys, "frozen", False),
                "dataDir": str(data_dir())}

    def open_logs(self) -> None:
        os.startfile(logs.logs_dir())

    def check_update(self) -> dict:
        return updater.check(force=True)

    def install_update(self) -> dict:
        if not getattr(sys, "frozen", False):
            return {"ok": False, "error": "Atualização automática só funciona no GameDock instalado."}
        try:
            updater.download_and_install()
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        # o instalador espera o GameDock fechar para instalar por cima
        threading.Timer(1.0, lambda: self._shell.quit() if self._shell else self._window.destroy()).start()
        return {"ok": True}

    # ------------------------------------------------------------ controle (bateria / luz)

    def get_controller(self) -> dict:
        try:
            return {"battery": dualsense.battery(), "ds4windows": _ds4windows_running()}
        except Exception as exc:
            return {"battery": None, "error": str(exc)}

    def sync_lightbar(self) -> bool:
        config = load_config()
        if not config.get("lightbar_sync"):
            return False
        color = config.get("lightbar_color") or player.load_settings().get("color") or "#8b7bff"
        try:
            return dualsense.set_lightbar(dualsense.hex_to_rgb(color))
        except Exception as exc:
            print("luz do controle:", exc)
            return False

    # ------------------------------------------------------------ desafios, calendário e música

    def get_challenges(self) -> dict:
        return challenges.compute(Library())

    def get_calendar(self) -> dict:
        return challenges.calendar(Library())

    def get_theme_music(self, game_id: str) -> str | None:
        game = Library().games.get(game_id)
        if not game or not load_config().get("theme_music", True):
            return None
        try:
            path = mediaedit.theme_music(game)
        except Exception as exc:
            print("música do jogo:", exc)
            return None
        return as_url(path, game) if path else None

    # ------------------------------------------------------------ edição de capturas

    def trim_clip(self, capture_id: str, start: float, end: float, vertical: bool = False) -> dict:
        return mediaedit.trim_clip(capture_id, start, end, vertical)

    def save_edited_image(self, capture_id: str, data_url: str) -> dict:
        return mediaedit.save_edited_image(capture_id, data_url)

    def share_discord(self, capture_id: str, text: str = "") -> dict:
        return mediaedit.share_discord(capture_id, text)

    def copy_image(self, capture_id: str) -> bool:
        """Copia a captura para a área de transferência (Ctrl+V no Discord/WhatsApp)."""
        item = next((c for c in captures.all_captures() if c["id"] == capture_id), None)
        path = mediaedit.capture_file(item) if item else None
        if not path or item["kind"] == "clip":
            return False
        ps = (f"Add-Type -AssemblyName System.Windows.Forms,System.Drawing; "
              f"[System.Windows.Forms.Clipboard]::SetImage([System.Drawing.Image]::FromFile('{str(path).replace(chr(39), chr(39) * 2)}'))")
        result = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True,
                                creationflags=0x08000000, timeout=20)
        return result.returncode == 0

    # ------------------------------------------------------------ RetroAchievements

    def ra_status(self) -> dict:
        ra = retroach.account()
        return {"user": ra["user"] if ra else None, "apiKey": bool(ra and ra.get("api_key"))}

    def ra_login(self, user: str, password: str, api_key: str) -> dict:
        return retroach.login(user, password, api_key)

    def ra_logout(self) -> dict:
        retroach.logout()
        return self.ra_status()

    # ------------------------------------------------------------ melhorias por jogo (emuladores)

    def get_game_enhancements(self, game_id: str) -> dict:
        game = Library().games.get(game_id)
        emu = emulators.EMULATORS.get(game.source) if game else None
        if not emu or not emu.ENHANCEMENTS:
            return {"options": []}
        return {"options": emu.ENHANCEMENTS, "values": emu.game_settings(game_id), "emulator": emu.name}

    def set_game_enhancements(self, game_id: str, changes: dict) -> dict:
        game = Library().games.get(game_id)
        emu = emulators.EMULATORS.get(game.source) if game else None
        return emu.set_game_settings(game_id, changes) if emu else {}

    # ------------------------------------------------------------ painel: ajustes rápidos

    def get_quick(self) -> dict:
        data = {"performance": quickset.performance()}
        for key, fn in (("volume", quickset.get_volume), ("outputs", quickset.outputs), ("brightness", quickset.get_brightness)):
            try:
                data[key] = fn()
            except Exception as exc:
                data[key] = None
                print("ajustes rápidos:", key, exc)
        return data

    def get_performance(self) -> dict:
        return quickset.performance()

    def set_volume(self, level: int | None = None, muted: bool | None = None) -> dict:
        return quickset.set_volume(level, muted)

    def set_output(self, device_id: str) -> list[dict]:
        return quickset.set_output(device_id)

    def set_brightness(self, level: int) -> dict:
        return quickset.set_brightness(level)

    def panel_savestate(self, action: str) -> dict:
        return self._live.panel_savestate(action) if self._live else {"ok": False}

    def panel_quit_game(self) -> dict:
        return self._live.panel_quit_game() if self._live else {"ok": False}

    def list_hidden_games(self) -> list[dict]:
        return [{"id": g.id, "name": g.display_name} for g in Library().games.values() if g.hidden and g.installed]

    def open_game_folder(self, game_id: str) -> bool:
        details = self.get_game_details(game_id)
        folder = details.get("installDir")
        if folder and Path(folder).is_dir():
            os.startfile(folder)
            return True
        return False

    def choose_cover(self, game_id: str) -> dict | None:
        game = Library().games.get(game_id)
        picked = self._window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Imagens (*.png;*.jpg;*.jpeg;*.webp)",))
        if not game or not picked:
            return None
        set_custom_cover(game, Path(picked[0]))
        return get_art(game)

    def reset_cover(self, game_id: str) -> dict:
        game = Library().games[game_id]
        set_custom_cover(game, None)
        return get_art(game)

    # ------------------------------------------------------------ capturas

    def list_captures(self, game_id: str | None = None) -> list[dict]:
        library = Library()
        result = []
        for item in reversed(captures.all_captures()):
            if game_id and item["game"] != game_id:
                continue
            game = library.games.get(item["game"]) if item["game"] else None
            if item["kind"] == "clip":
                url = "/clips/" + quote(item["path"][len(clips.CLIP_PREFIX):])
            else:
                path = data_dir() / "art" / item["path"]
                url = f"/art/{quote(item['path'])}?v={int(path.stat().st_mtime)}"
            result.append({"id": item["id"], "url": url,
                           "ts": item["ts"], "kind": item["kind"], "trophy": item.get("trophy"),
                           "gameId": item["game"], "game": game.display_name if game else "Área de trabalho"})
        return result

    def delete_capture(self, capture_id: str) -> bool:
        return captures.remove(capture_id)

    def capture_now(self) -> None:
        if self._live:
            self._live.capture_now()

    # ------------------------------------------------------------ painel do Alt+G e clipes

    def get_panel(self) -> dict:
        return self._live.panel_data() if self._live else {"game": None}

    def panel_capture(self) -> None:
        self._live.panel_capture()

    def panel_clip(self) -> dict:
        return self._live.panel_clip()

    def panel_close(self) -> None:
        if self._live and self._live.panel:
            self._live.panel.hide()

    def panel_open_app(self) -> None:
        self.panel_close()
        if self._shell:
            self._shell.show()

    def get_recording(self) -> dict:
        config = load_config()
        return {"enabled": bool(config.get("clip_enabled")), "seconds": int(config.get("clip_seconds", 30)),
                "fps": int(config.get("clip_fps", 60)), "audio": bool(config.get("clip_audio", True)),
                "hotkey": config.get("clip_hotkey", captures.DEFAULT_CLIP_HOTKEY) or "",
                "ffmpegReady": recorder.ffmpeg_ready(), "folder": str(recorder.clips_dir()),
                "encoder": self._live.replay.encoder[0] if self._live and self._live.replay.encoder else None,
                "running": bool(self._live and self._live.replay.running),
                "install": self._ffmpeg_install}

    def set_recording(self, options: dict) -> dict:
        config = load_config()
        for key in ("enabled", "seconds", "fps", "audio"):
            if key in options:
                config[f"clip_{key}"] = options[key]
        save_config(config)
        if self._live:
            self._live.restart_replay()
        return self.get_recording()

    def set_clip_hotkey(self, name: str) -> dict:
        ok = self._live.set_clip_hotkey(name or None) if self._live else True
        if ok:
            config = load_config()
            config["clip_hotkey"] = name or None
            save_config(config)
        return {"ok": ok, "error": None if ok else f"Outro programa já usa {name} (ex: NVIDIA App). Escolha outra."}

    def install_ffmpeg(self) -> None:
        """Baixa o componente de gravação em segundo plano; a interface acompanha por get_recording()."""
        if self._ffmpeg_install.get("running"):
            return

        def work():
            self._ffmpeg_install = {"running": True, "done": 0, "total": 0, "error": None}
            try:
                recorder.install_ffmpeg(lambda d, t: self._ffmpeg_install.update(done=d, total=t))
            except Exception as exc:
                self._ffmpeg_install["error"] = str(exc)
            self._ffmpeg_install["running"] = False

        threading.Thread(target=work, daemon=True).start()

    def open_clips_folder(self) -> None:
        os.startfile(recorder.clips_dir())

    # ------------------------------------------------------------ emuladores

    def list_emulators(self) -> list[dict]:
        return [e.status() for e in emulators.EMULATORS.values()]

    def emulator_action(self, emu_id: str, action: str) -> dict:
        emu = emulators.EMULATORS[emu_id]
        if action == "install":
            emu.install()
        elif action == "firmware":
            emu.install_firmware()
        elif action == "controller":
            emu.configure_input()
        elif action == "bios":
            picked = self._window.create_file_dialog(webview.FileDialog.OPEN, file_types=("BIOS (*.bin;*.BIN;*.rom)", "Todos (*.*)"))
            if picked:
                try:
                    emu.install_bios(Path(picked[0]))
                except (OSError, RuntimeError) as exc:
                    emu.task = {"running": False, "error": str(exc)}
        elif action == "open":  # a janela completa do emulador, para ajustes avançados
            with clean_dll_search():
                subprocess.Popen([str(emu.exe)], cwd=str(emu.folder))
        return emu.status()

    def emulator_add_folder(self, emu_id: str) -> dict:
        emu = emulators.EMULATORS[emu_id]
        picked = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        if picked:
            folders = emu.settings()["folders"]
            if picked[0] not in folders:
                emu.save_settings({"folders": folders + [picked[0]]})
        return emu.status() | {"found": len(emu.scan())}

    def emulator_remove_folder(self, emu_id: str, folder: str) -> dict:
        emu = emulators.EMULATORS[emu_id]
        emu.save_settings({"folders": [f for f in emu.settings()["folders"] if f != folder]})
        return emu.status()

    def emulator_set(self, emu_id: str, changes: dict) -> dict:
        emu = emulators.EMULATORS[emu_id]
        emu.save_settings({k: v for k, v in changes.items() if k in ("fullscreen", "auto_controller")})
        return emu.status()

    # ------------------------------------------------------------ perfil e backup

    def get_profile(self) -> dict:
        library = Library()
        data = player.compute(library)
        games = library.games

        def art(game_id, kind):
            game = games.get(game_id) if game_id else None
            return get_art(game).get(kind) if game else None

        if data["avatarMode"] == "custom" and (img := player.custom_image("avatar")):
            data["avatarUrl"] = as_url(img, None)
        elif data["avatarMode"] == "game":
            data["avatarUrl"] = art(data["avatar"], "cover")
        else:
            data["avatarUrl"] = None

        if data["bannerMode"] == "custom" and (img := player.custom_image("banner")):
            data["bannerUrl"] = as_url(img, None)
        elif data["bannerMode"] == "game":
            data["bannerUrl"] = art(data["bannerGame"], "hero") or art(data["bannerGame"], "cover")
        else:  # padrão: a arte do jogo mais jogado
            top = data["top"][0]["id"] if data["top"] else None
            data["bannerUrl"] = art(top, "hero")

        if data["showcaseGame"]:
            game = games[data["showcaseGame"]]
            data["showcase"] = {"id": game.id, "name": game.display_name, "cover": art(game.id, "cover"),
                                "seconds": game.playtime_seconds, "trophies": achievements.load(game.id).summary()}
        featured = []
        for ref in data["showcaseTrophies"]:
            trophy = achievements.load(ref["game"]).trophies.get(ref["api"])
            if trophy and trophy.unlocked and ref["game"] in games:
                featured.append({"game": games[ref["game"]].display_name, "gameId": ref["game"], "api": trophy.api,
                                 "name": trophy.name, "icon": trophy.icon, "tier": trophy.tier, "percent": trophy.percent})
        data["featured"] = featured
        return data

    def set_profile(self, changes: dict) -> dict:
        player.update_settings(changes)
        return self.get_profile()

    def choose_profile_image(self, kind: str) -> dict | None:
        """Foto (kind="avatar") ou banner (kind="banner") escolhidos do computador."""
        picked = self._window.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Imagens (*.png;*.jpg;*.jpeg;*.webp)",))
        if not picked:
            return None
        source = Path(picked[0])
        for old in player.profile_dir().glob(f"{kind}*"):
            old.unlink(missing_ok=True)
        dest = player.profile_dir() / f"{kind}{source.suffix.lower()}"
        shutil.copy2(source, dest)
        if kind == "banner" and dest.stat().st_size > 1_500_000:
            shrunk = shrink_image(dest)  # banner de 10 MB deixaria a página pesada
            if shrunk != dest:
                dest.unlink()
                shrunk.rename(dest.with_suffix(".jpg"))
        return self.set_profile({f"{kind}_mode": "custom"})

    def list_unlocked_trophies(self) -> list[dict]:
        """Troféus que podem ir para a vitrine (mais raros primeiro)."""
        library = Library()
        result = []
        for game in library.visible():
            for t in achievements.load(game.id).trophies.values():
                if t.unlocked:
                    result.append({"game": game.display_name, "gameId": game.id, "api": t.api, "name": t.name,
                                   "icon": t.icon, "tier": t.tier, "percent": t.percent})
        return sorted(result, key=lambda t: t["percent"] if t["percent"] is not None else 100)

    def get_recap(self, period: str = "week", offset: int = 0) -> dict:
        live = {}
        if self._live:  # sessões em andamento também contam
            for gid in self._live.running():
                started = self._live.monitor.session_started_at(gid)
                if started:
                    live[gid] = {"game": gid, "start": started, "end": time.time()}
        library = Library()
        data = recap.compute(library, period, int(offset), live)
        for item in data["top"]:
            art = get_art(library.games[item["id"]])
            item["cover"], item["hero"] = art.get("cover"), art.get("hero") or art.get("cover")
        for c in data["captures"]:
            c["url"] = f"/art/{quote(c.pop('path'))}"
        return data

    def save_image(self, data_url: str, name: str) -> dict:
        """Salva um PNG gerado na interface (ex: card da retrospectiva) em Imagens\\GameDock."""
        import base64
        folder = Path.home() / "Pictures" / "GameDock"
        folder.mkdir(parents=True, exist_ok=True)
        safe = re.sub(r"[^\w\- ]+", "", name).strip() or "GameDock"
        dest = folder / f"{safe}.png"
        dest.write_bytes(base64.b64decode(data_url.split(",", 1)[1]))
        return {"ok": True, "path": str(dest)}

    def open_pictures(self) -> None:
        folder = Path.home() / "Pictures" / "GameDock"
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(folder)

    def make_backup(self) -> dict:
        try:
            return {"ok": True, "path": str(backup.make_backup())}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}

    def open_backups(self) -> None:
        os.startfile(backup.backups_dir())

    def scan(self) -> dict:
        with self._scan_lock:
            config = load_config()
            scanners = build_scanners(config)
            games, errors = run_scanners(scanners)   # a parte lenta fica fora da trava
            with LIBRARY_LOCK:
                library = Library()
                report = library.merge_scan(games, {s.name for s in scanners} | {g.source for g in games})
                library.save()
        return {
            "total": len(games),
            "new": [g.display_name for g in report["new"]],
            "missing": [g.display_name for g in report["missing"]],
            "errors": errors,
        }

    def play(self, game_id: str) -> dict:
        with LIBRARY_LOCK:
            library = Library()
            game = library.games.get(game_id)
            if game is None:
                return {"ok": False, "error": "Jogo não encontrado."}
            try:
                launch(game)
            except (LaunchError, OSError) as exc:
                return {"ok": False, "error": str(exc)}
            library.save()
        return {"ok": True}

    def toggle_favorite(self, game_id: str) -> bool:
        with LIBRARY_LOCK:
            library = Library()
            game = library.games[game_id]
            game.favorite = not game.favorite
            library.save()
        return game.favorite

    # ------------------------------------------------------------ configurações

    def get_settings(self) -> dict:
        config = load_config()
        return {
            "steamgriddbKey": config.get("steamgriddb_key") or "",
            "steamgriddbKeyPage": sgdb.KEY_PAGE,
            "sound": {
                "enabled": config.get("sound_enabled", True),
                "boot": config.get("sound_boot", True),
                "volume": config.get("sound_volume", 0.6),
            },
            "trophyPopup": config.get("trophy_popup", True),
            "trophyScreenshot": config.get("trophy_screenshot", True),
            "captureHotkey": config.get("capture_hotkey", captures.DEFAULT_HOTKEY) or "",
            "hotkeys": list(captures.HOTKEYS),
            "igdbClientId": config.get("igdb_client_id") or "",
            "igdbClientSecret": config.get("igdb_client_secret") or "",
        }

    def set_capture_hotkey(self, name: str) -> dict:
        name = name or None
        ok = self._live.set_hotkey(name) if self._live else True
        if ok:
            config = load_config()
            config["capture_hotkey"] = name
            save_config(config)
        return {"ok": ok, "error": None if ok else f"Outro programa já usa {name}. Escolha outra tecla."}

    def set_trophy_options(self, popup: bool, screenshot: bool) -> None:
        config = load_config()
        config["trophy_popup"] = bool(popup)
        config["trophy_screenshot"] = bool(screenshot)
        save_config(config)

    def set_sound(self, sound: dict) -> None:
        config = load_config()
        config["sound_enabled"] = bool(sound.get("enabled", True))
        config["sound_boot"] = bool(sound.get("boot", True))
        config["sound_volume"] = max(0.0, min(1.0, float(sound.get("volume", 0.6))))
        save_config(config)

    def set_steamgriddb_key(self, key: str) -> dict:
        key = (key or "").strip()
        if key and not sgdb.check_key(key):
            return {"ok": False, "error": "Chave recusada pelo SteamGridDB. Confira se copiou inteira."}
        config = load_config()
        config["steamgriddb_key"] = key or None
        save_config(config)
        clear_memory()  # a interface vai pedir a arte de novo, agora com o SteamGridDB
        return {"ok": True}

    def open_url(self, url: str) -> None:
        webbrowser.open(url)

    # ------------------------------------------------------------ janela

    def minimize(self) -> None:
        self._window.minimize()

    def toggle_maximize(self) -> None:
        if self._maximized:
            self._window.restore()
        else:
            self._window.maximize()
        self._maximized = not self._maximized

    def toggle_fullscreen(self) -> None:
        self._window.toggle_fullscreen()

    def close(self) -> None:
        """Botão ✕: vai para a bandeja (se ligado nas configurações) ou encerra."""
        if self._shell and load_config().get("close_to_tray", True):
            self._shell.hide_to_tray()
        else:
            self.quit()

    def quit(self) -> None:
        if self._shell:
            self._shell.quit()
        else:
            self._window.destroy()

    def after_launch(self, game_id: str | None = None) -> None:
        """Depois de dar play: sai da frente do jogo (e libera memória, se for para a bandeja).
        Jogos de emulador: só minimiza — o GameDock volta sozinho quando o jogo fecha (veja LiveService)."""
        game = Library().games.get(game_id) if game_id else None
        if game and game.launch_type == "emu":
            self._window.minimize()
        elif self._shell and load_config().get("close_to_tray", True):
            self._shell.hide_to_tray(quiet=True)
        else:
            self._window.minimize()

    def begin_resize(self, edge: str) -> None:
        """Redimensionar pelas bordas: a borda clicada no HTML vira o "arrasto de borda" nativo do Windows."""
        if self._shell:
            self._shell.begin_native_drag(RESIZE_EDGES.get(edge, HTCAPTION))

    def get_shell_settings(self) -> dict:
        return {"closeToTray": load_config().get("close_to_tray", True), "autostart": shell.autostart_enabled(),
                "frozen": getattr(sys, "frozen", False)}

    def set_shell_settings(self, close_to_tray: bool, autostart: bool) -> None:
        config = load_config()
        config["close_to_tray"] = bool(close_to_tray)
        save_config(config)
        shell.set_autostart(bool(autostart))


# códigos de "hit test" do Windows: qual borda está sendo arrastada
HTCAPTION, HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 2, 10, 11, 12, 13, 14, 15, 16, 17
RESIZE_EDGES = {"left": HTLEFT, "right": HTRIGHT, "top": HTTOP, "bottom": HTBOTTOM, "top-left": HTTOPLEFT,
                "top-right": HTTOPRIGHT, "bottom-left": HTBOTTOMLEFT, "bottom-right": HTBOTTOMRIGHT}
UNLOAD_AFTER = 15  # segundos escondido na bandeja até descarregar a interface


def _wire_downloads(api: "Api") -> None:
    """Liga o gerenciador de downloads à janela: a barra de título mostra o progresso."""
    m = downloads.manager()
    if m.on_change is None:
        def push(summary: dict) -> None:
            api._window.evaluate_js(f"window.onDownloads && window.onDownloads({json.dumps(summary)})")
        m.on_change = push


def on_screen(geometry: dict) -> bool:
    """A barra de título da janela cai dentro de algum monitor? (protege contra -32000 e monitor desconectado)"""
    import ctypes
    from ctypes import wintypes
    x, y, w = geometry.get("x"), geometry.get("y"), geometry.get("width", 0)
    if x is None or y is None:
        return False
    monitor_from_point = ctypes.windll.user32.MonitorFromPoint
    monitor_from_point.argtypes = (wintypes.POINT, wintypes.DWORD)
    monitor_from_point.restype = wintypes.HMONITOR
    MONITOR_DEFAULTTONULL = 0
    return bool(monitor_from_point(wintypes.POINT(int(x + min(w, 400) // 2), int(y + 20)), MONITOR_DEFAULTTONULL))


class Shell:
    """Ciclo de vida da janela principal: bandeja, descarregar a interface, voltar, sair."""

    def __init__(self, api: "Api", window, extra_windows, port: int):
        self.api = api
        self.window = window
        self.extra_windows = extra_windows
        self.index_url = f"http://127.0.0.1:{port}/index.html"
        self.sleep_url = f"http://127.0.0.1:{port}/sleep.html"
        self.hidden = False
        self.unloaded = False
        self.quitting = False
        self._unload_timer: threading.Timer | None = None
        self.tray = shell.Tray(on_open=self.show, on_quit=self.quit, on_capture=lambda: api.capture_now(),
                               recent_games=self.recent_games, on_play=self.play_from_tray)
        self._told_about_tray = load_config().get("told_about_tray", False)

    # bandeja
    def recent_games(self) -> list[tuple[str, str]]:
        games = [g for g in Library().visible() if g.last_played]
        games.sort(key=lambda g: g.last_played, reverse=True)
        return [(g.id, g.display_name) for g in games[:6]]

    def play_from_tray(self, game_id: str) -> None:
        self.api.play(game_id)

    def hide_to_tray(self, quiet: bool = False) -> None:
        self.save_geometry()
        self.window.hide()
        self.hidden = True
        if not quiet and not self._told_about_tray:  # só na primeira vez
            self.tray.notify("O GameDock continua aqui na bandeja, contando tempo e vigiando troféus.")
            config = load_config()
            config["told_about_tray"] = self._told_about_tray = True
            save_config(config)
        self._cancel_unload()
        self._unload_timer = threading.Timer(UNLOAD_AFTER, self.unload)
        self._unload_timer.daemon = True
        self._unload_timer.start()

    def unload(self) -> None:
        """Troca a interface por uma página vazia: o WebView2 devolve quase toda a memória."""
        if self.hidden and not self.unloaded:
            self.window.load_url(self.sleep_url)
            self.unloaded = True

    def show(self) -> None:
        self._cancel_unload()
        if self.unloaded:
            self.window.load_url(self.index_url + "?wake=1")  # wake=1: sem o som de abertura
            self.unloaded = False
        self.window.show()
        self.window.restore()
        self.hidden = False

    def _cancel_unload(self) -> None:
        if self._unload_timer:
            self._unload_timer.cancel()
            self._unload_timer = None

    def quit(self) -> None:
        self.quitting = True
        self.save_geometry()
        self.window.destroy()  # dispara on_closed, que fecha o resto

    def on_closed(self) -> None:
        self.quitting = True
        self.tray.stop()
        if downloads._manager is not None:
            downloads._manager.shutdown()   # guarda o progresso dos torrents
        for w in self.extra_windows:
            try:
                w.destroy()
            except Exception:
                pass

    def on_closing(self):
        """Alt+F4 também vai para a bandeja (a não ser que seja "Sair" de verdade)."""
        if self.quitting or not load_config().get("close_to_tray", True):
            self.quitting = True
            return True
        threading.Thread(target=self.hide_to_tray, daemon=True).start()
        return False

    # tamanho e posição da janela
    def save_geometry(self) -> None:
        try:
            if self.hidden or self.api._maximized:
                return
            import ctypes
            # minimizada, a janela fica em (-32000, -32000): salvar isso fazia o app abrir fora da tela
            if ctypes.windll.user32.IsIconic(int(self.window.native.Handle.ToInt64())):
                return
            geometry = {"x": self.window.x, "y": self.window.y, "width": self.window.width, "height": self.window.height}
            if not on_screen(geometry):
                return
            config = load_config()
            config["window"] = geometry
            save_config(config)
        except Exception:
            pass

    # redimensionar / arrastar
    def begin_native_drag(self, hit: int) -> None:
        import ctypes
        from System import Action
        user32 = ctypes.windll.user32
        hwnd = int(self.window.native.Handle.ToInt64())

        def start():  # precisa rodar na thread da janela: é ela que tem a "captura" do mouse
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, 0x00A1, hit, 0)  # WM_NCLBUTTONDOWN

        self.window.native.BeginInvoke(Action(start))


class _LocalFiles(SimpleHTTPRequestHandler):
    """Servidor local da interface: "/art/..." vem do cache de imagens, o resto de gamedock/web.

    Servir as imagens por URL (em vez de data URIs pela ponte JS) deixa o navegador baixar em
    paralelo e guardar em cache; antes eram ~9 MB de base64 a cada abertura.
    """

    # o registro do Windows às vezes mapeia .js como text/plain
    extensions_map = {**SimpleHTTPRequestHandler.extensions_map, ".js": "text/javascript",
                      ".css": "text/css", ".svg": "image/svg+xml", ".ico": "image/x-icon"}

    on_show = None  # chamado quando uma segunda instância do GameDock pede para a janela aparecer

    def do_GET(self) -> None:
        if self.path.startswith(shell.SHOW_PATH):
            self.send_response(204)
            self.end_headers()
            if _LocalFiles.on_show:
                threading.Thread(target=_LocalFiles.on_show, daemon=True).start()
            return
        if self.path.startswith("/clips/"):
            return self._serve_clip()
        super().do_GET()

    def _serve_clip(self) -> None:
        """Clipes da pasta de capturas do Windows, com suporte a Range (para o vídeo poder avançar)."""
        folder = clips.captures_dir().resolve()
        file = (folder / unquote(self.path[len("/clips/"):].split("?", 1)[0])).resolve()
        if not file.is_relative_to(folder) or file.suffix.lower() != ".mp4" or not file.is_file():
            self.send_error(404)
            return
        size = file.stat().st_size
        start, end = 0, size - 1
        match = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if match and (match.group(1) or match.group(2)):
            if match.group(1):
                start = int(match.group(1))
                end = int(match.group(2)) if match.group(2) else size - 1
            else:  # "bytes=-500": os últimos 500 bytes
                start = max(0, size - int(match.group(2)))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with file.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 16, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (ConnectionError, OSError):
                    break  # o player pediu outro trecho e fechou esta conexão
                remaining -= len(chunk)

    def translate_path(self, path: str) -> str:
        path = unquote(path.split("?", 1)[0].split("#", 1)[0])
        if path.startswith("/art/"):
            root, rel = ART_ROOT, path[len("/art/"):]
        else:
            root, rel = WEB_DIR, path.lstrip("/") or "index.html"
        full = (root / rel).resolve()
        if not full.is_relative_to(root.resolve()):  # nada de "../" para fora das duas pastas
            return str(root / "__proibido__")
        return str(full)

    def end_headers(self) -> None:
        # arte tem ?v=<data de modificação> na URL, então pode ficar em cache para sempre
        cache = "max-age=31536000, immutable" if self.path.startswith("/art/") else "no-cache"
        self.send_header("Cache-Control", cache)
        super().end_headers()

    def log_message(self, *args) -> None:
        pass


def start_server() -> int:
    """Sobe o servidor em 127.0.0.1 numa porta escolhida pelo Windows (faixa 49152+).

    Porta fixa ou sorteada pode cair numa que o Chromium bloqueia (ex: 6666 → ERR_UNSAFE_PORT,
    tela em branco); na faixa efêmera isso não acontece.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalFiles)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server.server_address[1]


def run() -> None:
    if "--uninstall" in sys.argv:
        from . import installer
        installer.uninstall()
        return
    logs.setup()
    if not shell.acquire_single_instance():
        # já tem um GameDock aberto: pede para ele aparecer e sai
        if "--tray" not in sys.argv:
            shell.wake_existing_instance()
        return
    # sons da interface (inclusive o de abertura) sem precisar de um clique antes
    os.environ.setdefault("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", "--autoplay-policy=no-user-gesture-required")
    port = start_server()
    shell.publish_instance(port)
    try:
        gamemode.deactivate()   # o GameDock fechou no meio de um jogo: desfaz o Modo Jogo que ficou ligado
    except Exception as exc:
        print("modo jogo:", exc)
    start_in_tray = "--tray" in sys.argv   # iniciado com o Windows
    geometry = load_config().get("window") or {}
    if not on_screen(geometry):   # posição salva fora de qualquer monitor: abre centralizado
        geometry = {k: v for k, v in geometry.items() if k in ("width", "height")}
    api = Api()
    api._window = webview.create_window(
        "GameDock",
        # na bandeja desde o início: a interface só carrega quando a janela for aberta
        url=f"http://127.0.0.1:{port}/{'sleep.html' if start_in_tray else 'index.html'}",
        js_api=api,
        width=geometry.get("width", 1360),
        height=geometry.get("height", 800),
        x=geometry.get("x"),
        y=geometry.get("y"),
        min_size=(960, 600),
        frameless=True,       # barra de título própria, desenhada no HTML
        easy_drag=False,      # arrastar só pela barra (classe pywebview-drag-region)
        hidden=start_in_tray,
        background_color="#07070a",
    )
    # aviso de troféu: criado escondido, sem foco e sempre por cima (veja overlay.py)
    trophy_window = webview.create_window(
        "GameDock Trophy",
        url=f"http://127.0.0.1:{port}/overlay.html",
        width=440, height=112,
        frameless=True, easy_drag=False, on_top=True, focus=False, x=-32000, y=-32000,  # visível, mas fora da tela (veja overlay.py)

        background_color="#101015",
    )
    # painel do Alt+G: mesma ideia (fora da tela, nunca ativável), mas com a API do app para buscar dados
    panel_window = webview.create_window(
        "GameDock Painel",
        url=f"http://127.0.0.1:{port}/panel.html",
        js_api=api,
        width=400, height=640,
        frameless=True, easy_drag=False, on_top=True, focus=False, x=-32000, y=-32000,
        background_color="#0e0e13",
    )
    api._live = LiveService(api._window, trophy_window, panel_window)
    api._shell = Shell(api, api._window, [trophy_window, panel_window], port)
    if start_in_tray:
        api._shell.hidden = api._shell.unloaded = True
    _LocalFiles.on_show = api._shell.show
    api._window.events.closing += api._shell.on_closing
    # as outras janelas ficam fora da tela; sem isto, fechar a principal deixaria o processo vivo
    api._window.events.closed += api._shell.on_closed

    def started():
        api._shell.tray.start()
        api._live.start()
        config = load_config()
        if "--console" in sys.argv or config.get("start_fullscreen"):
            api._window.toggle_fullscreen()
        if config.get("lightbar_sync"):
            threading.Thread(target=api.sync_lightbar, daemon=True).start()
        threading.Thread(target=sources.refresh, name="fontes", daemon=True).start()
        if (data_dir() / "downloads.json").is_file():   # tem download para continuar
            _wire_downloads(api)

    webview.start(started, debug=False)

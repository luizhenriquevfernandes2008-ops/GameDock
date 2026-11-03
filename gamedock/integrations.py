"""Discord e YouTube Music juntos: mantém a conexão, repassa os eventos para a interface e o Alt+G e mostra os
avisos por cima do jogo (mensagem do Discord, música nova)."""
from __future__ import annotations

import json
import os
import threading
import time

from . import discord_rpc
from .discord_rpc import DiscordClient, DiscordError
from .storage import load_config
from .ytmusic import MusicPlayer

RECONNECT_EVERY = 20.0


def _safe_url(url) -> str:
    """O aviso monta a imagem com innerHTML: só passa link https sem aspas nem sinais de HTML."""
    url = str(url or "")
    return url if url.startswith("https://") and not any(c in url for c in "\"'<> ") else ""


class Integrations:
    def __init__(self):
        self.windows: list = []           # janelas da interface que recebem os eventos (principal, Alt+G)
        self.overlay = None               # TrophyOverlay (avisos por cima do jogo)
        self.game_running = lambda: False
        self.discord = DiscordClient(on_event=self._discord_event)
        self.music = MusicPlayer(on_change=self._music_change)
        self._stop = threading.Event()
        self._last_try = 0.0

    def attach(self, windows: list, overlay, game_running) -> None:
        self.windows, self.overlay, self.game_running = windows, overlay, game_running
        threading.Thread(target=self._keep_discord, daemon=True, name="discord-keep").start()

    def stop(self) -> None:
        self._stop.set()
        self.discord.close()

    # ------------------------------------------------------------ interface

    def _push(self, fn: str, data) -> None:
        payload = json.dumps(data)
        for w in self.windows:
            try:
                w.evaluate_js(f"window.{fn} && window.{fn}({payload})")
            except Exception:
                pass

    # ------------------------------------------------------------ Discord

    def _client_id(self) -> str:
        return (load_config().get("discord_client_id") or "").strip()

    def _keep_discord(self) -> None:
        """Com a integração ligada e o login já feito, reconecta sozinho quando o Discord abre."""
        while not self._stop.wait(5):
            config = load_config()
            if not config.get("discord_control") or self.discord.connected:
                continue
            if time.time() - self._last_try < RECONNECT_EVERY or not discord_rpc.is_running():
                continue
            if not discord_rpc.load_token(self._client_id()):
                continue
            self._last_try = time.time()
            try:
                self.discord.connect(self._client_id(), interactive=False)
                self._push("onDiscord", {"type": "status", **self.discord_state()})
            except DiscordError:
                pass

    def discord_state(self) -> dict:
        cid = self._client_id()
        return {"enabled": bool(load_config().get("discord_control")), "clientId": bool(cid),
                "running": discord_rpc.is_running(), "loggedBefore": bool(cid and discord_rpc.load_token(cid)),
                "portal": discord_rpc.portal_url(cid), **self.discord.status()}

    def discord_connect(self) -> dict:
        self._last_try = time.time()
        try:
            self.discord.connect(self._client_id(), interactive=True)
            return {"ok": True, **self.discord_state()}
        except DiscordError as exc:
            return {"ok": False, "error": str(exc), **self.discord_state()}

    def discord_logout(self) -> dict:
        self.discord.logout()
        return self.discord_state()

    def discord_do(self, fn, *args, **kwargs) -> dict:
        """Executa um comando do Discord e devolve {"ok", "data"|"error"} para a interface."""
        if not self.discord.connected:
            if not load_config().get("discord_control") or not discord_rpc.load_token(self._client_id()):
                return {"ok": False, "error": "conecte o Discord primeiro"}
            try:
                self.discord.connect(self._client_id(), interactive=False)
            except DiscordError as exc:
                return {"ok": False, "error": str(exc)}
        try:
            return {"ok": True, "data": fn(*args, **kwargs)}
        except DiscordError as exc:
            return {"ok": False, "error": str(exc)}

    def discord_call(self) -> dict:
        def both():
            call = self.discord.call()
            return {"call": call, "voice": self.discord.voice_settings()}
        return self.discord_do(both)

    def _discord_event(self, evt: str, data: dict) -> None:
        if evt in ("SPEAKING_START", "SPEAKING_STOP"):
            self._push("onDiscord", {"type": "speaking", "user": str(data.get("user_id")), "on": evt == "SPEAKING_START"})
        elif evt in ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE", "CALL_CHANGED"):
            self._push("onDiscord", {"type": "call"})
        elif evt == "VOICE_SETTINGS_UPDATE":
            self._push("onDiscord", {"type": "voice", "mute": bool(data.get("mute")), "deaf": bool(data.get("deaf"))})
        elif evt == "DISCONNECTED":
            self._push("onDiscord", {"type": "status", **self.discord_state()})
        elif evt == "NOTIFICATION_CREATE":
            self._discord_notification(data)

    def _discord_notification(self, data: dict) -> None:
        config = load_config()
        if not config.get("discord_notify", True) or not self.overlay or not self.game_running():
            return
        message = data.get("message") or {}
        author = message.get("author") or {}
        self.overlay.show({"kind": "discord", "label": data.get("title") or "Discord",
                           "name": (data.get("body") or message.get("content") or "")[:120],
                           "game": author.get("global_name") or author.get("username") or "",
                           "icon": _safe_url(data.get("icon_url") or (discord_rpc.avatar_url(author) if author.get("id") else "")),
                           "sound": config.get("sound_enabled", True), "volume": config.get("sound_volume", 0.6)})

    # ------------------------------------------------------------ YouTube Music

    def _music_change(self, st: dict) -> None:
        self._push("onMusic", st)
        if not st.get("changed") or not st.get("playing"):
            return
        config = load_config()
        if config.get("music_notify", True) and self.overlay and self.game_running():
            self.overlay.show({"kind": "music", "name": st.get("title", ""),
                               "game": " · ".join(x for x in (st.get("artist"), st.get("album")) if x),
                               "icon": _safe_url(st.get("art")), "sound": False})

    def music_call(self, fn, *args) -> dict:
        try:
            return {"ok": True, "data": fn(*args)}
        except RuntimeError as exc:
            return {"ok": False, "error": str(exc)}


def open_discord_link(guild_id: str | None, channel_id: str) -> None:
    os.startfile(f"discord://-/channels/{guild_id or '@me'}/{channel_id}")

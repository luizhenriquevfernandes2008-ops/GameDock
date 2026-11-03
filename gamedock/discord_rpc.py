"""Controle do Discord pelo GameDock: servidores, calls, quem está falando, mutar, ensurdecer, volume de cada pessoa.

Usa o RPC local do próprio Discord (o pipe \\\\.\\pipe\\discord-ipc-N, o mesmo do "Jogando ..."), não um bot nem a
conta da pessoa por fora. O fluxo, uma vez só:
  AUTHORIZE → o Discord mostra "GameDock quer acessar sua conta" → a pessoa aceita → volta um código →
  o GameDock troca o código por um token (PKCE, sem segredo: o app precisa de "Public Client" ligado) →
  AUTHENTICATE. O token fica guardado criptografado pela DPAPI do Windows e é renovado sozinho.

Limites do próprio Discord (não dá para contornar sem violar os termos):
- o RPC não manda mensagens; o GameDock mostra as calls e abre o canal de texto no app do Discord;
- enquanto o app não for aprovado pelo Discord, só quem está na lista "App Testers" do app (até 50) consegue
  autorizar. O dono do app já pode.
"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import queue
import secrets
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from ctypes import wintypes
from typing import Callable

from .storage import data_dir

TOKEN_URL = "https://discord.com/api/oauth2/token"
REDIRECT_URI = "http://localhost"
SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write", "rpc.notifications.read", "identify"]
CDN = "https://cdn.discordapp.com"
PIPE_DIR = "\\\\.\\pipe\\"   # \\.\pipe\

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4
VOICE_TYPES = {2, 13}       # canal de voz, palco
TEXT_TYPES = {0, 5, 15}     # texto, anúncios, fórum

# códigos de erro do RPC que viram mensagens em português
ERRORS = {
    4000: "pedido inválido",
    4002: "o Discord recusou o comando",
    4006: "o GameDock ainda não está conectado à sua conta",
    5000: "o Discord recusou a autorização",
    5003: "você já está em outra call",
    5004: "o tempo para entrar na call acabou",
}


class DiscordError(Exception):
    pass


# ---------------------------------------------------------------- token guardado com a DPAPI

class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi(data: bytes, protect: bool) -> bytes:
    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    blob_in = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    blob_out = _Blob()
    fn = crypt32.CryptProtectData if protect else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(blob_in), None, None, None, None, 0x1, ctypes.byref(blob_out)):  # 0x1 = sem UI
        raise OSError("DPAPI falhou")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        kernel32.LocalFree(blob_out.pbData)


def _token_path():
    return data_dir() / "discord_token.bin"


def load_token(client_id: str) -> dict | None:
    try:
        token = json.loads(_dpapi(_token_path().read_bytes(), protect=False))
        return token if token.get("client_id") == client_id else None
    except (OSError, ValueError):
        return None


def save_token(token: dict) -> None:
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_dpapi(json.dumps(token).encode(), protect=True))


def forget_token() -> None:
    try:
        _token_path().unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- OAuth com PKCE

def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def _post_token(form: dict) -> dict:
    req = urllib.request.Request(TOKEN_URL, data=urllib.parse.urlencode(form).encode(), method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded",
                                          "User-Agent": "GameDock (Windows game launcher)"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b"{}")
        except ValueError:
            body = {}
        err = body.get("error", "")
        if err == "invalid_client":
            raise DiscordError("ligue a opção \"Public Client\" na aba OAuth2 do seu app no portal do Discord") from exc
        if "redirect" in json.dumps(body).lower():
            raise DiscordError(f"adicione {REDIRECT_URI} em OAuth2 → Redirects no portal do Discord") from exc
        if err == "invalid_grant":
            raise DiscordError("o login expirou, conecte de novo") from exc
        raise DiscordError(f"o Discord recusou o login ({err or exc.code})") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise DiscordError(f"sem conexão com o Discord ({exc.__class__.__name__})") from exc
    return {"access_token": body["access_token"], "refresh_token": body.get("refresh_token", ""),
            "expires_at": time.time() + int(body.get("expires_in", 604800))}


# ---------------------------------------------------------------- cliente

class DiscordClient:
    """Uma conexão com o Discord aberto no PC. Um leitor em segundo plano entrega as respostas (pelo nonce) e os
    eventos assinados (quem entrou/saiu da call, quem está falando, notificações)."""

    def __init__(self, on_event: Callable[[str, dict], None] | None = None):
        self.on_event = on_event or (lambda evt, data: None)
        self.pipe = None
        self.client_id = ""
        self.user: dict | None = None
        self.connected = False
        self.error = ""
        self.write_lock = threading.Lock()
        self.connect_lock = threading.Lock()
        self.pending: dict[str, queue.Queue] = {}
        self.reader: threading.Thread | None = None
        self.call_channel: str | None = None      # canal de voz assinado agora
        self.speaking: set[str] = set()

    # --- transporte
    def _write(self, op: int, payload: dict) -> None:
        data = json.dumps(payload).encode()
        with self.write_lock:
            if not self.pipe:
                raise DiscordError("o Discord não está aberto")
            try:
                self.pipe.write(struct.pack("<II", op, len(data)) + data)
                self.pipe.flush()
            except OSError as exc:
                raise DiscordError("o Discord fechou a conexão") from exc

    def _read_exact(self, n: int) -> bytes:
        out = b""
        while len(out) < n:
            chunk = self.pipe.read(n - len(out))
            if not chunk:
                raise OSError("pipe fechado")
            out += chunk
        return out

    def _read_frame(self) -> tuple[int, dict]:
        op, length = struct.unpack("<II", self._read_exact(8))
        body = self._read_exact(length) if length else b"{}"
        return op, json.loads(body or b"{}")

    def _available(self, pipe) -> int:
        """Bytes esperando no pipe, sem ler. Necessário porque um pipe síncrono do Windows faz uma operação por vez:
        um ReadFile parado esperando o Discord falar travaria qualquer WriteFile (todo comando) atrás dele."""
        import msvcrt
        avail = wintypes.DWORD(0)
        handle = msvcrt.get_osfhandle(pipe.fileno())
        if not ctypes.windll.kernel32.PeekNamedPipe(wintypes.HANDLE(handle), None, 0, None, ctypes.byref(avail), None):
            raise OSError("pipe fechado")
        return avail.value

    def _reader_loop(self, pipe) -> None:
        while self.pipe is pipe:
            try:
                if self._available(pipe) < 8:
                    time.sleep(0.04)
                    continue
                op, data = self._read_frame()
            except (OSError, ValueError, struct.error):
                break
            if op == OP_PING:
                try:
                    self._write(OP_PONG, data)
                except DiscordError:
                    break
                continue
            if op == OP_CLOSE:
                self.error = data.get("message") or "o Discord encerrou a conexão"
                break
            nonce = data.get("nonce")
            if nonce and nonce in self.pending:
                self.pending[nonce].put(data)
            elif data.get("cmd") == "DISPATCH":
                self._dispatch(data.get("evt") or "", data.get("data") or {})
        if self.pipe is pipe:
            self._drop()
            self.on_event("DISCONNECTED", {})

    def command(self, cmd: str, args: dict | None = None, evt: str | None = None, timeout: float = 10) -> dict:
        nonce = str(uuid.uuid4())
        box: queue.Queue = queue.Queue()
        self.pending[nonce] = box
        payload = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            payload["evt"] = evt
        try:
            self._write(OP_FRAME, payload)
            try:
                data = box.get(timeout=timeout)
            except queue.Empty:
                raise DiscordError("o Discord não respondeu") from None
        finally:
            self.pending.pop(nonce, None)
        if data.get("evt") == "ERROR":
            err = data.get("data") or {}
            code = err.get("code")
            raise DiscordError(ERRORS.get(code) or err.get("message") or "erro do Discord")
        return data.get("data") or {}

    # --- conexão
    def _open_pipe(self):
        for n in range(10):
            try:
                return open(rf"\\.\pipe\discord-ipc-{n}", "r+b", buffering=0)
            except OSError:
                continue
        return None

    def connect(self, client_id: str, interactive: bool) -> dict:
        """Conecta e autentica. interactive=False só usa o token guardado (nunca abre o pedido no Discord)."""
        with self.connect_lock:
            if self.connected and self.client_id == client_id:
                return self.status()
            self.close()
            if not client_id:
                raise DiscordError("coloque o Application ID em Configurações → Discord")
            pipe = self._open_pipe()
            if not pipe:
                raise DiscordError("abra o Discord no PC")
            self.pipe, self.client_id, self.error = pipe, client_id, ""
            try:
                self._write(OP_HANDSHAKE, {"v": 1, "client_id": client_id})
                op, ready = self._read_frame()
                if op == OP_CLOSE or ready.get("evt") != "READY":
                    raise DiscordError(ready.get("message") or "o Discord recusou o Application ID")
            except (OSError, ValueError, struct.error) as exc:
                self._drop()
                raise DiscordError("o Discord não respondeu") from exc
            except DiscordError:
                self._drop()
                raise
            self.reader = threading.Thread(target=self._reader_loop, args=(pipe,), daemon=True, name="discord-rpc")
            self.reader.start()
            try:
                self._login(interactive)
            except DiscordError:
                self._drop()
                raise
            self.connected = True
            self._subscribe_global()
            return self.status()

    def _login(self, interactive: bool) -> None:
        token = load_token(self.client_id)
        if token and token.get("expires_at", 0) > time.time() + 3600:
            try:
                self._authenticate(token["access_token"])
                return
            except DiscordError:
                pass
        if token and token.get("refresh_token"):
            try:
                fresh = _post_token({"client_id": self.client_id, "grant_type": "refresh_token",
                                     "refresh_token": token["refresh_token"]})
                fresh["client_id"] = self.client_id
                save_token(fresh)
                self._authenticate(fresh["access_token"])
                return
            except DiscordError:
                pass
        if not interactive:
            raise DiscordError("conecte o Discord no GameDock")
        verifier, challenge = _pkce()
        data = self.command("AUTHORIZE", {"client_id": self.client_id, "scopes": SCOPES,
                                          "code_challenge": challenge, "code_challenge_method": "S256"},
                            timeout=180)
        if not data.get("code"):
            raise DiscordError("o Discord não devolveu a autorização")
        fresh = _post_token({"client_id": self.client_id, "grant_type": "authorization_code", "code": data["code"],
                             "redirect_uri": REDIRECT_URI, "code_verifier": verifier})
        fresh["client_id"] = self.client_id
        save_token(fresh)
        self._authenticate(fresh["access_token"])

    def _authenticate(self, access_token: str) -> None:
        data = self.command("AUTHENTICATE", {"access_token": access_token})
        user = data.get("user") or {}
        if not user.get("id"):
            raise DiscordError("o Discord não confirmou a conta")
        self.user = {"id": user["id"], "name": user.get("global_name") or user.get("username") or "",
                     "avatar": avatar_url(user)}

    def _drop(self) -> None:
        pipe, self.pipe = self.pipe, None
        self.connected = False
        self.call_channel = None
        self.speaking.clear()
        if pipe:
            try:
                pipe.close()
            except OSError:
                pass
        for box in list(self.pending.values()):
            box.put({"evt": "ERROR", "data": {"message": "o Discord fechou a conexão"}})

    def close(self) -> None:
        self._drop()
        self.user = None

    def logout(self) -> None:
        self.close()
        forget_token()

    def status(self) -> dict:
        return {"connected": self.connected, "user": self.user, "error": self.error}

    # --- eventos
    def _subscribe(self, evt: str, args: dict | None = None) -> None:
        try:
            self.command("SUBSCRIBE", args or {}, evt=evt)
        except DiscordError:
            pass

    def _unsubscribe(self, evt: str, args: dict | None = None) -> None:
        try:
            self.command("UNSUBSCRIBE", args or {}, evt=evt)
        except DiscordError:
            pass

    def _subscribe_global(self) -> None:
        for evt in ("VOICE_CHANNEL_SELECT", "VOICE_SETTINGS_UPDATE", "NOTIFICATION_CREATE"):
            self._subscribe(evt)
        threading.Thread(target=self._follow_call, daemon=True, name="discord-call").start()

    def _follow_call(self, channel_id: str | None = "?") -> None:
        """Troca as assinaturas de voz para a call em que a pessoa está agora."""
        if channel_id == "?":
            try:
                channel_id = (self.command("GET_SELECTED_VOICE_CHANNEL") or {}).get("id")
            except DiscordError:
                channel_id = None
        old = self.call_channel
        if old == channel_id:
            return
        events = ("VOICE_STATE_CREATE", "VOICE_STATE_UPDATE", "VOICE_STATE_DELETE", "SPEAKING_START", "SPEAKING_STOP")
        if old:
            for evt in events:
                self._unsubscribe(evt, {"channel_id": old})
        self.call_channel = channel_id
        self.speaking.clear()
        if channel_id:
            for evt in events:
                self._subscribe(evt, {"channel_id": channel_id})
        self.on_event("CALL_CHANGED", {"channel_id": channel_id})

    def _dispatch(self, evt: str, data: dict) -> None:
        if evt == "VOICE_CHANNEL_SELECT":
            threading.Thread(target=self._follow_call, args=(data.get("channel_id"),), daemon=True).start()
            return
        if evt == "SPEAKING_START":
            self.speaking.add(str(data.get("user_id")))
        elif evt == "SPEAKING_STOP":
            self.speaking.discard(str(data.get("user_id")))
        self.on_event(evt, data)

    # --- consultas
    def guilds(self) -> list[dict]:
        data = self.command("GET_GUILDS")
        return [{"id": g["id"], "name": g.get("name", ""), "icon": g.get("icon_url") or ""}
                for g in data.get("guilds", [])]

    def channels(self, guild_id: str) -> dict:
        """Canais do servidor: as calls (com quem está em cada uma) e os canais de texto."""
        data = self.command("GET_CHANNELS", {"guild_id": guild_id})
        chans = data.get("channels", [])
        voice = [c for c in chans if c.get("type") in VOICE_TYPES]
        text = [{"id": c["id"], "name": c.get("name", "")} for c in chans if c.get("type") in TEXT_TYPES]
        out = []
        for c in voice:
            try:
                full = self.command("GET_CHANNEL", {"channel_id": c["id"]})
            except DiscordError:
                full = c
            out.append({"id": c["id"], "name": c.get("name", ""), "stage": c.get("type") == 13,
                        "limit": full.get("user_limit") or 0,
                        "members": [member(v, self.speaking) for v in full.get("voice_states") or []]})
        return {"voice": out, "text": text}

    def call(self) -> dict | None:
        """A call atual: canal, servidor e quem está nela."""
        data = self.command("GET_SELECTED_VOICE_CHANNEL")
        if not data or not data.get("id"):
            return None
        guild_name = ""
        if data.get("guild_id"):
            try:
                guild_name = self.command("GET_GUILD", {"guild_id": data["guild_id"]}).get("name", "")
            except DiscordError:
                pass
        return {"id": data["id"], "name": data.get("name", ""), "guild_id": data.get("guild_id"),
                "guild": guild_name,
                "members": [member(v, self.speaking) for v in data.get("voice_states") or []]}

    def voice_settings(self) -> dict:
        data = self.command("GET_VOICE_SETTINGS")
        return {"mute": bool(data.get("mute")), "deaf": bool(data.get("deaf")),
                "input": (data.get("input") or {}).get("volume"), "output": (data.get("output") or {}).get("volume")}

    # --- ações
    def join(self, channel_id: str) -> None:
        self.command("SELECT_VOICE_CHANNEL", {"channel_id": channel_id, "force": True, "timeout": 20}, timeout=25)

    def leave(self) -> None:
        self.command("SELECT_VOICE_CHANNEL", {"channel_id": None, "force": True})

    def set_voice(self, **settings) -> dict:
        allowed = {k: v for k, v in settings.items() if k in ("mute", "deaf")}
        if "output" in settings:
            allowed["output"] = {"volume": max(0, min(200, int(settings["output"])))}
        if "input" in settings:
            allowed["input"] = {"volume": max(0, min(100, int(settings["input"])))}
        self.command("SET_VOICE_SETTINGS", allowed)
        return self.voice_settings()

    def set_user(self, user_id: str, volume: int | None = None, mute: bool | None = None) -> None:
        args: dict = {"user_id": user_id}
        if volume is not None:
            args["volume"] = max(0, min(200, int(volume)))
        if mute is not None:
            args["mute"] = bool(mute)
        self.command("SET_USER_VOICE_SETTINGS", args)

    def open_text(self, channel_id: str) -> None:
        self.command("SELECT_TEXT_CHANNEL", {"channel_id": channel_id})


def avatar_url(user: dict) -> str:
    if user.get("avatar"):
        ext = "gif" if str(user["avatar"]).startswith("a_") else "png"
        return f"{CDN}/avatars/{user['id']}/{user['avatar']}.{ext}?size=64"
    try:
        index = (int(user["id"]) >> 22) % 6
    except (KeyError, ValueError):
        index = 0
    return f"{CDN}/embed/avatars/{index}.png"


def member(state: dict, speaking: set[str]) -> dict:
    user = state.get("user") or {}
    vs = state.get("voice_state") or {}
    return {"id": user.get("id", ""), "name": state.get("nick") or user.get("global_name") or user.get("username", ""),
            "avatar": avatar_url(user), "bot": bool(user.get("bot")),
            "mute": bool(vs.get("self_mute") or vs.get("mute") or vs.get("suppress")),
            "deaf": bool(vs.get("self_deaf") or vs.get("deaf")),
            "volume": state.get("volume", 100), "localMute": bool(state.get("mute")),
            "speaking": str(user.get("id")) in speaking}


def portal_url(client_id: str) -> str:
    return f"https://discord.com/developers/applications/{client_id}/oauth2" if client_id \
        else "https://discord.com/developers/applications"


def is_running() -> bool:
    try:
        return any(name.startswith("discord-ipc-") for name in os.listdir(PIPE_DIR))
    except OSError:
        return False

"""Ouvir junto na call: quem transmite manda "o que está tocando e em que segundo"; os amigos da mesma call tocam a
mesma música no YouTube Music deles, sincronizada.

Por que não jogar o áudio dentro da call: isso é um bot de música com áudio do YouTube, proibido pelos termos do
YouTube (foi o que derrubou Rythm e Groovy). Aqui cada pessoa toca no próprio player oficial; só a "partitura" viaja.

Transporte: ntfy.sh (pub/sub HTTP público e grátis, sem conta). O tópico sai do ID do canal de voz misturado com um
segredo do GameDock: todo mundo na mesma call cai no mesmo tópico sem convite nenhum. A mensagem leva só o ID do
vídeo, título, artista, capa, se está tocando e o segundo — nada da conta de ninguém.

Mensagens (JSON):
  {"v":1,"kind":"state","host":{id,name,avatar},"video","title","artist","art","playing","pos","at"}
  {"v":1,"kind":"stop","host":{...}}
  {"v":1,"kind":"listen","user":{id,name,avatar},"host":"<id do host>"}      (quem está ouvindo, a cada 30 s)
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

RELAY = "https://ntfy.sh"
SALT = "gamedock-ouvir-junto-v1"
UA = "GameDock (Windows game launcher; listen-together)"
HEARTBEAT = 12.0        # o host repete o estado (quem entra na call no meio da música pega por aqui)
STALE = 40.0            # host sem notícia há mais que isso some da lista
TOLERANCE = 2.5         # diferença (s) que dispara um ajuste de posição


def topic_for(channel_id: str) -> str:
    return "gd-" + hashlib.sha256(f"{SALT}:{channel_id}".encode()).hexdigest()[:32]


class Relay:
    """Um tópico do ntfy: publicar e escutar (com reconexão) numa thread."""

    def __init__(self, topic: str, on_message: Callable[[dict], None]):
        self.topic = topic
        self.on_message = on_message
        self.stopped = threading.Event()
        self.resp = None
        threading.Thread(target=self._listen, daemon=True, name="listen-relay").start()

    def publish(self, payload: dict) -> bool:
        req = urllib.request.Request(f"{RELAY}/{self.topic}", data=json.dumps(payload).encode(), method="POST",
                                     headers={"User-Agent": UA, "Content-Type": "text/plain", "Cache": "yes"})
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                resp.read()
            return True
        except (urllib.error.URLError, OSError):
            return False

    def _listen(self) -> None:
        since = "1m"   # pega o último estado de quem já está transmitindo
        while not self.stopped.is_set():
            try:
                req = urllib.request.Request(f"{RELAY}/{self.topic}/json?since={since}", headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=90) as resp:
                    self.resp = resp
                    for line in resp:
                        if self.stopped.is_set():
                            return
                        try:
                            event = json.loads(line)
                        except ValueError:
                            continue
                        if event.get("event") != "message":
                            continue
                        since = event.get("id") or since
                        try:
                            data = json.loads(event.get("message") or "{}")
                        except ValueError:
                            continue
                        if isinstance(data, dict) and data.get("v") == 1:
                            self.on_message(data)
            except (urllib.error.URLError, OSError, ValueError):
                pass
            self.stopped.wait(3)

    def close(self) -> None:
        self.stopped.set()
        try:
            if self.resp:
                self.resp.close()
        except Exception:
            pass


class ListenTogether:
    def __init__(self, music, me: Callable[[], dict | None], push: Callable[[dict], None]):
        self.music = music
        self.me = me                    # {"id","name","avatar"} da conta do Discord conectada
        self.push = push                # manda o estado para a interface
        self.channel: str | None = None
        self.relay: Relay | None = None
        self.hosts: dict[str, dict] = {}        # id -> último estado (com "seen")
        self.listeners: dict[str, dict] = {}    # quem ouve a minha transmissão: id -> {user, seen}
        self.hosting = False
        self.following: str | None = None
        self.lock = threading.Lock()
        self._loop_started = False
        self._last_sent = None
        self._last_beat = 0.0
        self._last_listen_ping = 0.0
        self._loading = ""                      # vídeo que o seguidor mandou carregar (espera abrir)
        self._loading_since = 0.0

    # ------------------------------------------------------------ call
    def on_call(self, channel_id: str | None) -> None:
        """Entrou/saiu/trocou de call: troca o tópico que escuta. Sair da call encerra transmissão e escuta."""
        with self.lock:
            if channel_id == self.channel:
                return
            if self.hosting and self.relay:
                self._send({"kind": "stop"})
            if self.relay:
                self.relay.close()
            self.channel, self.relay = channel_id, None
            self.hosts.clear()
            self.listeners.clear()
            self.hosting, self.following = False, None
            if channel_id:
                self.relay = Relay(topic_for(channel_id), self._received)
        self._ensure_loop()
        self._notify()

    # ------------------------------------------------------------ host
    def start_hosting(self) -> dict:
        if not self.channel or not self.relay:
            return {"ok": False, "error": "entre numa call do Discord primeiro"}
        if not self.music.is_open() or not self.music.state().get("videoId"):
            return {"ok": False, "error": "toque uma música no YouTube Music primeiro"}
        self.following = None
        self.hosting = True
        self._last_sent = None
        self._ensure_loop()
        self._notify()
        return {"ok": True}

    def stop_hosting(self) -> None:
        if self.hosting:
            self._send({"kind": "stop"})
        self.hosting = False
        self.listeners.clear()
        self._notify()

    def _host_tick(self) -> None:
        st = self.music.state()
        if not st.get("open") or st.get("ad") or not st.get("videoId"):
            return
        key = (st["videoId"], bool(st.get("playing")))
        pos = float(st.get("time") or 0)
        expected = None
        if self._last_sent and self._last_sent["key"] == key:
            sent = self._last_sent
            expected = sent["pos"] + ((time.time() - sent["at"]) if key[1] else 0)
        jumped = expected is not None and abs(pos - expected) > TOLERANCE
        if self._last_sent is None or self._last_sent["key"] != key or jumped or time.time() - self._last_beat > HEARTBEAT:
            now = time.time()
            ok = self._send({"kind": "state", "video": st["videoId"], "title": st.get("title", ""),
                             "artist": st.get("artist", ""), "art": st.get("art", ""), "playing": key[1],
                             "pos": pos, "at": now, "duration": st.get("duration") or 0})
            if ok:
                self._last_sent = {"key": key, "pos": pos, "at": now}
                self._last_beat = now

    # ------------------------------------------------------------ ouvinte
    def follow(self, host_id: str) -> dict:
        if host_id not in self.hosts:
            return {"ok": False, "error": "essa transmissão acabou"}
        self.hosting = False
        self.following = host_id
        self._loading = ""
        if not self.music.is_open():
            self.music.open(show=False)
        self._ping_listen(force=True)
        self._ensure_loop()
        self._notify()
        return {"ok": True}

    def unfollow(self) -> None:
        self.following = None
        self._notify()

    def _follow_tick(self) -> None:
        host = self.hosts.get(self.following or "")
        if not host:
            return
        self._ping_listen()
        if not self.music.is_open():
            self.music.open(show=False)
            return
        target = host["pos"] + ((time.time() - host["at"]) if host["playing"] else 0)
        if host.get("duration") and target > host["duration"] + 1:
            return   # a música do host já acabou: espera ele mandar a próxima
        st = self.music.state()
        if st.get("ad"):
            return   # anúncio do YouTube (conta sem Premium): ajusta quando acabar
        if st.get("videoId") != host["video"]:
            if self._loading == host["video"] and time.time() - self._loading_since < 15:
                return   # já mandou abrir, esperando carregar
            self._loading, self._loading_since = host["video"], time.time()
            self.music.play(video=host["video"])
            return
        if not st.get("duration"):
            return
        if abs(float(st.get("time") or 0) - target) > TOLERANCE:
            self.music.control("seek", min(target, st["duration"] - 0.5))
        if bool(st.get("playing")) != host["playing"]:
            self.music.control("toggle")

    def _ping_listen(self, force: bool = False) -> None:
        if force or time.time() - self._last_listen_ping > 30:
            self._last_listen_ping = time.time()
            threading.Thread(target=self._send, args=({"kind": "listen", "host": self.following},), daemon=True).start()

    # ------------------------------------------------------------ mensagens
    def _send(self, payload: dict) -> bool:
        me = self.me()
        relay = self.relay
        if not me or not relay:
            return False
        payload = {"v": 1, **payload}
        payload["user" if payload["kind"] == "listen" else "host"] = me if payload["kind"] != "listen" else me
        if payload["kind"] == "listen":
            payload["host"] = self.following
        return relay.publish(payload)

    def _received(self, data: dict) -> None:
        me = (self.me() or {}).get("id")
        kind = data.get("kind")
        if kind in ("state", "stop"):
            host = data.get("host") or {}
            hid = str(host.get("id") or "")
            if not hid or hid == me:
                return
            if kind == "stop":
                self.hosts.pop(hid, None)
                if self.following == hid:
                    self.following = None
            else:
                try:
                    self.hosts[hid] = {"host": {"id": hid, "name": str(host.get("name") or ""),
                                                "avatar": str(host.get("avatar") or "")},
                                       "video": str(data["video"]), "title": str(data.get("title") or ""),
                                       "artist": str(data.get("artist") or ""), "art": str(data.get("art") or ""),
                                       "playing": bool(data.get("playing")), "pos": float(data.get("pos") or 0),
                                       "at": float(data.get("at") or time.time()),
                                       "duration": float(data.get("duration") or 0), "seen": time.time()}
                except (KeyError, TypeError, ValueError):
                    return
            self._notify()
        elif kind == "listen" and self.hosting and data.get("host") == me:
            user = data.get("user") or {}
            if user.get("id") and str(user["id"]) != me:
                self.listeners[str(user["id"])] = {"user": user, "seen": time.time()}
                self._notify()

    # ------------------------------------------------------------ laço
    def _ensure_loop(self) -> None:
        if not self._loop_started:
            self._loop_started = True
            threading.Thread(target=self._loop, daemon=True, name="listen-loop").start()

    def _loop(self) -> None:
        while True:
            time.sleep(1.0)
            try:
                now = time.time()
                for hid in [h for h, s in self.hosts.items() if now - s["seen"] > STALE]:
                    self.hosts.pop(hid, None)
                    if self.following == hid:
                        self.following = None
                    self._notify()
                for uid in [u for u, s in self.listeners.items() if now - s["seen"] > 75]:
                    self.listeners.pop(uid, None)
                    self._notify()
                if self.hosting:
                    self._host_tick()
                elif self.following:
                    self._follow_tick()
            except Exception as exc:
                print("ouvir junto:", exc)

    # ------------------------------------------------------------ interface
    def state(self) -> dict:
        return {"inCall": bool(self.channel), "hosting": self.hosting, "following": self.following,
                "listeners": [s["user"] for s in self.listeners.values()],
                "hosts": [{k: v for k, v in s.items() if k != "seen"} for s in self.hosts.values()]}

    def _notify(self) -> None:
        try:
            self.push(self.state())
        except Exception:
            pass

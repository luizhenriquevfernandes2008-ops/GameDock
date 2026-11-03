"""YouTube Music dentro do GameDock: o site oficial (music.youtube.com) numa janela própria, com a conta da pessoa.

Por que o site e não uma API: a API pública do YouTube não toca música fora do player do próprio YouTube, e extrair o
áudio quebraria os termos. Aqui quem toca é o YouTube Music de verdade (Premium vale, sem anúncio se a conta tiver);
o GameDock só lê o que está tocando, aperta os botões do player e pede ao próprio site a lista das suas playlists.

- A janela abre só quando a pessoa usa a música (o WebView2 gasta memória) e fechar o X só esconde (a música continua).
  "Parar e fechar" destrói a janela de verdade.
- O login fica salvo no perfil do WebView2 do GameDock (pasta "webview" nos dados do app).
- O site usa Trusted Types: nada de eval/innerHTML. Os scripts vão direto pelo ExecuteScript do WebView2
  (window.gui.evaluate_js) e o que é assíncrono grava o resultado em window.__gd, lido depois.
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Callable
from urllib.parse import quote

import webview

HOME = "https://music.youtube.com/"
WIDTH, HEIGHT = 1180, 780
OFFSCREEN = -32000
TITLE = "GameDock · YouTube Music"

# ----------------------------------------------------------------- scripts que rodam dentro do site

STATE_JS = r"""(() => {
  const p = document.getElementById('movie_player'), v = document.querySelector('video');
  const m = navigator.mediaSession && navigator.mediaSession.metadata;
  const cfg = window.ytcfg && ytcfg.get ? ytcfg : null;
  const art = m && m.artwork && m.artwork.length ? m.artwork[m.artwork.length - 1].src : '';
  const like = document.querySelector('ytmusic-player-bar ytmusic-like-button-renderer');
  // durante um anúncio o mediaSession mostra o anúncio: aí vale o que o player diz da música
  const ad = !!(p && p.classList && p.classList.contains('ad-showing'));
  const vd = p && p.getVideoData ? p.getVideoData() || {} : {};
  return JSON.stringify({
    ready: !!(p && cfg), loggedIn: !!(cfg && cfg.get('LOGGED_IN')),
    title: ad ? vd.title || '' : m ? m.title : '', artist: ad ? vd.author || '' : m ? m.artist : '',
    album: ad ? '' : m ? m.album : '', art: ad ? '' : art,
    playing: p && p.getPlayerState ? [1, 3].includes(p.getPlayerState()) || !!(v && !v.paused && !v.ended) : !!(v && !v.paused && !v.ended),
    time: p && p.getCurrentTime ? p.getCurrentTime() : 0, duration: p && p.getDuration ? p.getDuration() : 0,
    ad,
    volume: p && p.getVolume ? p.getVolume() : null, muted: p && p.isMuted ? p.isMuted() : false,
    liked: like ? like.getAttribute('like-status') === 'LIKE' : null,
    videoId: p && p.getVideoData ? (p.getVideoData() || {}).video_id || '' : '',
  });
})()"""

# chamada à API interna do próprio site (a mesma que a página usa), com a sessão da pessoa
INNERTUBE_JS = r"""(() => {
  window.__gd = window.__gd || {};
  const id = %(id)s, endpoint = %(endpoint)s, body = %(body)s;
  (async () => {
    const cookie = (name) => (document.cookie.split('; ').find((c) => c.startsWith(name + '=')) || '').slice(name.length + 1);
    const sapisid = cookie('SAPISID') || cookie('__Secure-3PAPISID');
    const headers = { 'Content-Type': 'application/json', 'X-Origin': location.origin,
                      'X-Goog-AuthUser': String(ytcfg.get('SESSION_INDEX') || 0) };
    if (sapisid) {
      const ts = Math.floor(Date.now() / 1000);
      const digest = await crypto.subtle.digest('SHA-1', new TextEncoder().encode(`${ts} ${sapisid} ${location.origin}`));
      const hex = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('');
      headers.Authorization = `SAPISIDHASH ${ts}_${hex}`;
    }
    const key = ytcfg.get('INNERTUBE_API_KEY');
    const resp = await fetch(`/youtubei/v1/${endpoint}?prettyPrint=false${key ? '&key=' + key : ''}`, {
      method: 'POST', credentials: 'include', headers,
      body: JSON.stringify(Object.assign({ context: ytcfg.get('INNERTUBE_CONTEXT') }, body)) });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    return resp.json();
  })().then((data) => { window.__gd[id] = { ok: true, data }; },
           (err) => { window.__gd[id] = { ok: false, error: String(err && err.message || err) }; });
  return 'started';
})()"""

TAKE_JS = r"""(() => { const r = (window.__gd || {})[%(id)s]; if (!r) return null; delete window.__gd[%(id)s];
  return JSON.stringify(r); })()"""

CONTROL_JS = {
    "toggle": "(() => { const b = document.querySelector('ytmusic-player-bar #play-pause-button');"
              " if (b) { b.click(); return true; } const p = document.getElementById('movie_player');"
              " if (!p) return false; p.getPlayerState() === 1 ? p.pauseVideo() : p.playVideo(); return true; })()",
    "next": "(() => { const b = document.querySelector('ytmusic-player-bar .next-button'); if (b) b.click();"
            " else document.getElementById('movie_player')?.nextVideo(); return true; })()",
    "prev": "(() => { const b = document.querySelector('ytmusic-player-bar .previous-button'); if (b) b.click();"
            " else document.getElementById('movie_player')?.previousVideo(); return true; })()",
    "like": "(() => { const b = document.querySelector('ytmusic-player-bar ytmusic-like-button-renderer #button-shape-like button')"
            " || document.querySelector('ytmusic-player-bar ytmusic-like-button-renderer .like'); if (b) b.click(); return !!b; })()",
    "mute": "(() => { const p = document.getElementById('movie_player'); if (!p) return false;"
            " p.isMuted() ? p.unMute() : p.mute(); return true; })()",
}


# ----------------------------------------------------------------- leitura das respostas do site

def _text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if "simpleText" in node:
        return node["simpleText"]
    return "".join(r.get("text", "") for r in node.get("runs", []))


def _thumb(node) -> str:
    thumbs = []
    def walk(n):
        if isinstance(n, dict):
            if "thumbnails" in n and isinstance(n["thumbnails"], list):
                thumbs.extend(n["thumbnails"])
                return
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)
    walk(node)
    if not thumbs:
        return ""
    best = max(thumbs, key=lambda t: t.get("width", 0))
    return best.get("url", "")


def _find(node, key: str, out: list) -> list:
    """Todos os objetos `key` em qualquer profundidade (as respostas mudam de forma com frequência)."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                out.append(v)
            else:
                _find(v, key, out)
    elif isinstance(node, list):
        for v in node:
            _find(v, key, out)
    return out


def _endpoint_ids(node) -> tuple[str, str]:
    """(playlistId, videoId) do primeiro "tocar" encontrado no item."""
    for ep in _find(node, "watchPlaylistEndpoint", []):
        if ep.get("playlistId"):
            return ep["playlistId"], ""
    for ep in _find(node, "watchEndpoint", []):
        if ep.get("playlistId") or ep.get("videoId"):
            return ep.get("playlistId", ""), ep.get("videoId", "")
    for ep in _find(node, "browseEndpoint", []):
        bid = ep.get("browseId", "")
        if bid.startswith("VL"):
            return bid[2:], ""
    return "", ""


def parse_cards(data: dict) -> list[dict]:
    """Cartões (playlists, álbuns, mixes) de uma página de navegação."""
    items, seen = [], set()
    for card in _find(data, "musicTwoRowItemRenderer", []):
        playlist, video = _endpoint_ids(card)
        if not (playlist or video) or (playlist, video) in seen:
            continue
        seen.add((playlist, video))
        items.append({"title": _text(card.get("title")), "subtitle": _text(card.get("subtitle")),
                      "image": _thumb(card.get("thumbnailRenderer")), "playlist": playlist, "video": video})
    return items


def parse_shelves(data: dict) -> list[dict]:
    """Prateleiras da página inicial ("Mixes para você", "Ouvir de novo"...), cada uma com seus cartões."""
    shelves = []
    for shelf in _find(data, "musicCarouselShelfRenderer", []):
        header = _find(shelf.get("header", {}), "title", [])
        cards = parse_cards({"contents": shelf.get("contents", [])})
        rows = parse_rows({"contents": shelf.get("contents", [])}) if not cards else []
        if cards or rows:
            shelves.append({"title": _text(header[0]) if header else "", "items": cards or rows})
    return shelves


def parse_rows(data: dict) -> list[dict]:
    """Linhas de música (resultado de busca, "músicas rápidas")."""
    rows, seen = [], set()
    for row in _find(data, "musicResponsiveListItemRenderer", []):
        playlist, video = _endpoint_ids(row.get("overlay") or row)
        if not video:
            video = (row.get("playlistItemData") or {}).get("videoId", "")
        if not (playlist or video) or (playlist, video) in seen:
            continue
        seen.add((playlist, video))
        cols = [_text(c.get("musicResponsiveListItemFlexColumnRenderer", {}).get("text")) for c in row.get("flexColumns", [])]
        rows.append({"title": cols[0] if cols else "", "subtitle": " · ".join(c for c in cols[1:] if c),
                     "image": _thumb(row.get("thumbnail")), "playlist": playlist, "video": video})
    return rows


# ----------------------------------------------------------------- a janela

class MusicPlayer:
    def __init__(self, on_change: Callable[[dict], None] | None = None):
        self.window = None
        self.on_change = on_change or (lambda s: None)
        self.lock = threading.Lock()
        self.ready = threading.Event()
        self.visible = False
        self.closing_for_real = False
        self.last_track = ""
        self._watching = False
        self.cached: dict | None = None
        self.parked = False          # janela criada fora da tela (ainda não foi mostrada)
        self._hidden_once = False

    # --- ciclo de vida
    def is_open(self) -> bool:
        return self.window is not None

    def open(self, show: bool = True, url: str = HOME) -> None:
        with self.lock:
            if self.window is None:
                self.ready.clear()
                self.closing_for_real = False
                # nunca nasce com hidden=True: uma janela criada escondida às vezes nem inicia o WebView2. Sem mostrar,
                # ela nasce fora da tela e se esconde assim que a página carrega (a música toca escondida).
                where = {} if show else {"x": OFFSCREEN, "y": OFFSCREEN, "focus": False}
                self.window = webview.create_window(TITLE, url=url, width=WIDTH, height=HEIGHT, min_size=(720, 520),
                                                    background_color="#030303", **where)
                self.window.events.loaded += self._loaded
                self.window.events.closing += self._closing
                self.window.events.closed += self._closed
                self.visible = show
                self.parked = not show
                self._start_watch()
                return
        if url != HOME:
            self.navigate(url)
        if show:
            self.show()

    def show(self) -> None:
        if self.window is None:
            self.open(show=True)
            return
        self.visible = True
        self.parked = False
        self.window.show()
        self.window.restore()
        self._bring_on_screen()

    def _bring_on_screen(self) -> None:
        """A janela nasce fora da tela (-32000). O move() do pywebview numa janela escondida não vale e o restore()
        a devolvia para lá; então, depois de mostrar, ela é posta no meio do monitor do mouse direto pelo Windows."""
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        try:
            hwnd = int(self.window.native.Handle.ToInt64())
        except Exception:
            return
        if user32.MonitorFromWindow(hwnd, 0):   # 0 = MONITOR_DEFAULTTONULL: já está em algum monitor
            user32.SetForegroundWindow(hwnd)
            return

        class MONITORINFO(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                        ("dwFlags", wintypes.DWORD)]
        pt = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        monitor = user32.MonitorFromPoint(pt, 2)   # 2 = MONITOR_DEFAULTTONEAREST
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(monitor, ctypes.byref(info))
        work = info.rcWork
        scale = (user32.GetDpiForWindow(hwnd) or 96) / 96
        w = min(int(WIDTH * scale), work.right - work.left)
        h = min(int(HEIGHT * scale), work.bottom - work.top)
        x = work.left + (work.right - work.left - w) // 2
        y = work.top + (work.bottom - work.top - h) // 2
        user32.SetWindowPos(hwnd, 0, x, y, w, h, 0x0004 | 0x0040)   # SWP_NOZORDER | SWP_SHOWWINDOW
        user32.SetForegroundWindow(hwnd)

    def hide(self) -> None:
        if self.window is not None:
            self.window.hide()
            self.visible = False

    def close(self) -> None:
        """Para a música e libera a memória."""
        w = self.window
        if w is not None:
            self.closing_for_real = True
            try:
                w.destroy()
            except Exception:
                pass
        self._closed()

    def _loaded(self) -> None:
        self.ready.set()
        if self.parked and not self.visible and not self._hidden_once:
            self._hidden_once = True
            threading.Thread(target=self.hide, daemon=True).start()

    def _closing(self):
        if self.closing_for_real:
            return True
        threading.Thread(target=self.hide, daemon=True).start()  # o X esconde: a música continua
        return False

    def _closed(self) -> None:
        self.window = None
        self._hidden_once = False
        self.visible = False
        self.ready.clear()
        self.last_track = ""
        self.on_change({"open": False})

    # --- scripts
    def _run(self, script: str, timeout: float = 6):
        """Roda o script direto no WebView2 (sem o eval do pywebview, que o Trusted Types do site bloqueia).
        Numa thread com limite de tempo: se a página estiver no meio de uma navegação, a resposta pode nunca vir."""
        w = self.window
        if w is None:
            return None
        box = {}

        def work():
            try:
                box["r"] = w.gui.evaluate_js(script, w.uid, True)
            except Exception:
                box["r"] = None
        t = threading.Thread(target=work, daemon=True, name="ytmusic-js")
        t.start()
        t.join(timeout)
        return box.get("r")

    def _call(self, endpoint: str, body: dict, timeout: float = 12) -> dict:
        if not self.ready.wait(10):
            raise RuntimeError("o YouTube Music ainda está carregando")
        rid = uuid.uuid4().hex
        started = self._run(INNERTUBE_JS % {"id": json.dumps(rid), "endpoint": json.dumps(endpoint),
                                            "body": json.dumps(body)})
        if started != "started":
            raise RuntimeError("o YouTube Music ainda está carregando")
        end = time.time() + timeout
        while time.time() < end:
            res = self._run(TAKE_JS % {"id": json.dumps(rid)})
            if isinstance(res, str):
                res = json.loads(res)
            if res:
                if not res.get("ok"):
                    raise RuntimeError(res.get("error") or "o YouTube Music recusou")
                return res.get("data") or {}
            time.sleep(0.15)
        raise RuntimeError("o YouTube Music não respondeu")

    # --- leitura
    def state(self) -> dict:
        if self.window is None:
            return {"open": False}
        raw = self._run(STATE_JS)
        try:
            st = json.loads(raw) if isinstance(raw, str) else (raw or {})
        except ValueError:
            st = {}
        st["open"] = True
        st["visible"] = self.visible
        return st

    def library(self) -> dict:
        """Suas playlists (inclui "Músicas curtidas") e as prateleiras da página inicial."""
        st = self.state()
        if not st.get("loggedIn"):
            return {"loggedIn": False, "playlists": [], "shelves": []}
        playlists = parse_cards(self._call("browse", {"browseId": "FEmusic_liked_playlists"}))
        if not any(p["playlist"] == "LM" for p in playlists):
            playlists.insert(0, {"title": "Músicas curtidas", "subtitle": "Playlist automática", "image": "",
                                 "playlist": "LM", "video": ""})
        try:
            shelves = parse_shelves(self._call("browse", {"browseId": "FEmusic_home"}))[:6]
        except RuntimeError:
            shelves = []
        self.cached = {"at": time.time(), "playlists": playlists}
        return {"loggedIn": True, "playlists": playlists, "shelves": shelves}

    def playlists_cached(self) -> list[dict]:
        """As playlists para o Alt+G: da memória (até 15 min), sem a página inicial."""
        if self.cached and time.time() - self.cached["at"] < 900:
            return self.cached["playlists"]
        if not self.state().get("loggedIn"):
            return []
        playlists = parse_cards(self._call("browse", {"browseId": "FEmusic_liked_playlists"}))
        self.cached = {"at": time.time(), "playlists": playlists}
        return playlists

    def search(self, query: str) -> list[dict]:
        data = self._call("search", {"query": query})
        rows = parse_rows(data)
        cards = parse_cards(data)
        return (rows + cards)[:40]

    # --- ações
    def navigate(self, url: str) -> None:
        if self.window is None:
            self.open(show=False, url=url)
            return
        self.ready.clear()
        self.window.load_url(url)

    def play(self, playlist: str = "", video: str = "") -> None:
        if video:
            url = f"{HOME}watch?v={quote(video)}" + (f"&list={quote(playlist)}" if playlist else "")
        elif playlist:
            url = f"{HOME}watch?list={quote(playlist)}"
        else:
            return
        self.navigate(url)

    def control(self, action: str, value=None) -> dict:
        if action == "volume" and value is not None:
            self._run(f"document.getElementById('movie_player')?.setVolume({max(0, min(100, int(value)))})")
        elif action == "seek" and value is not None:
            self._run(f"(() => {{ const v = document.querySelector('video'); if (v) v.currentTime = {float(value)}; }})()")
        elif action in CONTROL_JS:
            self._run(CONTROL_JS[action])
        time.sleep(0.25)
        return self.state()

    # --- aviso de música nova (o painel e o aviso por cima do jogo)
    def _start_watch(self) -> None:
        if self._watching:
            return
        self._watching = True
        threading.Thread(target=self._watch, daemon=True, name="ytmusic-watch").start()

    def _watch(self) -> None:
        try:
            while self.window is not None:
                time.sleep(2)
                if not self.ready.is_set():
                    continue
                st = self.state()
                track = f"{st.get('title')}|{st.get('artist')}"
                if st.get("title") and not st.get("ad") and track != self.last_track:
                    first = not self.last_track
                    self.last_track = track
                    self.on_change(dict(st, changed=not first))
        finally:
            self._watching = False

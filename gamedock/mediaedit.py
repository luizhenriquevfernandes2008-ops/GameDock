"""Edição de capturas e clipes, e envio para o Discord.

- Cortar clipe: o ffmpeg recodifica só o trecho escolhido (corte preciso, não só em quadro-chave), com o
  mesmo codificador da placa de vídeo que a gravação usa.
- Vertical (9:16) para TikTok/Shorts/Reels: recorta o centro do vídeo e redimensiona para 1080×1920.
- Captura editada: a interface recorta no canvas e manda a imagem; vira uma captura nova (a original fica).
- Discord: um "webhook" de canal (Configurações do canal → Integrações → Webhooks) recebe a imagem ou o vídeo.
"""
from __future__ import annotations

import base64
import json
import time
import urllib.request
import uuid
from pathlib import Path

from . import captures, clips, recorder
from .storage import data_dir, load_config

DISCORD_LIMIT = 10 * 1024 * 1024   # limite de arquivo de um webhook sem Nitro no servidor


def _capture(capture_id: str) -> dict | None:
    return next((c for c in captures.all_captures() if c["id"] == capture_id), None)


def capture_file(item: dict) -> Path | None:
    if item["kind"] == "clip":
        return clips.clip_file(item["path"])
    path = data_dir() / "art" / item["path"]
    return path if path.is_file() else None


def trim_clip(capture_id: str, start: float, end: float, vertical: bool = False) -> dict:
    item = _capture(capture_id)
    src = capture_file(item) if item else None
    if not src or item["kind"] != "clip":
        return {"ok": False, "error": "Clipe não encontrado."}
    if not recorder.ffmpeg_ready():
        return {"ok": False, "error": "O componente de gravação (ffmpeg) não está instalado."}
    start, end = max(0.0, float(start)), float(end)
    if end - start < 0.5:
        return {"ok": False, "error": "O trecho precisa ter pelo menos meio segundo."}
    dest = src.with_name(f"{src.stem}-{'vertical' if vertical else 'corte'}-{int(time.time())}.mp4")
    codec, opts = recorder.pick_encoder()
    video_filter = ["-vf", "crop=ih*9/16:ih,scale=1080:1920:flags=lanczos"] if vertical else []
    result = recorder._ffmpeg(["-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src), *video_filter,
                               "-c:v", codec, *opts, "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart",
                               "-y", str(dest)], timeout=600)
    if result.returncode != 0 or not dest.is_file():
        return {"ok": False, "error": (result.stderr or "o ffmpeg falhou").strip()[-300:]}
    rel = dest.relative_to(recorder.clips_dir()).as_posix()
    new = captures.add(item["game"], clips.CLIP_PREFIX + rel, "clip")
    return {"ok": True, "id": new["id"], "path": str(dest)}


def save_edited_image(capture_id: str, data_url: str) -> dict:
    item = _capture(capture_id)
    src = capture_file(item) if item else None
    if not src or item["kind"] == "clip":
        return {"ok": False, "error": "Captura não encontrada."}
    header, _, payload = data_url.partition(",")
    ext = ".png" if "png" in header else ".jpg"
    dest = src.with_name(f"{src.stem}-editada-{int(time.time())}{ext}")
    dest.write_bytes(base64.b64decode(payload))
    new = captures.add(item["game"], dest.relative_to(data_dir() / "art").as_posix(), "manual")
    return {"ok": True, "id": new["id"]}


def share_discord(capture_id: str, text: str = "") -> dict:
    webhook = (load_config().get("discord_webhook") or "").strip()
    if not webhook.startswith(("https://discord.com/api/webhooks/", "https://discordapp.com/api/webhooks/")):
        return {"ok": False, "error": "Cole o link do webhook do canal em Configurações → Discord."}
    item = _capture(capture_id)
    path = capture_file(item) if item else None
    if not path:
        return {"ok": False, "error": "Arquivo não encontrado."}
    if path.stat().st_size > DISCORD_LIMIT:
        return {"ok": False, "error": "O arquivo passa de 10 MB, o limite do Discord. Corte o clipe antes."}
    boundary = uuid.uuid4().hex
    mime = "video/mp4" if path.suffix == ".mp4" else "image/png" if path.suffix == ".png" else "image/jpeg"
    payload = json.dumps({"content": text[:1900], "username": "GameDock"})
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
            f"Content-Type: application/json\r\n\r\n{payload}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"files[0]\"; filename=\"{path.name}\"\r\n"
            f"Content-Type: {mime}\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(webhook, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": "GameDock"})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            resp.read()
    except Exception as exc:
        return {"ok": False, "error": f"O Discord recusou ({exc})."}
    return {"ok": True}


def theme_music(game) -> Path | None:
    """Música de tema do jogo: PS3 traz o SND0.AT3 (a música do XMB) no próprio disco; vira .m4a no cache."""
    from .art import art_dir
    from .iso9660 import IsoImage
    if game.source != "ps3" or not game.install_dir:
        return None
    out = art_dir(game) / "theme.m4a"
    if out.is_file():
        return out
    if not recorder.ffmpeg_ready():
        return None
    raw = art_dir(game) / "snd0.at3"
    if not raw.is_file():
        base = Path(game.install_dir)
        try:
            if base.suffix.lower() == ".iso":
                with IsoImage(base) as img:
                    data = img.read("PS3_GAME/SND0.AT3")
            else:
                f = next((p for p in (base / "PS3_GAME" / "SND0.AT3", base / "SND0.AT3") if p.is_file()), None)
                data = f.read_bytes() if f else None
        except (OSError, ValueError):
            data = None
        if not data:
            return None
        raw.write_bytes(data)
    result = recorder._ffmpeg(["-i", str(raw), "-c:a", "aac", "-b:a", "160k", "-y", str(out)], timeout=60)
    return out if result.returncode == 0 and out.is_file() else None

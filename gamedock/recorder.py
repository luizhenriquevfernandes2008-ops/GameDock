"""Clipes "replay buffer" (como OBS, ShadowPlay e Steam): grava continuamente enquanto um jogo roda e,
ao apertar a tecla, salva os últimos segundos em .mp4.

- Vídeo: ffmpeg com ddagrab (Desktop Duplication do Windows) codificado na placa de vídeo (NVENC/AMF/QSV),
  em pedaços de 2 s num anel. Pesa pouco no jogo e não ocupa disco além dos ~2 min do anel.
- Som do PC: loopback do WASAPI (biblioteca soundcard), guardado na memória.
- Salvar: junta os pedaços do intervalo + o áudio do mesmo intervalo, sem recodificar o vídeo.
O ffmpeg é baixado na primeira vez que a gravação é ligada (build "essentials" do gyan.dev, com SHA-256 conferido).
"""
from __future__ import annotations

import collections
import hashlib
import os
import subprocess
import threading
import time
import urllib.request
import wave
import zipfile
from datetime import datetime
from pathlib import Path

from .launcher import clean_dll_search

FFMPEG_URL = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
TOOLS_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GameDock" / "tools"
FFMPEG = TOOLS_DIR / "ffmpeg.exe"
BUFFER_DIR = TOOLS_DIR / "replay-buffer"
SEGMENT = 2                    # segundos por pedaço (= intervalo entre quadros-chave)
CREATE_NO_WINDOW = 0x08000000
SAMPLE_RATE = 48000
# codificadores por ordem de preferência: placa de vídeo primeiro, CPU só se nada mais existir
ENCODERS = (
    ("h264_nvenc", ["-preset", "p4", "-tune", "ll", "-rc", "vbr", "-cq", "23"]),
    ("h264_amf", ["-quality", "speed", "-rc", "cqp", "-qp_i", "22", "-qp_p", "24"]),
    ("h264_qsv", ["-preset", "veryfast", "-global_quality", "23"]),
    ("libx264", ["-preset", "ultrafast", "-crf", "23"]),
)


def clips_dir() -> Path:
    folder = Path.home() / "Videos" / "GameDock"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# ---------------------------------------------------------------- ffmpeg

def ffmpeg_ready() -> bool:
    return FFMPEG.is_file()


def install_ffmpeg(progress=lambda done, total: None) -> None:
    """Baixa o ffmpeg oficial, confere o SHA-256 publicado e extrai só o ffmpeg.exe."""
    TOOLS_DIR.mkdir(parents=True, exist_ok=True)
    download = TOOLS_DIR / "download"
    download.mkdir(exist_ok=True)
    zip_path = download / "ffmpeg.zip"
    req = urllib.request.Request(FFMPEG_URL, headers={"User-Agent": "GameDock"})
    with urllib.request.urlopen(req, timeout=60) as resp, zip_path.open("wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            progress(done, total)
    with urllib.request.urlopen(urllib.request.Request(FFMPEG_URL + ".sha256", headers={"User-Agent": "GameDock"}),
                                timeout=30) as resp:
        expected = resp.read().decode().split()[0].lower()
    actual = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    if actual != expected:
        zip_path.unlink(missing_ok=True)
        raise RuntimeError("O download do ffmpeg veio corrompido (SHA-256 não confere). Tente de novo.")
    with zipfile.ZipFile(zip_path) as zf:
        member = next(n for n in zf.namelist() if n.endswith("/bin/ffmpeg.exe"))
        FFMPEG.write_bytes(zf.read(member))
    zip_path.unlink()
    download.rmdir()


def _ffmpeg(args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    with clean_dll_search():
        return subprocess.run([str(FFMPEG), "-hide_banner", "-loglevel", "error", *args], capture_output=True,
                          text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)


def pick_encoder() -> tuple[str, list[str]]:
    """O primeiro codificador que realmente funciona nesta máquina (teste de 1 quadro)."""
    for name, opts in ENCODERS:
        test = _ffmpeg(["-f", "lavfi", "-i", "color=black:s=256x144:d=0.1", "-c:v", name, *opts, "-f", "null", "-"], 30)
        if test.returncode == 0:
            return name, opts
    return ENCODERS[-1]


# ---------------------------------------------------------------- áudio

class AudioBuffer(threading.Thread):
    """Grava o som do PC (o que sai nos alto-falantes) continuamente, guardando só os últimos segundos."""

    def __init__(self, seconds: int):
        super().__init__(daemon=True, name="audio-buffer")
        self.keep = seconds + 10
        self.chunks: collections.deque = collections.deque()   # (horário do fim do pedaço, amostras int16)
        self.lock = threading.Lock()
        self.halt = threading.Event()
        self.error = None

    def run(self) -> None:
        try:
            import numpy as np
            import soundcard as sc
            speaker = sc.default_speaker()
            mic = sc.get_microphone(id=str(speaker.name), include_loopback=True)
            with mic.recorder(samplerate=SAMPLE_RATE, channels=2) as rec:
                while not self.halt.is_set():
                    data = rec.record(numframes=SAMPLE_RATE // 10)   # 100 ms
                    pcm = (np.clip(data, -1, 1) * 32767).astype(np.int16)
                    now = time.time()
                    with self.lock:
                        self.chunks.append((now, pcm))
                        while self.chunks and now - self.chunks[0][0] > self.keep:
                            self.chunks.popleft()
        except Exception as exc:  # sem som (ex: nenhum dispositivo de saída) o clipe sai mudo
            self.error = str(exc)

    def write_wav(self, start: float, end: float, dest: Path) -> bool:
        import numpy as np
        with self.lock:
            parts = [pcm for t, pcm in self.chunks if start <= t - len(pcm) / SAMPLE_RATE and t <= end + 0.1]
        if not parts:
            return False
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(SAMPLE_RATE)
            w.writeframes(np.concatenate(parts).tobytes())
        return True

    def stop(self) -> None:
        self.halt.set()


# ---------------------------------------------------------------- replay buffer

class ReplayBuffer:
    def __init__(self, seconds: int = 30, fps: int = 60, audio: bool = True, monitor: int = 0):
        self.seconds, self.fps, self.with_audio, self.monitor = seconds, fps, audio, monitor
        self.proc: subprocess.Popen | None = None
        self.audio: AudioBuffer | None = None
        self.started_at = 0.0
        self.encoder = None
        self.lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> None:
        with self.lock:
            if self.running or not ffmpeg_ready():
                return
            BUFFER_DIR.mkdir(parents=True, exist_ok=True)
            for old in BUFFER_DIR.glob("*.ts"):
                old.unlink(missing_ok=True)
            if self.encoder is None:
                self.encoder = pick_encoder()
            name, opts = self.encoder
            wrap = self.seconds // SEGMENT + 4   # anel um pouco maior que o clipe
            gpu = name != "libx264"
            # com placa de vídeo, os quadros vão direto do ddagrab (D3D11) para o codificador, sem passar pela CPU
            source = f"ddagrab=output_idx={self.monitor}:framerate={self.fps}" + ("" if gpu else ",hwdownload,format=bgra")
            args = [str(FFMPEG), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", source,
                    "-c:v", name, *opts, "-g", str(self.fps * SEGMENT), "-pix_fmt", "yuv420p" if not gpu else "nv12"]
            if gpu:
                args = args[:-2]  # o NVENC/AMF/QSV escolhem o formato de pixel a partir do D3D11
            args += ["-f", "segment", "-segment_time", str(SEGMENT), "-segment_wrap", str(wrap),
                     "-reset_timestamps", "1", "-segment_format", "mpegts", str(BUFFER_DIR / "seg%03d.ts")]
            with clean_dll_search():
                self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW)
            self.started_at = time.time()
            if self.with_audio:
                self.audio = AudioBuffer(self.seconds)
                self.audio.start()

    def stop(self) -> None:
        with self.lock:
            if self.audio:
                self.audio.stop()
                self.audio = None
            if self.proc and self.proc.poll() is None:
                try:
                    self.proc.stdin.write(b"q")  # "q" encerra o ffmpeg fechando o pedaço atual direito
                    self.proc.stdin.flush()
                    self.proc.wait(timeout=5)
                except (OSError, subprocess.TimeoutExpired):
                    self.proc.kill()
            self.proc = None

    def error(self) -> str | None:
        if self.proc and self.proc.poll() is not None:
            return (self.proc.stderr.read() or b"").decode(errors="replace").strip()[-300:] or "o gravador parou"
        return None

    def save(self, game_name: str | None) -> Path:
        """Salva os últimos `seconds` segundos em Vídeos\\GameDock\\<jogo>\\<jogo> <data>.mp4."""
        if not self.running:
            raise RuntimeError(self.error() or "A gravação não está ligada.")
        now = time.time()
        # pedaços já fechados (o que está sendo escrito agora fica de fora) do mais antigo ao mais novo
        segments = sorted(BUFFER_DIR.glob("seg*.ts"), key=lambda p: p.stat().st_mtime)
        closed = [s for s in segments if now - s.stat().st_mtime > 0.3 and s.stat().st_size > 0]
        wanted = closed[-(self.seconds // SEGMENT):]
        if not wanted:
            raise RuntimeError("Ainda não há o que salvar: espere alguns segundos de jogo.")
        work = BUFFER_DIR / "save"
        work.mkdir(exist_ok=True)
        listing = work / "list.txt"
        listing.write_text("".join(f"file '{s.as_posix()}'\n" for s in wanted), encoding="utf-8")
        clip_start = wanted[0].stat().st_mtime - SEGMENT
        clip_end = wanted[-1].stat().st_mtime

        safe = "".join(c for c in (game_name or "Área de trabalho") if c not in '\\/:*?"<>|').strip()
        folder = clips_dir() / safe
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / f"{safe} {datetime.now():%Y-%m-%d %H-%M-%S}.mp4"

        inputs = ["-f", "concat", "-safe", "0", "-i", str(listing)]
        maps = ["-map", "0:v"]
        wav = work / "audio.wav"
        if self.audio and self.audio.write_wav(clip_start, clip_end, wav):
            inputs += ["-i", str(wav)]
            maps += ["-map", "1:a", "-c:a", "aac", "-b:a", "160k", "-shortest"]
        result = _ffmpeg([*inputs, *maps, "-c:v", "copy", "-movflags", "+faststart", "-y", str(dest)], timeout=120)
        if result.returncode != 0 or not dest.is_file():
            raise RuntimeError(f"Não foi possível salvar o clipe: {result.stderr.strip()[-200:]}")
        return dest

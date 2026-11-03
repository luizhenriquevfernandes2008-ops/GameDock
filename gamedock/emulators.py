"""Emuladores integrados: o GameDock baixa, configura e abre os jogos direto ("standalone").

Cada console é uma subclasse de Emulator que sabe: baixar o emulador oficial, instalar o que mais
for preciso (ex: firmware), achar os jogos nas pastas do usuário e montar a linha de comando que abre o
jogo direto, sem a janela do emulador. Os jogos precisam ser cópias do próprio usuário (dumps dos seus
discos); o GameDock não baixa jogos.

Por enquanto: PS3 (RPCS3). PS2, PS1 e NES entram aqui com a mesma estrutura.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

from .iso9660 import IsoImage
from .launcher import clean_dll_search
from .models import Game
from .scanners.base import slug
from .storage import data_dir, load_config, save_config

EMU_ROOT = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "GameDock" / "emulators"
USER_AGENT = "GameDock/0.3 (personal Windows game launcher)"
CREATE_NO_WINDOW = 0x08000000


def _get(url: str, timeout: int = 30) -> bytes:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as r:
        return r.read()


def _download(url: str, dest: Path, progress) -> None:
    with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as r, \
            dest.open("wb") as out:
        total, done = int(r.headers.get("Content-Length") or 0), 0
        while chunk := r.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            progress(done, total)


# ---------------------------------------------------------------- PARAM.SFO (metadados dos jogos de PS3/PSP)

def read_sfo(path: Path) -> dict:
    return parse_sfo(Path(path).read_bytes())


def parse_sfo(data: bytes) -> dict:
    """Formato SFO: cabeçalho "\\0PSF", tabela de chaves e tabela de dados (texto UTF-8 ou inteiro)."""
    if data[:4] != b"\x00PSF":
        return {}
    key_table, data_table, count = struct.unpack_from("<III", data, 8)
    result = {}
    for i in range(count):
        key_off, fmt, length, _max, data_off = struct.unpack_from("<HHIII", data, 20 + i * 16)
        key = data[key_table + key_off:data.index(b"\0", key_table + key_off)].decode("utf-8", "replace")
        raw = data[data_table + data_off:data_table + data_off + length]
        result[key] = struct.unpack("<I", raw[:4])[0] if fmt == 0x0404 else raw.split(b"\0", 1)[0].decode("utf-8", "replace")
    return result


# ---------------------------------------------------------------- base

class Emulator:
    id = ""
    name = ""
    platform = ""

    def __init__(self):
        self.folder = EMU_ROOT / self.id
        self.task: dict = {"running": False}   # tarefa em andamento (download/instalação) para a interface acompanhar
        self.warnings: list[str] = []          # avisos do último scan (ex: ISO criptografado)
        self.last_controller: str | None = None

    # configurações do usuário deste emulador (em config.json → emulators.<id>)
    def settings(self) -> dict:
        return {"folders": [], "fullscreen": True, "auto_controller": True} | load_config().get("emulators", {}).get(self.id, {})

    def save_settings(self, changes: dict) -> dict:
        config = load_config()
        current = self.settings() | changes
        config.setdefault("emulators", {})[self.id] = current
        save_config(config)
        return current

    def run_task(self, label: str, work) -> None:
        if self.task.get("running"):
            return

        def wrapper():
            self.task = {"running": True, "label": label, "done": 0, "total": 0, "error": None}
            try:
                work(lambda d, t: self.task.update(done=d, total=t))
            except Exception as exc:
                self.task["error"] = str(exc)
            self.task["running"] = False

        threading.Thread(target=wrapper, daemon=True, name=f"emu-{self.id}").start()

    def status(self) -> dict:
        settings = self.settings()
        return {"id": self.id, "name": self.name, "platform": self.platform, "installed": self.installed(),
                "version": self.version(), "folders": settings["folders"], "fullscreen": settings["fullscreen"],
                "autoController": settings["auto_controller"], "controller": self.last_controller,
                "task": self.task, "warnings": self.warnings, "needsBios": self.needs_bios,
                "bios": self.bios_installed() if self.needs_bios else True, "savestates": bool(self.savestate_keys),
                "achievements": self.achievements_support, "enhancements": self.ENHANCEMENTS, **self.extra_status()}

    # para as subclasses
    def installed(self) -> bool: ...
    def extra_status(self) -> dict: return {}
    def scan(self) -> list[Game]: ...
    def configure(self) -> None: ...

    def configure_achievements(self, ra: dict | None) -> None:
        """RetroAchievements: ra = {"user", "token"} ou None (desliga)."""

    def save_paths(self, game: Game) -> list[Path]:
        return []

    # ------------------------------------------------------------ recursos comuns
    needs_bios = False
    bios_min = 0                 # tamanho mínimo de um arquivo de BIOS válido
    savestate_keys: dict | None = None   # {"save": VK, "load": VK}: teclas que o Alt+G manda para o emulador
    achievements_support = False         # RetroAchievements
    ENHANCEMENTS: list[dict] = []        # opções da seção "Melhorias" da página do jogo

    def version(self) -> str | None:
        info = self.folder / "gamedock-version.json"
        return json.loads(info.read_text(encoding="utf-8")).get("version") if info.is_file() else None

    @property
    def bios_dir(self) -> Path:
        return self.folder / "bios"

    def bios_installed(self) -> bool:
        return self.bios_dir.is_dir() and any(f.is_file() and f.stat().st_size >= self.bios_min
                                              for f in self.bios_dir.iterdir())

    def install_bios(self, source: Path) -> None:
        if source.stat().st_size < self.bios_min:
            raise RuntimeError(f"{source.name} não parece um BIOS de {self.platform} (arquivo pequeno demais).")
        self.bios_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, self.bios_dir / source.name)
        self.configure()

    def game_settings(self, game_id: str) -> dict:
        defaults = {e["key"]: e["default"] for e in self.ENHANCEMENTS}
        return defaults | self.settings().get("games", {}).get(game_id, {})

    def set_game_settings(self, game_id: str, changes: dict) -> dict:
        games = dict(self.settings().get("games", {}))
        allowed = {e["key"] for e in self.ENHANCEMENTS}
        games[game_id] = games.get(game_id, {}) | {k: v for k, v in changes.items() if k in allowed}
        self.save_settings({"games": games})
        return self.game_settings(game_id)

    def install_github(self, repo: str, pattern: str, tag: str | None = None, progress=None) -> str:
        """Baixa o arquivo da release que bate com `pattern`, confere o SHA-256 que o GitHub publica e extrai."""
        url = f"https://api.github.com/repos/{repo}/releases/" + (f"tags/{tag}" if tag else "latest")
        release = json.loads(_get(url))
        asset = next(a for a in release["assets"] if re.search(pattern, a["name"]))
        EMU_ROOT.mkdir(parents=True, exist_ok=True)
        archive = EMU_ROOT / asset["name"]
        _download(asset["browser_download_url"], archive, progress or (lambda d, t: None))
        digest = (asset.get("digest") or "").removeprefix("sha256:").lower()
        if digest and hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            archive.unlink(missing_ok=True)
            raise RuntimeError(f"O download do {self.name} veio corrompido (SHA-256 não confere).")
        self.task["label"] = "Extraindo…"
        extract(archive, self.folder)
        archive.unlink(missing_ok=True)
        version = release.get("tag_name") or asset["name"]
        if tag == "latest":   # release contínua: a data identifica a versão
            version = (release.get("published_at") or "")[:10] or version
        (self.folder / "gamedock-version.json").write_text(json.dumps({"version": version}), encoding="utf-8")
        return version


# ---------------------------------------------------------------- PS3: RPCS3

class RPCS3(Emulator):
    id = "ps3"
    name = "RPCS3"
    platform = "PlayStation 3"
    RELEASES = "https://api.github.com/repos/RPCS3/rpcs3-binaries-win/releases/latest"
    FIRMWARE_LIST = "http://fus01.ps3.update.playstation.net/update/ps3/list/us/ps3-updatelist.txt"

    @property
    def exe(self) -> Path:
        return self.folder / "rpcs3.exe"

    def installed(self) -> bool:
        return self.exe.is_file()

    ENHANCEMENTS = [{"key": "scale", "label": "Resolução", "default": 100,
                     "options": [[100, "Nativa (720p)"], [150, "1,5× (1080p)"], [200, "2× (1440p)"], [300, "3× (4K)"]]}]

    def firmware_installed(self) -> bool:
        # o firmware instalado cria os módulos do sistema em dev_flash
        return (self.folder / "dev_flash" / "sys" / "external").is_dir()

    def extra_status(self) -> dict:
        return {"firmware": self.firmware_installed(), "needsFirmware": True}

    # instalar / atualizar o emulador
    def install(self) -> None:
        def work(progress):
            release = json.loads(_get(self.RELEASES))
            asset = next(a for a in release["assets"] if a["name"].endswith("_win64_msvc.7z") or a["name"].endswith(".7z"))
            hash_asset = next((a for a in release["assets"] if a["name"] == asset["name"] + ".sha256"), None)
            EMU_ROOT.mkdir(parents=True, exist_ok=True)
            archive = EMU_ROOT / asset["name"]
            _download(asset["browser_download_url"], archive, progress)
            if hash_asset:
                expected = _get(hash_asset["browser_download_url"]).decode().split()[0].lower()
                if hashlib.sha256(archive.read_bytes()).hexdigest() != expected:
                    archive.unlink(missing_ok=True)
                    raise RuntimeError("O download do RPCS3 veio corrompido (SHA-256 não confere).")
            self.task["label"] = "Extraindo…"
            self.folder.mkdir(parents=True, exist_ok=True)
            # o .7z do RPCS3 usa o filtro BCJ2, que o py7zr não lê; o tar.exe do Windows (libarchive) lê.
            # Extrai por cima: mantém firmware, jogos instalados e saves.
            result = subprocess.run([str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"),
                                     "-xf", str(archive), "-C", str(self.folder)],
                                    capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=600)
            if result.returncode != 0:
                raise RuntimeError(f"Não foi possível extrair o RPCS3: {result.stderr.strip()[-200:]}")
            archive.unlink()
            version = asset["name"].split("_win64")[0].replace("rpcs3-", "")
            (self.folder / "gamedock-version.json").write_text(json.dumps({"version": version}), encoding="utf-8")
            self.configure()

        self.run_task("Baixando o RPCS3…", work)

    def configure(self) -> None:
        """Ajustes para abrir jogos direto: sem perguntas ao sair, sem tela de boas-vindas, sem checar atualização."""
        gui = self.folder / "GuiConfigs"
        gui.mkdir(exist_ok=True)
        ini = gui / "CurrentSettings.ini"
        text = ini.read_text(encoding="utf-8") if ini.is_file() else ""
        wanted = {"main_window": {"confirmationBoxExitGame": "false", "infoBoxEnabledWelcome": "false",
                                  "checkForUpdates": "false"},
                  "Meta": {"checkUpdateStart": "false"}}
        for section, values in wanted.items():
            for key, value in values.items():
                text = _ini_set(text, section, key, value)
        ini.write_text(text, encoding="utf-8")
        self.configure_input()

    def configure_input(self) -> str | None:
        """Configura o controle conectado agora como Jogador 1, para todos os jogos (perfil global)."""
        pad = detect_controller()
        if pad:
            self.save_settings({"last_pad": pad})
        else:
            # controle desligado agora: usa o último detectado (ele pode ser ligado com o jogo já aberto)
            pad = self.settings().get("last_pad")
            if not pad:
                self.last_controller = "Nenhum controle conectado"
                return None
            pad = pad | {"label": pad["label"] + " — ligue o controle antes de jogar"}
        folder = self.folder / "config" / "input_configs"
        (folder / "global").mkdir(parents=True, exist_ok=True)
        (folder / "global" / "Default.yml").write_text(rpcs3_pad_yaml(pad["handler"]), encoding="utf-8")
        (folder / "active_input_configurations.yml").write_text("Active Configurations:\n  global: Default\n",
                                                                 encoding="utf-8")
        self.last_controller = pad["label"]
        return pad["label"]

    def configure_video(self) -> None:
        """Tela cheia *sem borda* em vez da exclusiva: com a exclusiva o Windows não deixa nada aparecer por cima
        do jogo nem copiar a imagem dele — o aviso de troféu, o Alt+G, a captura e a gravação sairiam pretos."""
        path = self.folder / "config" / "config.yml"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        if re.search(r"^\s*Exclusive Fullscreen Mode:", text, re.M):
            text = re.sub(r"^(\s*Exclusive Fullscreen Mode:).*$", r"\1 Disable", text, flags=re.M)
        elif "Video:" not in text:   # o RPCS3 completa o resto do arquivo com os padrões
            text += "Video:\n  Vulkan:\n    Exclusive Fullscreen Mode: Disable\n"
        if text:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")

    # firmware oficial da Sony
    def install_firmware(self) -> None:
        def work(progress):
            listing = _get(self.FIRMWARE_LIST).decode("utf-8", "replace")
            url = next(part.split("=", 1)[1] for line in listing.splitlines() if "PS3UPDAT.PUP" in line
                       for part in line.split(";") if part.startswith("CDN="))
            pup = EMU_ROOT / "PS3UPDAT.PUP"
            if not (pup.is_file() and pup.stat().st_size > 100_000_000):  # já baixado numa tentativa anterior
                _download(url, pup, progress)
            self._close_stuck_installers()
            self.task.update(label="Instalando o firmware…", done=0, total=0)
            with clean_dll_search():
                proc = subprocess.Popen([str(self.exe), "--installfw", str(pup)], cwd=self.folder)
            # O RPCS3 instala e *continua aberto* (mostra "instalado com sucesso"); esperar ele fechar travava
            # em "100%". Então acompanhamos os arquivos em dev_flash e fechamos o RPCS3 quando param de mudar.
            last_count, stable_since, started = -1, time.time(), time.time()
            while time.time() - started < 900:
                time.sleep(1)
                count = sum(1 for _ in (self.folder / "dev_flash").rglob("*") if _.is_file())
                self.task["label"] = f"Instalando o firmware… {count} arquivos"
                if count != last_count:
                    last_count, stable_since = count, time.time()
                if self.firmware_installed() and count > 100 and time.time() - stable_since > 6:
                    break   # terminou de escrever
                if proc.poll() is not None and not self.firmware_installed():
                    raise RuntimeError("O RPCS3 fechou sem instalar o firmware. Abra o RPCS3 completo e veja a mensagem.")
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
            if not self.firmware_installed():
                raise RuntimeError("O firmware não terminou de instalar em 15 minutos.")
            pup.unlink(missing_ok=True)

        self.run_task("Baixando o firmware oficial da Sony…", work)

    def _close_stuck_installers(self) -> None:
        """Uma instalação de firmware anterior que ficou presa (ex: o erro de DLL de versões antigas) trava o
        dev_flash. Fecha só processos deste RPCS3 abertos com --installfw."""
        script = ("Get-CimInstance Win32_Process -Filter \"Name='rpcs3.exe'\" | Where-Object { $_.CommandLine -like "
                  f"'*--installfw*' -and $_.ExecutablePath -eq '{_ps_quote(self.exe)}' }} | "
                  "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }")
        _powershell(script)
        time.sleep(1)

    def save_paths(self, game: Game) -> list[Path]:
        title = game.id.split(":", 1)[1]
        home = self.folder / "dev_hdd0" / "home"
        return sorted(home.glob(f"*/savedata/{title}*")) if home.is_dir() else []

    def apply_game_settings(self, game: Game) -> None:
        """Config personalizado do jogo (o RPCS3 aplica por cima do global): resolução e FPS na tela."""
        title = game.id.split(":", 1)[1]
        path = self.folder / "config" / "custom_configs" / f"config_{title}.yml"
        text = path.read_text(encoding="utf-8") if path.is_file() else ""
        opts = self.game_settings(game.id)
        text = yaml_set(text, ["Video", "Resolution Scale"], str(int(opts["scale"])))
        text = yaml_set(text, ["Video", "Performance Overlay", "Enabled"], "true" if perf_overlay() else "false")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    # jogos
    def _game_dirs(self) -> list[Path]:
        """Jogos em formato de disco (pasta com PS3_GAME) nas pastas do usuário + jogos instalados no RPCS3."""
        found = []
        for root in [Path(f) for f in self.settings()["folders"]]:
            if not root.is_dir():
                continue
            candidates = [root] + [p for p in root.iterdir() if p.is_dir()]
            found += [c for c in candidates if (c / "PS3_GAME" / "PARAM.SFO").is_file()]
        hdd = self.folder / "dev_hdd0" / "game"
        if hdd.is_dir():
            found += [p for p in hdd.iterdir() if (p / "PARAM.SFO").is_file() and (p / "USRDIR" / "EBOOT.BIN").is_file()]
        return found

    def _isos(self) -> list[Path]:
        found = []
        for root in [Path(f) for f in self.settings()["folders"]]:
            if root.is_dir():
                found += [p for p in [*root.glob("*.iso"), *root.glob("*/*.iso")] if p.is_file()]
        return found

    def _iso_game(self, iso: Path) -> Game | None:
        """Jogo de PS3 em .iso: lê o PARAM.SFO de dentro da imagem (sem montar) e guarda ícone e fundo no cache."""
        try:
            with IsoImage(iso) as img:
                sfo_raw = img.read("PS3_GAME/PARAM.SFO")
                if not sfo_raw:
                    return None
                sfo = parse_sfo(sfo_raw)
                if sfo.get("CATEGORY") != "DG":
                    return None
                eboot = img.read("PS3_GAME/USRDIR/EBOOT.BIN", 4) or b""
                if not eboot.startswith(b"SCE"):
                    # dumps "Redump" vêm criptografados como no disco: o RPCS3 só roda a versão descriptografada
                    self.warnings.append(f"{iso.name} está criptografado. Descriptografe a imagem (ex: PS3Dec com a "
                                         "chave do disco) para jogar no RPCS3.")
                    return None
                game_id = f"ps3:{sfo.get('TITLE_ID') or slug(iso.stem)}"
                cache = data_dir() / "art" / slug(game_id)
                cache.mkdir(parents=True, exist_ok=True)
                for inner, name in (("PS3_GAME/ICON0.PNG", "ps3-icon0.png"), ("PS3_GAME/PIC1.PNG", "ps3-pic1.png")):
                    if not (cache / name).is_file() and (raw := img.read(inner)):
                        (cache / name).write_bytes(raw)
        except (OSError, ValueError, struct.error):
            return None
        title = " ".join(sfo.get("TITLE", iso.stem).split())
        if title.isupper():  # "ASURA'S WRATH" -> "Asura's Wrath" (o .title() do Python faria "Asura'S")
            title = " ".join(w[:1] + w[1:].lower() for w in title.split())
        return Game(
            id=game_id,
            name=title,
            source="ps3",
            launch_type="emu",          # o emulador monta o ISO antes de abrir (veja launch)
            launch_target=str(iso),
            install_dir=str(iso),       # único por jogo (vários ISOs podem estar na mesma pasta)
            icon=str(cache / "ps3-icon0.png") if (cache / "ps3-icon0.png").is_file() else None,
        )

    def scan(self) -> list[Game]:
        if not self.installed():
            return []
        self.warnings = []
        games = [g for iso in self._isos() if (g := self._iso_game(iso))]
        for folder in self._game_dirs():
            base = folder / "PS3_GAME" if (folder / "PS3_GAME").is_dir() else folder
            sfo = read_sfo(base / "PARAM.SFO")
            # só jogos (disco "DG" ou instalado "HG"); atualizações e DLCs ficam de fora
            if sfo.get("CATEGORY") not in ("DG", "HG") or not (base / "USRDIR" / "EBOOT.BIN").is_file():
                continue
            title_id = sfo.get("TITLE_ID") or slug(folder.name)
            games.append(Game(
                id=f"ps3:{title_id}",
                name=" ".join(sfo.get("TITLE", folder.name).split()),   # alguns títulos vêm com quebras de linha
                source="ps3",
                launch_type="emu",
                launch_target=str(base / "USRDIR" / "EBOOT.BIN"),
                install_dir=str(folder),
                icon=str(base / "ICON0.PNG") if (base / "ICON0.PNG").is_file() else None,
            ))
        return games


    def launch(self, game: Game) -> subprocess.Popen:
        target = Path(game.launch_target)
        if not target.is_file():
            raise RuntimeError(f"Jogo não encontrado: {target}")
        if self.settings()["auto_controller"]:
            self.configure_input()   # o controle conectado agora vira o Jogador 1
        self.configure_video()
        self.apply_game_settings(game)
        args = ["--no-gui"] + (["--fullscreen"] if self.settings()["fullscreen"] else [])
        if target.suffix.lower() != ".iso":   # jogo em pasta: abre o EBOOT.BIN direto
            return subprocess.Popen([str(self.exe), *args, str(target)], cwd=str(self.folder))
        iso = target
        drive = mount_iso(iso)
        proc = subprocess.Popen([str(self.exe), *args, f"{drive}:\\PS3_GAME\\USRDIR\\EBOOT.BIN"], cwd=str(self.folder))

        def cleanup():  # jogo fechou: desmonta a imagem
            proc.wait()
            dismount_iso(iso)

        threading.Thread(target=cleanup, daemon=True, name="ps3-iso").start()
        return proc


# ---------------------------------------------------------------- controle

REMAPPERS = {"ds4windows": "DS4Windows", "dsx": "DSX", "inputmapper": "InputMapper", "x360ce": "x360ce",
             "betterjoy": "BetterJoy"}
SONY_PIDS = {"0CE6": "dualsense", "0DF2": "dualsense", "05C4": "ds4", "09CC": "ds4", "0BA0": "ds4"}

# nomes dos botões em cada "handler" do RPCS3 (ordem dos botões do PS3)
PS3_BUTTONS = ("Cross", "Circle", "Square", "Triangle", "L1", "R1", "L2", "R2", "L3", "R3", "Start", "Select",
               "PS Button", "Up", "Down", "Left", "Right")
HANDLER_BUTTONS = {
    "XInput": ("A", "B", "X", "Y", "LB", "RB", "LT", "RT", "LS", "RS", "Start", "Back", "Guide",
               "Up", "Down", "Left", "Right"),
    "DualSense": ("Cross", "Circle", "Square", "Triangle", "L1", "R1", "L2", "R2", "L3", "R3", "Options", "Share",
                  "PS Button", "Up", "Down", "Left", "Right"),
    "DualShock 4": ("Cross", "Circle", "Square", "Triangle", "L1", "R1", "L2", "R2", "L3", "R3", "Options", "Share",
                    "PS Button", "Up", "Down", "Left", "Right"),
}
DEVICES = {"XInput": "XInput Pad #1", "DualSense": "DualSense Pad #1", "DualShock 4": "DS4 Pad #1"}


def _xinput_connected() -> bool:
    import ctypes

    class State(ctypes.Structure):
        _fields_ = [("packet", ctypes.c_ulong), ("buttons", ctypes.c_ushort), ("lt", ctypes.c_ubyte),
                    ("rt", ctypes.c_ubyte), ("lx", ctypes.c_short), ("ly", ctypes.c_short),
                    ("rx", ctypes.c_short), ("ry", ctypes.c_short)]
    try:
        xinput = ctypes.windll.xinput1_4
    except OSError:
        return False
    return any(xinput.XInputGetState(i, ctypes.byref(State())) == 0 for i in range(4))


def detect_controller() -> dict | None:
    """Qual controle está conectado e qual handler do RPCS3 usar.

    Com um conversor (ex: DS4Windows) rodando, o DualSense aparece duas vezes: o físico e um Xbox virtual.
    Usar os dois daria comandos em dobro, então nesse caso fica o XInput (o que o conversor entrega)."""
    from .processes import running_executables
    names = {Path(p).stem.lower() for p in running_executables().values()}
    remapper = next((r for r in REMAPPERS if r in names), None)
    xinput = _xinput_connected()
    devices = _powershell("Get-PnpDevice -PresentOnly -Class HIDClass,USB,Bluetooth -ErrorAction SilentlyContinue | "
                          "Where-Object { $_.InstanceId -match 'VID_054C' } | ForEach-Object { $_.InstanceId }")
    sony = next((kind for pid, kind in SONY_PIDS.items() if f"PID_{pid}" in devices.upper()), None)
    if remapper and xinput:
        label = {"dualsense": "DualSense", "ds4": "DualShock 4"}.get(sony, "Controle")
        return {"handler": "XInput", "label": f"{label} via {REMAPPERS[remapper]} (XInput)"}
    if sony == "dualsense":
        return {"handler": "DualSense", "label": "DualSense"}
    if sony == "ds4":
        return {"handler": "DualShock 4", "label": "DualShock 4"}
    if xinput:
        return {"handler": "XInput", "label": "Controle de Xbox (XInput)"}
    return None


def rpcs3_pad_yaml(handler: str) -> str:
    """Configuração do Jogador 1 no formato do RPCS3 (input_configs/global/Default.yml)."""
    # valores sempre entre aspas: em YAML, " #" começa um comentário, e "XInput Pad #1" virava "XInput Pad"
    # (o RPCS3 não achava o controle e caía no NullPadHandler)
    binds = dict(zip(PS3_BUTTONS, HANDLER_BUTTONS[handler]))
    lines = ["Player 1 Input:", f'  Handler: "{handler}"', f'  Device: "{DEVICES[handler]}"', "  Config:"]
    for side, prefix in (("Left", "LS"), ("Right", "RS")):
        lines += [f'    {side} Stick Left: "{prefix} X-"', f'    {side} Stick Down: "{prefix} Y-"',
                  f'    {side} Stick Right: "{prefix} X+"', f'    {side} Stick Up: "{prefix} Y+"']
    lines += [f'    {ps3}: "{pad}"' for ps3, pad in binds.items()]
    lines += ["    Enable Vibration Motor Large: true", "    Enable Vibration Motor Small: true"]
    for player in range(2, 8):   # os outros jogadores ficam desligados (sem teclado roubando o Jogador 1)
        lines += [f"Player {player} Input:", '  Handler: "Null"', '  Device: "Null"']
    return "\n".join(lines) + "\n"


def _powershell(script: str, timeout: int = 60) -> str:
    result = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script], capture_output=True,
                            text=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    return result.stdout.strip()


def _ps_quote(path: Path) -> str:
    return str(path).replace("'", "''")


def mount_iso(iso: Path) -> str:
    """Monta o ISO só leitura com o próprio Windows (como o "Montar" do Explorer) e devolve a letra da unidade."""
    p = _ps_quote(iso)
    letter = _powershell(f"$i=Get-DiskImage -ImagePath '{p}'; if(-not $i.Attached){{$i=Mount-DiskImage -ImagePath '{p}' "
                         f"-StorageType ISO -Access ReadOnly -PassThru}}; ($i|Get-Volume).DriveLetter")
    if len(letter) != 1:
        raise RuntimeError(f"O Windows não conseguiu montar {iso.name}.")
    return letter


def dismount_iso(iso: Path) -> None:
    _powershell(f"Dismount-DiskImage -ImagePath '{_ps_quote(iso)}' | Out-Null")


def launch_game(game: Game) -> subprocess.Popen:
    """Jogos com launch_type "emu": o emulador cuida de preparar (ex: montar o ISO) e abrir."""
    return EMULATORS[game.source].launch(game)


def _ini_set(text: str, section: str, key: str, value: str, sep: str = "=") -> str:
    """Define key=value numa seção de .ini preservando o resto do arquivo (sep=" = " para PCSX2/DuckStation)."""
    lines = text.splitlines()
    header = f"[{section}]"
    entry = f"{key}{sep}{value}"
    if header not in lines:
        return "\n".join(lines + ["", header, entry]) + "\n"
    start = lines.index(header) + 1
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
    for i in range(start, end):
        if lines[i].split("=", 1)[0].strip() == key:
            lines[i] = entry
            return "\n".join(lines) + "\n"
    while end > start and not lines[end - 1].strip():   # antes das linhas em branco do fim da seção
        end -= 1
    lines.insert(end, entry)
    return "\n".join(lines) + "\n"


def ini_update(path: Path, values: dict[str, dict[str, object]], sep: str = " = ") -> None:
    """Aplica {seção: {chave: valor}} num .ini (cria se não existir)."""
    text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
    for section, items in values.items():
        for key, value in items.items():
            if isinstance(value, bool):
                value = "true" if value else "false"
            text = _ini_set(text, section, key, str(value), sep)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def yaml_set(text: str, keys: list[str], value: str) -> str:
    """Define um valor num YAML simples de blocos com recuo de 2 espaços (o formato dos configs do RPCS3)."""
    lines = text.splitlines()
    start, end = 0, len(lines)
    for depth, key in enumerate(keys):
        indent = "  " * depth
        found = next((i for i in range(start, end) if lines[i].startswith(indent + key + ":")), None)
        if found is None:   # cria o que falta, dentro do bloco atual
            insert = [("  " * d) + k + ":" for d, k in enumerate(keys[depth:-1], start=depth)]
            insert.append(("  " * (len(keys) - 1)) + keys[-1] + ": " + value)
            lines[end:end] = insert
            return "\n".join(lines) + "\n"
        if depth == len(keys) - 1:
            lines[found] = f"{indent}{key}: {value}"
            return "\n".join(lines) + "\n"
        child = "  " * (depth + 1)
        start = found + 1
        end = next((i for i in range(start, len(lines)) if lines[i].strip() and not lines[i].startswith(child)), len(lines))
    return "\n".join(lines) + "\n"


def extract(archive: Path, dest: Path) -> None:
    """.7z/.zip com o tar.exe do Windows (libarchive lê o BCJ2 do 7-Zip, que o py7zr não lê). Se tudo veio dentro
    de uma pasta só (ex: "RetroArch-Win64/"), sobe o conteúdo dela para `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest.parent / f".{dest.name}-extract"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    tar = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    result = subprocess.run([str(tar), "-xf", str(archive), "-C", str(tmp)],
                            capture_output=True, text=True, creationflags=CREATE_NO_WINDOW, timeout=900)
    if result.returncode != 0:
        shutil.rmtree(tmp, ignore_errors=True)
        raise RuntimeError(f"Não foi possível extrair {archive.name}: {result.stderr.strip()[-200:]}")
    items = list(tmp.iterdir())
    root = items[0] if len(items) == 1 and items[0].is_dir() else tmp
    for item in root.iterdir():   # por cima do que já existe: mantém BIOS, saves e configurações
        target = dest / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    shutil.rmtree(tmp, ignore_errors=True)


def perf_overlay() -> bool:
    """Configurações → Emuladores → "Mostrar FPS dentro do jogo" (vale para todos)."""
    return bool(load_config().get("emu_perf_overlay", False))


EMULATORS: dict[str, Emulator] = {e.id: e for e in (RPCS3(),)}

from .emulators_retro import DuckStation, PCSX2, RetroArchNES  # noqa: E402  (usam as classes acima)

for _emu in (PCSX2(), DuckStation(), RetroArchNES()):
    EMULATORS[_emu.id] = _emu


def game_art(game: Game) -> dict[str, Path | None]:
    """Imagens que vêm dentro do próprio jogo de PS3: PIC1 (fundo 1920×1080) e ICON0 (ícone)."""
    if game.source != "ps3" or not game.install_dir:
        return {}
    if game.launch_type == "emu":   # ISO: as imagens foram extraídas para o cache no scan
        cache = data_dir() / "art" / slug(game.id)
        pick = lambda name: cache / name if (cache / name).is_file() else None
        return {"hero": pick("ps3-pic1.png"), "icon": pick("ps3-icon0.png")}
    base = Path(game.install_dir)
    base = base / "PS3_GAME" if (base / "PS3_GAME").is_dir() else base
    pick = lambda *names: next((base / n for n in names if (base / n).is_file()), None)
    return {"hero": pick("PIC1.PNG", "PIC0.PNG"), "icon": pick("ICON0.PNG")}

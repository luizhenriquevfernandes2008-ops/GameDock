"""PS2 (PCSX2), PS1 (DuckStation) e NES (RetroArch + núcleo Mesen), no mesmo esquema do RPCS3:
o GameDock baixa o emulador oficial, configura (controle, tela cheia sem borda, teclas de save state,
RetroAchievements) e abre o jogo direto, sem a janela do emulador.

Os três ficam em modo "portátil": as configurações moram na pasta do emulador dentro do GameDock
(%LOCALAPPDATA%\\GameDock\\emulators\\<console>), sem misturar com uma instalação que o usuário já tenha.
PS1 e PS2 precisam do BIOS do console do próprio usuário (o GameDock não baixa BIOS).
"""
from __future__ import annotations

import re
import subprocess
import time
import zipfile
from pathlib import Path

from .emulators import (EMU_ROOT, Emulator, _download, _get, detect_controller, extract, ini_update, perf_overlay)
from .iso9660 import IsoImage
from .models import Game
from .scanners.base import slug
from .storage import data_dir

VK_F1, VK_F2, VK_F3, VK_F4 = 0x70, 0x71, 0x72, 0x73

# botões do DualShock → nomes que o PCSX2/DuckStation usam (iguais aos do SDL: A = embaixo, Y = em cima...)
PAD_BINDS = {"Up": "DPadUp", "Right": "DPadRight", "Down": "DPadDown", "Left": "DPadLeft", "Triangle": "Y",
             "Circle": "B", "Cross": "A", "Square": "X", "Select": "Back", "Start": "Start", "L1": "LeftShoulder",
             "R1": "RightShoulder", "L2": "+LeftTrigger", "R2": "+RightTrigger", "L3": "LeftStick", "R3": "RightStick",
             "LUp": "-LeftY", "LRight": "+LeftX", "LDown": "+LeftY", "LLeft": "-LeftX", "RUp": "-RightY",
             "RRight": "+RightX", "RDown": "+RightY", "RLeft": "-RightX", "Analog": "Guide",
             "LargeMotor": "LargeMotor", "SmallMotor": "SmallMotor"}


def clean_title(stem: str) -> str:
    """"God of War II (USA) (En,Fr)" -> "God of War II"; "Final Fantasy VII (Disc 1)" -> "Final Fantasy VII"."""
    title = re.sub(r"\s*[\(\[][^\)\]]*[\)\]]", "", stem).strip(" -_.")
    title = re.sub(r",\s*The$", "", title)
    return " ".join(title.split()) or stem


def disc_serial(path: Path) -> tuple[str | None, str | None]:
    """(serial, "ps1"|"ps2") lidos do SYSTEM.CNF do disco (BOOT2 = PS2, BOOT = PS1)."""
    try:
        with IsoImage(path) as img:
            cnf = (img.read("SYSTEM.CNF", 4096) or b"").decode("latin-1", "replace")
    except (OSError, ValueError, IndexError):
        return None, None
    m = re.search(r"BOOT(2?)\s*=\s*cdrom0?:\\?([^;\s]+)", cnf, re.I)
    if not m:
        return None, None
    serial = Path(m.group(2).replace("\\", "/")).name.upper().replace("_", "-").replace(".", "")
    return serial, "ps2" if m.group(1) else "ps1"


def _cue_bins(cue: Path) -> list[Path]:
    try:
        text = cue.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [cue.parent / m for m in re.findall(r'FILE\s+"([^"]+)"', text)]


def pad_config(prefix_sdl: bool) -> tuple[dict, str]:
    """(seção [InputSources], prefixo do controle) conforme o controle detectado (mesma regra do RPCS3)."""
    pad = detect_controller()
    use_xinput = not pad or pad["handler"] == "XInput"
    sources = {"SDL": not use_xinput, "SDLControllerEnhancedMode": not use_xinput, "XInput": use_xinput}
    return sources, ("XInput-0" if use_xinput else "SDL-0"), (pad or {}).get("label")


class DiscEmulator(Emulator):
    """Base de PCSX2 e DuckStation (mesmo formato de .ini, mesma linha de comando)."""
    extensions: tuple[str, ...] = ()
    repo = ""
    asset = ""
    tag: str | None = None
    exe_glob = ""
    pad_type = ""
    achievements_support = True

    @property
    def exe(self) -> Path:
        found = sorted(self.folder.glob(self.exe_glob))
        return found[0] if found else self.folder / self.exe_glob.replace("*", "")

    def installed(self) -> bool:
        return self.exe.is_file()

    def extra_status(self) -> dict:
        return {"biosHint": self.bios_hint}

    def install(self) -> None:
        def work(progress):
            self.install_github(self.repo, self.asset, self.tag, progress)
            self.make_portable()
            self.configure()
        self.run_task(f"Baixando o {self.name}…", work)

    def make_portable(self) -> None: ...
    def ini(self) -> Path: ...
    def base_config(self) -> dict: ...

    def configure(self) -> None:
        self.make_portable()
        ini_update(self.ini(), self.base_config())
        self.configure_input()

    def configure_input(self) -> str | None:
        sources, prefix, label = pad_config(True)
        binds = {k: f"{prefix}/{v}" for k, v in PAD_BINDS.items()}
        ini_update(self.ini(), {"InputSources": sources, "Pad1": {"Type": self.pad_type, **binds}})
        self.last_controller = label or "Nenhum controle conectado (ligue antes de jogar)"
        return label

    # jogos
    def _files(self) -> list[Path]:
        found = []
        for root in [Path(f) for f in self.settings()["folders"]]:
            if root.is_dir():
                found += [p for p in [*root.iterdir(), *(c for d in root.iterdir() if d.is_dir() for c in d.iterdir())]
                          if p.is_file() and p.suffix.lower() in self.extensions]
        listed = {b.resolve() for cue in found if cue.suffix.lower() == ".cue" for b in _cue_bins(cue)}
        m3u_listed = {(p.parent / line.strip()).resolve() for p in found if p.suffix.lower() == ".m3u"
                      for line in p.read_text(errors="replace").splitlines() if line.strip() and not line.startswith("#")}
        return [p for p in found if p.resolve() not in listed and p.resolve() not in m3u_listed]

    def _disc_for_id(self, path: Path) -> Path:
        """Arquivo a ler para achar o serial (.cue → o primeiro .bin; .m3u → o primeiro disco)."""
        if path.suffix.lower() == ".cue":
            bins = _cue_bins(path)
            return bins[0] if bins else path
        if path.suffix.lower() == ".m3u":
            first = next((l.strip() for l in path.read_text(errors="replace").splitlines() if l.strip() and not l.startswith("#")), "")
            return self._disc_for_id(path.parent / first) if first else path
        return path

    def scan(self) -> list[Game]:
        if not self.installed():
            return []
        self.warnings = []
        games: dict[str, Game] = {}
        discs: dict[str, list[Path]] = {}
        for path in self._files():
            serial, kind = disc_serial(self._disc_for_id(path)) if path.suffix.lower() not in (".chd", ".cso", ".zso", ".pbp") else (None, None)
            if kind and kind != self.id:
                continue   # disco do outro PlayStation na mesma pasta
            title = clean_title(path.stem)
            multi = re.search(r"\((Disc|Disco|CD)\s*(\d+)\)", path.stem, re.I)
            if multi:
                discs.setdefault(title, []).append(path)
                if int(multi.group(2)) != 1:
                    continue
            game_id = f"{self.id}:{serial or slug(title)}"
            games[game_id] = Game(id=game_id, name=title, source=self.id, launch_type="emu",
                                  launch_target=str(path), install_dir=str(path))
        for game in games.values():   # jogo de vários discos: playlist .m3u (o emulador troca de disco sozinho)
            parts = discs.get(game.name)
            if parts and len(parts) > 1:
                playlist = self.folder / "playlists" / f"{slug(game.name)}.m3u"
                playlist.parent.mkdir(parents=True, exist_ok=True)
                playlist.write_text("\n".join(str(p) for p in sorted(parts)) + "\n", encoding="utf-8")
                game.launch_target = str(playlist)
        return list(games.values())

    def before_launch(self, game: Game) -> None: ...

    def launch(self, game: Game) -> subprocess.Popen:
        target = Path(game.launch_target)
        if not target.is_file():
            raise RuntimeError(f"Jogo não encontrado: {target}")
        if not self.bios_installed():
            raise RuntimeError(f"Falta o BIOS do {self.platform}. Configurações → Emuladores → {self.platform} → Escolher BIOS.")
        if self.settings()["auto_controller"]:
            self.configure_input()
        ini_update(self.ini(), self.base_config())
        self.before_launch(game)
        args = ["-batch", "-nogui"] + (["-fullscreen"] if self.settings()["fullscreen"] else [])
        return subprocess.Popen([str(self.exe), *args, "--", str(target)], cwd=str(self.folder))

    def configure_achievements(self, ra: dict | None) -> None:
        section = self.ACH_SECTION
        values = {"Enabled": bool(ra), "ChallengeMode": False}
        if ra:
            values |= {"Username": ra["user"], "Token": ra["token"], "LoginTimestamp": int(time.time())}
        ini_update(self.ini(), {section: values})

    ACH_SECTION = "Achievements"


# ---------------------------------------------------------------- PS2

class PCSX2(DiscEmulator):
    id = "ps2"
    name = "PCSX2"
    platform = "PlayStation 2"
    repo = "PCSX2/pcsx2"
    asset = r"windows-x64-Qt\.7z$"
    exe_glob = "pcsx2-qt*.exe"
    pad_type = "DualShock2"
    extensions = (".iso", ".chd", ".cso", ".zso", ".bin", ".cue", ".gz")
    needs_bios = True
    bios_min = 4 * 1024 * 1024
    bios_hint = "O BIOS é um arquivo de ~4 MB copiado do seu PS2 (ex: SCPH-39001.bin)."
    savestate_keys = {"save": VK_F1, "load": VK_F3}
    ENHANCEMENTS = [
        {"key": "scale", "label": "Resolução", "default": 2,
         "options": [[1, "Nativa (480p)"], [2, "2× (720p)"], [3, "3× (1080p)"], [4, "4× (1440p)"], [6, "6× (4K)"]]},
        {"key": "widescreen", "label": "Widescreen (16:9)", "default": False, "type": "bool",
         "hint": "usa o patch de widescreen do próprio jogo, quando existe"},
        {"key": "nointerlace", "label": "Sem entrelaçado", "default": True, "type": "bool",
         "hint": "imagem mais nítida em jogos que tremem"},
    ]

    def make_portable(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "portable.ini").touch()   # configurações dentro da pasta do emulador

    def ini(self) -> Path:
        return self.folder / "inis" / "PCSX2.ini"

    def base_config(self) -> dict:
        return {
            "UI": {"SetupWizardIncomplete": False, "ConfirmShutdown": False, "HideMouseCursor": True,
                   "StartFullscreen": self.settings()["fullscreen"]},
            "Folders": {"Bios": "bios"},
            "EmuCore/GS": {"OsdShowFPS": perf_overlay(), "OsdShowSpeed": perf_overlay()},
            "Hotkeys": {"SaveStateToSlot": "Keyboard/F1", "NextSaveStateSlot": "Keyboard/F2",
                        "LoadStateFromSlot": "Keyboard/F3", "ToggleFullscreen": "Keyboard/Alt & Keyboard/Return"},
        }

    def before_launch(self, game: Game) -> None:
        opts = self.game_settings(game.id)
        bios = next((f.name for f in sorted(self.bios_dir.iterdir()) if f.stat().st_size >= self.bios_min), "")
        ini_update(self.ini(), {"Filenames": {"BIOS": bios},
                                "EmuCore/GS": {"upscale_multiplier": int(opts["scale"])},
                                "EmuCore": {"EnableWideScreenPatches": bool(opts["widescreen"]),
                                            "EnableNoInterlacingPatches": bool(opts["nointerlace"])}})

    def save_paths(self, game: Game) -> list[Path]:
        serial = game.id.split(":", 1)[1]
        return [self.folder / "memcards", *sorted((self.folder / "sstates").glob(f"{serial}*"))]


# ---------------------------------------------------------------- PS1

class DuckStation(DiscEmulator):
    id = "ps1"
    name = "DuckStation"
    platform = "PlayStation"
    repo = "stenzek/duckstation"
    asset = r"duckstation-windows-x64-release\.zip$"
    tag = "latest"
    exe_glob = "duckstation-qt*.exe"
    pad_type = "AnalogController"
    extensions = (".cue", ".chd", ".pbp", ".iso", ".m3u", ".img", ".ecm", ".bin")
    needs_bios = True
    bios_min = 512 * 1024
    bios_hint = "O BIOS é um arquivo de 512 KB copiado do seu PS1 (ex: SCPH1001.BIN)."
    savestate_keys = {"save": VK_F2, "load": VK_F1}
    ACH_SECTION = "Cheevos"
    ENHANCEMENTS = [
        {"key": "scale", "label": "Resolução", "default": 3,
         "options": [[1, "Nativa (240p)"], [2, "2× (480p)"], [3, "3× (720p)"], [5, "5× (1080p)"], [9, "9× (4K)"]]},
        {"key": "widescreen", "label": "Widescreen (16:9)", "default": False, "type": "bool",
         "hint": "estica o cenário em 3D sem distorcer (pode mostrar falhas nas bordas)"},
        {"key": "pgxp", "label": "Geometria precisa (PGXP)", "default": True, "type": "bool",
         "hint": "acaba com os polígonos \"tremendo\" típicos do PS1"},
    ]

    def make_portable(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        (self.folder / "portable.txt").touch()

    def ini(self) -> Path:
        return self.folder / "settings.ini"

    def base_config(self) -> dict:
        return {
            "Main": {"SetupWizardIncomplete": False, "ConfirmPowerOff": False, "StartFullscreen": self.settings()["fullscreen"],
                     "PauseOnFocusLoss": False},
            "BIOS": {"SearchDirectory": str(self.bios_dir)},
            "Display": {"ShowFPS": perf_overlay(), "ShowSpeed": perf_overlay()},
            "Pad1": {"ForceAnalogOnReset": True},
            "Hotkeys": {"LoadSelectedSaveState": "Keyboard/F1", "SaveSelectedSaveState": "Keyboard/F2",
                        "SelectNextSaveStateSlot": "Keyboard/F4", "ToggleFullscreen": "Keyboard/Alt & Keyboard/Return"},
            "AutoUpdater": {"CheckAtStartup": False},
        }

    def before_launch(self, game: Game) -> None:
        opts = self.game_settings(game.id)
        ini_update(self.ini(), {"GPU": {"ResolutionScale": int(opts["scale"]), "WidescreenHack": bool(opts["widescreen"]),
                                        "PGXPEnable": bool(opts["pgxp"])},
                                "Display": {"AspectRatio": "16:9" if opts["widescreen"] else "Auto (Game Native)"}})

    def save_paths(self, game: Game) -> list[Path]:
        cards = self.folder / "memcards"
        mine = [p for p in cards.glob("*.mcd") if re.sub(r"[^a-z0-9]", "", p.stem.lower()).startswith(
            re.sub(r"[^a-z0-9]", "", game.name.lower())[:12])] if cards.is_dir() else []
        serial = game.id.split(":", 1)[1]
        return [*(mine or ([cards] if cards.is_dir() else [])), *sorted((self.folder / "savestates").glob(f"{serial}*"))]


# ---------------------------------------------------------------- NES

class RetroArchNES(Emulator):
    """NES pelo RetroArch com o núcleo Mesen (um dos mais precisos). O RetroArch tem perfis prontos de controle
    (autoconfig), save states e RetroAchievements."""
    id = "nes"
    name = "RetroArch (Mesen)"
    platform = "NES"
    STABLE = "https://buildbot.libretro.com/stable/"
    CORE = "https://buildbot.libretro.com/nightly/windows/x86_64/latest/mesen_libretro.dll.zip"
    extensions = (".nes", ".fds", ".unf", ".unif", ".zip")
    savestate_keys = {"save": VK_F2, "load": VK_F4}
    achievements_support = True
    ENHANCEMENTS = [
        {"key": "integer", "label": "Pixels perfeitos", "default": True, "type": "bool",
         "hint": "escala inteira: pixels quadrados e nítidos (pode sobrar borda)"},
        {"key": "smooth", "label": "Suavizar imagem", "default": False, "type": "bool", "hint": "filtro bilinear"},
    ]

    @property
    def exe(self) -> Path:
        return self.folder / "retroarch.exe"

    @property
    def core(self) -> Path:
        return self.folder / "cores" / "mesen_libretro.dll"

    def installed(self) -> bool:
        return self.exe.is_file() and self.core.is_file()

    def install(self) -> None:
        def work(progress):
            listing = _get(self.STABLE).decode("utf-8", "replace")
            versions = sorted({v for v in re.findall(r'href="/stable/([\d.]+)/"', listing)},
                              key=lambda v: tuple(int(x) for x in v.split(".")))
            version = versions[-1]
            EMU_ROOT.mkdir(parents=True, exist_ok=True)
            archive = EMU_ROOT / "RetroArch.7z"
            _download(f"{self.STABLE}{version}/windows/x86_64/RetroArch.7z", archive, progress)
            self.task["label"] = "Extraindo…"
            extract(archive, self.folder)
            archive.unlink(missing_ok=True)
            self.task.update(label="Baixando o núcleo de NES (Mesen)…", done=0, total=0)
            core_zip = EMU_ROOT / "mesen_libretro.dll.zip"
            _download(self.CORE, core_zip, progress)
            (self.folder / "cores").mkdir(exist_ok=True)
            with zipfile.ZipFile(core_zip) as zf:
                zf.extract("mesen_libretro.dll", self.folder / "cores")
            core_zip.unlink(missing_ok=True)
            (self.folder / "gamedock-version.json").write_text(f'{{"version": "{version}"}}', encoding="utf-8")
            self.configure()
        self.run_task("Baixando o RetroArch…", work)

    def cfg(self) -> Path:
        return self.folder / "retroarch.cfg"

    def _cfg_update(self, values: dict) -> None:
        path = self.cfg()
        text = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else ""
        for key, value in values.items():
            value = ("true" if value else "false") if isinstance(value, bool) else str(value)
            line = f'{key} = "{value}"'
            text, n = re.subn(rf"^{re.escape(key)}\s*=.*$", line, text, flags=re.M)
            if not n:
                text += ("" if not text or text.endswith("\n") else "\n") + line + "\n"
        path.write_text(text, encoding="utf-8")

    def configure(self) -> None:
        self.configure_input()
        self._cfg_update({
            "video_fullscreen": self.settings()["fullscreen"], "video_windowed_fullscreen": True,
            "pause_nonactive": False, "menu_show_quit_retroarch": True, "config_save_on_exit": False,
            "input_save_state": "f2", "input_load_state": "f4", "input_exit_emulator": "escape",
            "input_menu_toggle_gamepad_combo": 2,   # L3 + R3 abre o menu do RetroArch
            "input_quit_gamepad_combo": 4,          # Start + Select fecha o jogo
            "fps_show": perf_overlay(), "savestate_auto_index": False, "video_driver": "vulkan",
        })

    def configure_input(self) -> str | None:
        pad = detect_controller()
        self._cfg_update({"input_joypad_driver": "xinput" if not pad or pad["handler"] == "XInput" else "sdl2",
                          "input_autodetect_enable": True})
        self.last_controller = (pad or {}).get("label") or "Nenhum controle conectado (ligue antes de jogar)"
        return (pad or {}).get("label")

    def configure_achievements(self, ra: dict | None) -> None:
        values = {"cheevos_enable": bool(ra), "cheevos_hardcore_mode_enable": False}
        if ra:
            values |= {"cheevos_username": ra["user"], "cheevos_token": ra["token"], "cheevos_password": ""}
        self._cfg_update(values)

    def _roms(self) -> list[Path]:
        found = []
        for root in [Path(f) for f in self.settings()["folders"]]:
            if not root.is_dir():
                continue
            for p in [*root.iterdir(), *(c for d in root.iterdir() if d.is_dir() for c in d.iterdir())]:
                if not p.is_file() or p.suffix.lower() not in self.extensions:
                    continue
                if p.suffix.lower() == ".zip":
                    try:
                        with zipfile.ZipFile(p) as zf:
                            if not any(n.lower().endswith((".nes", ".fds", ".unf")) for n in zf.namelist()):
                                continue
                    except zipfile.BadZipFile:
                        continue
                found.append(p)
        return found

    def scan(self) -> list[Game]:
        if not self.installed():
            return []
        games = {}
        for rom in self._roms():
            title = clean_title(rom.stem)
            game_id = f"nes:{slug(title)}"
            games.setdefault(game_id, Game(id=game_id, name=title, source="nes", launch_type="emu",
                                           launch_target=str(rom), install_dir=str(rom)))
        return list(games.values())

    def launch(self, game: Game) -> subprocess.Popen:
        rom = Path(game.launch_target)
        if not rom.is_file():
            raise RuntimeError(f"Jogo não encontrado: {rom}")
        if self.settings()["auto_controller"]:
            self.configure_input()
        opts = self.game_settings(game.id)
        self._cfg_update({"video_fullscreen": self.settings()["fullscreen"], "fps_show": perf_overlay(),
                          "video_scale_integer": bool(opts["integer"]), "video_smooth": bool(opts["smooth"])})
        args = ["-L", str(self.core), str(rom)] + (["-f"] if self.settings()["fullscreen"] else [])
        return subprocess.Popen([str(self.exe), *args], cwd=str(self.folder))

    def save_paths(self, game: Game) -> list[Path]:
        stem = Path(game.launch_target).stem
        return [p for p in (*(self.folder / "saves").glob(f"**/{stem}.*"), *(self.folder / "states").glob(f"**/{stem}.*"))]

"""Decide se um .exe qualquer é um jogo, olhando o próprio executável e a pasta dele.

Usado para atalhos da Área de Trabalho que não pertencem a nenhum launcher conhecido
(jogos instalados "na mão", repacks, jogos antigos...). Cada evidência soma ou subtrai pontos:

  + motor de jogo na pasta (Unity, Unreal, Godot, GameMaker, RPG Maker, Ren'Py, Source)
  + middleware típico de jogo: Bink (vídeo), FMOD/Wwise (áudio), PhysX, SDKs de Steam/Epic/GOG
  + o .exe importa DirectX / OpenGL / Vulkan / XInput / DirectInput (lido do cabeçalho PE)
  + vídeos de jogo (.bik/.bk2/.usm) e muitos GB em arquivos de dados grandes
  + caminho com pasta "Games"/"Jogos"
  − app Electron/Chromium (Discord, VS Code, Docker...), instalador Squirrel, pastas do sistema
"""
from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from pathlib import Path

THRESHOLD = 6

# DLLs que aparecem ao lado do .exe -> (pontos, motivo)
GAME_FILES = {
    "unityplayer.dll": (6, "Unity"),
    "gameassembly.dll": (4, "Unity IL2CPP"),
    "data.win": (6, "GameMaker"),
    "steam_api.dll": (4, "Steam SDK"), "steam_api64.dll": (4, "Steam SDK"),
    "eossdk-win64-shipping.dll": (3, "Epic SDK"), "eossdk-win32-shipping.dll": (3, "Epic SDK"),
    "galaxy.dll": (4, "GOG Galaxy SDK"), "galaxy64.dll": (4, "GOG Galaxy SDK"),
    "binkw32.dll": (3, "vídeo Bink"), "binkw64.dll": (3, "vídeo Bink"),
    "bink2w32.dll": (3, "vídeo Bink"), "bink2w64.dll": (3, "vídeo Bink"),
    "fmod.dll": (3, "áudio FMOD"), "fmod64.dll": (3, "áudio FMOD"), "fmodex.dll": (3, "áudio FMOD"),
    "fmodex64.dll": (3, "áudio FMOD"), "fmodstudio.dll": (3, "áudio FMOD"),
    "aksoundengine.dll": (3, "áudio Wwise"),
    "physxloader.dll": (3, "PhysX"), "physx3_x64.dll": (3, "PhysX"), "physx3_x86.dll": (3, "PhysX"),
    "physx_64.dll": (3, "PhysX"), "physxcore.dll": (3, "PhysX"),
    "xinput1_3.dll": (2, "XInput"), "d3dx9_43.dll": (2, "DirectX 9"),
    "openal32.dll": (2, "OpenAL"), "sdl2.dll": (2, "SDL2"), "sdl3.dll": (2, "SDL3"),
    "rgss3a.dll": (6, "RPG Maker"), "rgss301.dll": (6, "RPG Maker"),
    "gameinfo.txt": (5, "Source Engine"),
}
GAME_DIRS = {"engine": (3, "Unreal Engine"), "renpy": (6, "Ren'Py"), "www": (1, "RPG Maker MV/MZ")}

# importações do PE -> (pontos, motivo). Cada grupo conta uma vez só.
IMPORT_GROUPS = (
    (re.compile(r"^(d3d9|d3d10|d3d11|d3d12|dxgi|ddraw|opengl32|vulkan-1)\.dll$"), 2, "gráficos 3D"),
    (re.compile(r"^(xinput[\d_]*|dinput8?|xinput9_1_0)\.dll$"), 2, "entrada de controle"),
    (re.compile(r"^(xaudio2_\d|x3daudio1_\d|dsound)\.dll$"), 1, "áudio de jogo"),
    (re.compile(r"^(steam_api(64)?|eossdk.*|galaxy(64)?)\.dll$"), 3, "SDK de loja"),
)
VIDEO_EXT = {".bik", ".bk2", ".usm"}
DATA_EXT = {".pak", ".dat", ".arc", ".bundle", ".assets", ".vpk", ".wad", ".big", ".forge",
            ".rpf", ".pck", ".bnk", ".cpk", ".bin", ".gcf", ".rgssad", ".rgss3a", ".ucas", ".utoc"}
CHROMIUM_PAKS = re.compile(r"^(resources|chrome_\d+_percent|en-us)\.pak$", re.I)
SUBFOLDERS_TO_ROOT = {"bin", "bin32", "bin64", "binaries", "win32", "win64", "x86", "x64",
                      "retail", "system", "exe", "game"}
SYSTEM_PATHS = re.compile(r"\\(windows|program files( \(x86\))?\\(microsoft|windowsapps|common files))\\", re.I)
APP_WORDS = re.compile(r"mod[ _-]?manager|launcher|updater|installer|setup|editor|server|tool", re.I)


@dataclass
class Verdict:
    exe: Path
    root: Path
    score: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def is_game(self) -> bool:
        return self.score >= THRESHOLD

    def add(self, points: int, reason: str) -> None:
        self.score += points
        self.reasons.append(f"{points:+d} {reason}")


def install_root(exe: Path) -> Path:
    """D:\\Games\\Castlevania\\bin\\x.exe -> D:\\Games\\Castlevania"""
    folder = exe.parent
    while folder.name.lower() in SUBFOLDERS_TO_ROOT and folder.parent != folder:
        folder = folder.parent
    return folder


# ---------------------------------------------------------------- leitura do PE

def pe_imports(exe: Path) -> list[str]:
    """Nomes das DLLs na tabela de importação de um executável Windows (PE32/PE32+)."""
    # Lê só os pedaços necessários com seek: alguns jogos têm .exe de centenas de MB.
    try:
        with exe.open("rb") as f:
            def read(offset: int, size: int) -> bytes:
                f.seek(offset)
                return f.read(size)

            head = read(0, 4096)
            if head[:2] != b"MZ":
                return []
            pe = struct.unpack_from("<I", head, 0x3C)[0]
            header = read(pe, 4096)
            if header[:4] != b"PE\0\0":
                return []
            sections_count = struct.unpack_from("<H", header, 6)[0]
            optional_size = struct.unpack_from("<H", header, 20)[0]
            magic = struct.unpack_from("<H", header, 24)[0]
            data_dirs = 24 + (96 if magic == 0x10B else 112)  # PE32 / PE32+
            import_rva = struct.unpack_from("<I", header, data_dirs + 8)[0]  # diretório 1 = imports
            table = 24 + optional_size
            sections = []
            for i in range(sections_count):
                vsize, vaddr, rsize, raw = struct.unpack_from("<IIII", header, table + i * 40 + 8)
                sections.append((vaddr, max(vsize, rsize), raw))

            def offset(rva: int) -> int | None:
                for vaddr, size, raw in sections:
                    if vaddr <= rva < vaddr + size:
                        return rva - vaddr + raw
                return None

            names = []
            pos = offset(import_rva) if import_rva else None
            while pos is not None and len(names) < 512:
                descriptor = read(pos, 20)
                if len(descriptor) < 20:
                    break
                name_rva = struct.unpack_from("<I", descriptor, 12)[0]
                if name_rva == 0:
                    break
                name_at = offset(name_rva)
                if name_at is not None:
                    names.append(read(name_at, 256).split(b"\0", 1)[0].decode("ascii", "replace").lower())
                pos += 20
            return names
    except (OSError, struct.error, ValueError):
        return []


# ---------------------------------------------------------------- análise

def _scan_tree(root: Path, max_depth: int = 3, max_files: int = 20000):
    stack = [(root, 0)]
    count = 0
    while stack and count < max_files:
        folder, depth = stack.pop()
        try:
            entries = list(folder.iterdir())
        except OSError:
            continue
        for entry in entries:
            count += 1
            try:
                if entry.is_dir():
                    if depth < max_depth:
                        stack.append((entry, depth + 1))
                elif entry.is_file():
                    yield entry
            except OSError:
                continue


def analyze(exe: Path, quick: bool = False) -> Verdict:
    """quick=True para no meio do caminho quando o veredito já está claro (usado no scan).
    Sem quick, junta todas as evidências (usado pelo comando `detect`, para explicar)."""
    exe = Path(exe)
    root = install_root(exe)
    verdict = Verdict(exe=exe, root=root)
    if SYSTEM_PATHS.search(str(exe)) and quick:
        verdict.add(-10, "pasta do sistema")
        return verdict

    if SYSTEM_PATHS.search(str(exe)) and not quick:
        verdict.add(-10, "pasta do sistema")
    if APP_WORDS.search(exe.stem):
        verdict.add(-3, f"nome de ferramenta ({exe.stem})")
    if re.search(r"\\(games|jogos|gog games|steamlibrary)\\", str(exe), re.I):
        verdict.add(2, "instalado numa pasta de jogos")

    # arquivos ao redor do exe
    near = {}
    for folder in {exe.parent, root}:
        try:
            near.update({p.name.lower(): p for p in folder.iterdir()})
        except OSError:
            pass
    seen_reasons = set()
    for name, (points, reason) in GAME_FILES.items():
        if name in near and reason not in seen_reasons:
            seen_reasons.add(reason)
            verdict.add(points, reason)
    for name, (points, reason) in GAME_DIRS.items():
        if name in near and near[name].is_dir():
            verdict.add(points, reason)
    if (exe.parent / f"{exe.stem}_Data").is_dir():
        verdict.add(5, "pasta _Data do Unity")
    if (exe.parent / f"{exe.stem}.pck").is_file():
        verdict.add(6, "Godot")
    if "shipping" in exe.stem.lower():
        verdict.add(4, "build Shipping do Unreal")

    # sinais de app que não é jogo
    if "resources" in near and ((near["resources"] / "app.asar").exists()):
        verdict.add(-10, "app Electron")
    if "license.electron.txt" in near or "licenses.chromium.html" in near:
        verdict.add(-8, "app baseado em Chromium")
    if exe.parent.name.lower().startswith("app-") and (exe.parent.parent / "Update.exe").exists():
        verdict.add(-6, "instalador Squirrel (app desktop)")

    # o que o exe importa
    imports = pe_imports(exe)
    for pattern, points, reason in IMPORT_GROUPS:
        if any(pattern.match(dll) for dll in imports):
            verdict.add(points, f"importa {reason}")

    # varrer a pasta é a parte cara; no scan só fazemos quando ainda há dúvida.
    # Com evidência contra (score < 0), nem muitos GB de dados salvariam o exe.
    if quick and (verdict.score >= THRESHOLD or verdict.score < 0):
        return verdict

    # vídeos e dados do jogo
    videos, data_bytes = 0, 0
    for file in _scan_tree(root):
        ext = file.suffix.lower()
        if ext in VIDEO_EXT:
            videos += 1
        elif ext in DATA_EXT and not CHROMIUM_PAKS.match(file.name):
            try:
                size = file.stat().st_size
            except OSError:
                continue
            if size > 50_000_000:
                data_bytes += size
    if videos:
        verdict.add(2, f"{videos} vídeo(s) de jogo")
    gb = data_bytes / 1e9
    if gb >= 4:
        verdict.add(4, f"{gb:.1f} GB em arquivos de dados")
    elif gb >= 0.5:
        verdict.add(2, f"{gb:.1f} GB em arquivos de dados")

    return verdict

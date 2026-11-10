"""Detecção: atalhos .lnk (IDList do Inno Setup), argumentos, carregadores de mod, ports e sugestões."""
import json
import struct
import tempfile
import unittest
from pathlib import Path

import tests  # noqa: F401  (GAMEDOCK_HOME temporário)
from gamedock.art import search_name
from gamedock.formats import read_lnk
from gamedock.models import Game
from gamedock.scanners.shortcuts import (_candidate_ok, _flag_value, _split_args, clean_name, port_title,
                                         recomp_game)


# ---------------------------------------------------------------- montar um .lnk só com IDList

def _file_item(name: str, is_dir: bool) -> bytes:
    """Item de arquivo/pasta da IDList com nome curto + bloco 0xBEEF0004 (versão 9) com o nome longo."""
    short = name.upper().replace(" ", "")[:8].encode("ascii") + b"\0"
    if len(short) % 2:
        short += b"\0"
    long_name = name.encode("utf-16-le") + b"\0\0"
    ext_body = (struct.pack("<II", 0, 0) + struct.pack("<H", 0x2E) + b"\0" * 18 + struct.pack("<H", 0)
                + b"\0" * 8 + long_name)
    ext = struct.pack("<HHI", 8 + len(ext_body) + 2, 9, 0xBEEF0004) + ext_body
    ext += struct.pack("<H", 0)   # offset do 1º bloco (não usado pelo leitor)
    body = bytes([0x31 if is_dir else 0x32, 0]) + struct.pack("<IIH", 0, 0, 0x10 if is_dir else 0x20) + short + ext
    return struct.pack("<H", len(body) + 2) + body


def make_idlist_lnk(path: Path, target: str) -> None:
    drive, *parts = Path(target).parts
    items = struct.pack("<H", 0x14) + bytes([0x1F, 0x50]) + b"\x11" * 16          # "Este Computador"
    vol = bytes([0x2F]) + drive.encode("ascii") + b"\0" * (22 - len(drive))
    items += struct.pack("<H", len(vol) + 2) + vol
    for i, part in enumerate(parts):
        items += _file_item(part, is_dir=i < len(parts) - 1)
    items += b"\0\0"
    header = bytearray(0x4C)
    header[0:4] = b"\x4c\x00\x00\x00"
    struct.pack_into("<I", header, 0x14, 0x01 | 0x80)   # HasLinkTargetIDList | IsUnicode
    path.write_bytes(bytes(header) + struct.pack("<H", len(items)) + items)


class LnkTests(unittest.TestCase):
    def test_inno_style_idlist_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            lnk = Path(tmp) / "Iron Blight.lnk"
            make_idlist_lnk(lnk, r"D:\Games\Iron Blight\Iron Blight.exe")
            self.assertEqual(read_lnk(lnk)["target"], r"D:\Games\Iron Blight\Iron Blight.exe")

    def test_garbage_is_not_a_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            lnk = Path(tmp) / "x.lnk"
            lnk.write_bytes(b"\x4c\x00\x00\x00" + b"\xff" * 200)
            info = read_lnk(lnk)
            self.assertTrue(info is None or isinstance(info, dict))


class ArgsTests(unittest.TestCase):
    def test_quotes_are_removed(self):
        self.assertEqual(_split_args(r'--launch "C:\a b\sh3.exe"'), ["--launch", r"C:\a b\sh3.exe"])

    def test_plain_args(self):
        self.assertEqual(_split_args("--launch-product=lion --launch-patchline=live"),
                         ["--launch-product=lion", "--launch-patchline=live"])
        self.assertEqual(_split_args("   "), [])

    def test_flag_value(self):
        self.assertEqual(_flag_value(r'--launch "C:\x y\sh3.exe" --foo', "--launch"), r"C:\x y\sh3.exe")
        self.assertIsNone(_flag_value("--other x", "--launch"))


class NameTests(unittest.TestCase):
    def game(self, name, custom=None):
        return Game(id="x", name=name, source="local", launch_type="exe", launch_target="", custom_name=custom)

    def test_search_name_strips_tags(self):
        self.assertEqual(search_name(self.game("SILENT HILL 3 (Reloaded)")), "SILENT HILL 3")
        self.assertEqual(search_name(self.game("Jogo [GOG] (2003)")), "Jogo")
        self.assertEqual(search_name(self.game("Lords of Shadow 2")), "Lords of Shadow 2")
        self.assertEqual(search_name(self.game("(Weird)")), "(Weird)")

    def test_custom_name_wins(self):
        self.assertEqual(search_name(self.game("sh3", custom="Silent Hill 3")), "Silent Hill 3")

    def test_shortcut_suffix(self):
        self.assertEqual(clean_name("MoonSweepers - Atalho"), "MoonSweepers")
        self.assertEqual(clean_name("Jogo (2)"), "Jogo")

    def test_port_titles(self):
        self.assertEqual(port_title(r"D:\soh\soh.exe"), "The Legend of Zelda: Ocarina of Time")
        self.assertIsNone(port_title(r"D:\x\game.exe"))


class RecompTests(unittest.TestCase):
    def test_nx2navi_reads_game_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            launcher_dir, game_dir = Path(tmp) / "NX2navi", Path(tmp) / "Games" / "NX2navi"
            (launcher_dir / "data").mkdir(parents=True)
            game_dir.mkdir(parents=True)
            (game_dir / "ocarina.exe").write_bytes(b"MZ")
            (launcher_dir / "NX2navi.exe").write_bytes(b"MZ")
            (launcher_dir / "data" / "launcher.json").write_text(json.dumps(
                {"game_dir": str(game_dir), "dump": r"C:\x\The Legend of Zelda Ocarina of Time [0400486024dec000][v1].nup"}))
            name, folder, exe = recomp_game(launcher_dir / "NX2navi.exe", "nx2navi")
            self.assertEqual(name, "The Legend of Zelda: Ocarina of Time")
            self.assertEqual(folder, game_dir)
            self.assertEqual(exe.name, "ocarina.exe")

    def test_nx2navi_without_game(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "data").mkdir()
            (Path(tmp) / "data" / "launcher.json").write_text(json.dumps({"game_dir": ""}))
            self.assertIsNone(recomp_game(Path(tmp) / "NX2navi.exe", "nx2navi"))


class CandidateTests(unittest.TestCase):
    def test_program_files_and_tools_are_skipped(self):
        self.assertFalse(_candidate_ok(Path(r"C:\Program Files\WinRAR\WinRAR.exe")))
        self.assertFalse(_candidate_ok(Path(r"C:\Users\x\Downloads\app\setup.exe")))

    def test_chromium_apps_are_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "App"
            (app / "1a2b3c").mkdir(parents=True)
            (app / "1a2b3c" / "chrome_100_percent.pak").write_bytes(b"")
            self.assertFalse(_candidate_ok(app / "App.exe"))

    def test_plain_folder_is_a_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertTrue(_candidate_ok(Path(tmp) / "Fritchii" / "lichii.exe"))


if __name__ == "__main__":
    unittest.main()

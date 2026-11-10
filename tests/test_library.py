"""Biblioteca: junção do scan, fontes cobertas, travas do desinstalar, limpeza de pastas e sessões retomadas."""
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import tests  # noqa: F401  (GAMEDOCK_HOME temporário)
from gamedock import playtime, uninstall
from gamedock.models import Game
from gamedock.scanners import scanned_sources
from gamedock.scanners.base import Scanner
from gamedock.storage import Library


def game(id, source, target, launch_type="exe", **kw):
    return Game(id=id, name=id, source=source, launch_type=launch_type, launch_target=str(target), **kw)


class MergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lib = Library(self.dir / "library.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_deleted_iso_is_missing_even_if_source_not_scanned(self):
        iso = self.dir / "jogo.iso"
        self.lib.games["ps3:x"] = game("ps3:x", "ps3", iso, launch_type="emu", install_dir=str(iso))
        report = self.lib.merge_scan([], scanned_sources=set())   # emulador nem rodou
        self.assertEqual([g.id for g in report["missing"]], ["ps3:x"])
        self.assertFalse(self.lib.games["ps3:x"].installed)

    def test_existing_iso_stays_when_source_not_scanned(self):
        iso = self.dir / "jogo.iso"
        iso.write_bytes(b"x")
        self.lib.games["ps3:x"] = game("ps3:x", "ps3", iso, launch_type="emu")
        self.lib.merge_scan([], scanned_sources=set())
        self.assertTrue(self.lib.games["ps3:x"].installed)

    def test_local_game_survives_deleted_shortcut(self):
        exe = self.dir / "game.exe"
        exe.write_bytes(b"MZ")
        self.lib.games["local:g"] = game("local:g", "local", exe)
        self.lib.merge_scan([], scanned_sources={"local"})
        self.assertTrue(self.lib.games["local:g"].installed)

    def test_steam_game_missing_only_when_steam_scanned(self):
        self.lib.games["steam:1"] = game("steam:1", "steam", "steam://rungameid/1", launch_type="uri")
        self.lib.merge_scan([], scanned_sources={"local"})
        self.assertTrue(self.lib.games["steam:1"].installed)
        self.lib.merge_scan([], scanned_sources={"steam"})
        self.assertFalse(self.lib.games["steam:1"].installed)

    def test_manual_game_follows_file(self):
        exe = self.dir / "manual.exe"
        self.lib.games["manual:m"] = game("manual:m", "manual", exe, manual=True)
        self.lib.merge_scan([], scanned_sources=set())
        self.assertFalse(self.lib.games["manual:m"].installed)
        exe.write_bytes(b"MZ")
        self.lib.merge_scan([], scanned_sources=set())
        self.assertTrue(self.lib.games["manual:m"].installed)

    def test_failed_scanner_covers_nothing(self):
        class Broken(Scanner):
            name = "steam"
        class Shortcuts(Scanner):
            name = "shortcuts"
            sources = {"local", "riot"}
        self.assertEqual(scanned_sources([Broken(), Shortcuts()], errors={"steam": "boom"}), {"local", "riot"})


class UninstallSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        fake = mock.MagicMock()
        fake.return_value.games = {}
        self.patch = mock.patch.object(uninstall, "Library", fake)
        self.patch.start()
        self.fake = fake

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def g(self, folder):
        return game("local:x", "local", Path(folder) / "x.exe", install_dir=str(folder))

    def test_refuses_dangerous_folders(self):
        home = Path.home()
        for folder in (Path(home.drive + "\\"), home, home / "Desktop"):
            self.assertIsNotNone(uninstall._unsafe_reason(folder, self.g(folder)), folder)

    def test_refuses_library_folder(self):
        lib = self.dir / "Games"
        lib.mkdir()
        self.assertIn("vários jogos", uninstall._unsafe_reason(lib, self.g(lib)))

    def test_refuses_folder_with_another_game(self):
        outer = self.dir / "Pack"
        (outer / "Other").mkdir(parents=True)
        other = game("local:o", "local", outer / "Other" / "o.exe", install_dir=str(outer / "Other"))
        self.fake.return_value.games = {"local:o": other}
        self.assertIn("local:o", uninstall._unsafe_reason(outer, self.g(outer)))

    def test_allows_normal_game_folder(self):
        folder = self.dir / "Meu Jogo"
        folder.mkdir()
        self.assertIsNone(uninstall._unsafe_reason(folder, self.g(folder)))

    def test_remove_empty_dirs_stops_at_content(self):
        deep = self.dir / "Outer" / "Inner" / "Deep"
        deep.mkdir(parents=True)
        (self.dir / "keep.txt").write_text("x")
        uninstall.remove_empty_dirs(deep)
        self.assertFalse((self.dir / "Outer").exists())
        self.assertTrue(self.dir.exists())

    def test_recycle_moves_to_bin(self):
        folder = self.dir / "Fake Game"
        folder.mkdir()
        (folder / "a.pak").write_bytes(b"x" * 10)
        self.assertTrue(uninstall.recycle([str(folder)]))
        self.assertFalse(folder.exists())


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.file = Path(self.tmp.name) / "active.json"
        self.patch = mock.patch.object(playtime, "_active_file", lambda: self.file)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_stop_saves_and_restart_closes_old_session(self):
        mon = playtime.PlaytimeMonitor()
        mon.active = {"local:g": time.time() - 600}
        mon.stop()
        self.assertTrue(self.file.exists())
        sessions = []
        with mock.patch.object(playtime, "append_session", lambda *a: sessions.append(a)):
            mon2 = playtime.PlaytimeMonitor()
            mon2._load_resume()
            self.assertIn("local:g", mon2.resume)      # retomável se o jogo ainda estiver aberto
            mon2._close_resumed()                       # jogo não está mais rodando
        self.assertEqual(len(sessions), 1)
        self.assertFalse(self.file.exists())

    def test_old_resume_file_is_closed_right_away(self):
        self.file.write_text(json.dumps({"saved_at": time.time() - 3600, "active": {"local:g": time.time() - 4000}}))
        sessions = []
        with mock.patch.object(playtime, "append_session", lambda *a: sessions.append(a)):
            mon = playtime.PlaytimeMonitor()
            mon._load_resume()
        self.assertEqual(mon.resume, {})
        self.assertEqual(len(sessions), 1)


if __name__ == "__main__":
    unittest.main()

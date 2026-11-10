"""Ouvir junto: dois GameDocks na mesma call, com um relay em memória no lugar do ntfy.sh."""
import time
import unittest
from unittest import mock

import tests  # noqa: F401
from gamedock import listen


class FakeRelay:
    topics: dict = {}

    def __init__(self, topic, on_message):
        self.topic, self.on_message = topic, on_message
        FakeRelay.topics.setdefault(topic, []).append(self)

    def publish(self, payload):
        for relay in FakeRelay.topics.get(self.topic, []):
            relay.on_message(dict(payload))
        return True

    def close(self):
        FakeRelay.topics[self.topic].remove(self)


class FakeMusic:
    def __init__(self, video="", pos=0.0, playing=True):
        self.st = {"open": True, "videoId": video, "time": pos, "playing": playing, "duration": 200, "title": "Song"}
        self.calls = []

    def is_open(self):
        return True

    def open(self, show=False):
        self.calls.append(("open",))

    def state(self):
        return dict(self.st)

    def play(self, video="", playlist=""):
        self.calls.append(("play", video))
        self.st.update(videoId=video, time=0.0)

    def control(self, action, value=None):
        self.calls.append((action, value))
        if action == "seek":
            self.st["time"] = value


class ListenTests(unittest.TestCase):
    def setUp(self):
        FakeRelay.topics = {}
        self.patch = mock.patch.object(listen, "Relay", FakeRelay)
        self.patch.start()
        self.host_music, self.fan_music = FakeMusic("abc", pos=50.0), FakeMusic("")
        self.host = listen.ListenTogether(self.host_music, lambda: {"id": "1", "name": "Host"}, lambda st: None)
        self.fan = listen.ListenTogether(self.fan_music, lambda: {"id": "2", "name": "Fan"}, lambda st: None)
        for side in (self.host, self.fan):
            side._loop_started = True   # sem a thread do laço: os "ticks" são chamados na mão

    def tearDown(self):
        self.patch.stop()

    def test_same_call_same_topic(self):
        self.assertEqual(listen.topic_for("123"), listen.topic_for("123"))
        self.assertNotEqual(listen.topic_for("123"), listen.topic_for("124"))

    def test_host_to_follower_sync(self):
        self.host.on_call("call-1")
        self.fan.on_call("call-1")
        self.assertTrue(self.host.start_hosting()["ok"])
        self.host._host_tick()
        self.assertEqual([h["host"]["id"] for h in self.fan.state()["hosts"]], ["1"])
        self.assertTrue(self.fan.follow("1")["ok"])
        self.fan._follow_tick()                       # 1º: abre a música do host
        self.assertIn(("play", "abc"), self.fan_music.calls)
        self.fan_music.st["duration"] = 200
        self.fan._follow_tick()                       # 2º: pula para a posição do host
        seeks = [c for c in self.fan_music.calls if c[0] == "seek"]
        self.assertTrue(seeks and abs(seeks[-1][1] - 50.0) < 3)
        time.sleep(0.3)                                # o aviso "estou ouvindo" sai numa thread
        self.assertEqual([u["id"] for u in self.host.state()["listeners"]], ["2"])

    def test_leaving_call_stops_everything(self):
        self.host.on_call("call-1")
        self.fan.on_call("call-1")
        self.host.start_hosting()
        self.host._host_tick()
        self.host.on_call(None)                       # host saiu da call: manda "stop"
        self.assertEqual(self.fan.state()["hosts"], [])
        self.assertFalse(self.host.state()["hosting"])

    def test_hosting_needs_call_and_music(self):
        self.assertFalse(self.host.start_hosting()["ok"])
        self.host.on_call("c")
        self.host_music.st["videoId"] = ""
        self.assertFalse(self.host.start_hosting()["ok"])


if __name__ == "__main__":
    unittest.main()

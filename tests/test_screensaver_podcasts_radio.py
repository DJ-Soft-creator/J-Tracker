"""Player: Podcasts, Internetradio und die Abrufe fremder Hosts dahinter."""

import email.message
import importlib.util
import os
import socket
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


FLASK_AVAILABLE = importlib.util.find_spec("flask") is not None
ROOT = Path(__file__).resolve().parents[1]

if FLASK_AVAILABLE:
    os.environ.setdefault("SECRET_KEY", "screensaver-test")
    os.environ.setdefault("DATA_DIR", "/tmp/journl-screensaver-import")
    sys.path.insert(0, str(ROOT / "app"))
    import main
    import screensaver_podcasts
    import screensaver_radio
    import screensaver_remote as remote

FEED_URL = "https://podcast.example/feed.xml"
FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd">
<channel>
  <title>Ruhige Stunde</title>
  <itunes:author>Studio Nord</itunes:author>
  <itunes:image href="https://podcast.example/cover.png"/>
  <item>
    <title>Folge 1</title><guid>f1</guid>
    <pubDate>Mon, 05 Oct 2026 08:00:00 +0000</pubDate>
    <itunes:duration>59:30</itunes:duration>
    <enclosure url="https://cdn.example/f1.mp3" type="audio/mpeg" length="1"/>
    <description>&lt;p&gt;Erste &lt;b&gt;Folge&lt;/b&gt;&lt;/p&gt;</description>
  </item>
  <item>
    <title>Folge 2</title><guid>f2</guid>
    <pubDate>Mon, 12 Oct 2026 08:00:00 +0000</pubDate>
    <itunes:duration>3600</itunes:duration>
    <itunes:image href="https://podcast.example/f2.png"/>
    <enclosure url="https://cdn.example/f2.mp3" type="audio/mpeg" length="1"/>
  </item>
  <item>
    <title>Video</title><guid>v1</guid>
    <enclosure url="https://cdn.example/v1.mp4" type="video/mp4" length="1"/>
  </item>
</channel>
</rss>"""
PNG = b"\x89PNG\r\n\x1a\n" + bytes(32)


class FakeUpstream:
    """Antwort eines fremden Hosts wie ``screensaver_remote.Upstream``."""

    def __init__(self, body=b"", status=200, content_type="audio/mpeg", headers=None, url="https://cdn.example/x"):
        self.body = body
        self.status = status
        self.url = url
        self.headers = email.message.Message()
        self.headers["Content-Type"] = content_type
        for name, value in (headers or {}).items():
            self.headers[name] = value
        self.closed = False
        self._offset = 0

    def content_type(self):
        return self.headers.get_content_type()

    def read1(self, size=32768):
        chunk = self.body[self._offset:self._offset + size]
        self._offset += len(chunk)
        return chunk

    def read_limited(self, limit):
        return self.body[:limit]

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def addresses(*values):
    return [(socket.AF_INET6 if ":" in value else socket.AF_INET, socket.SOCK_STREAM, 6, "", (value, 0))
            for value in values]


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class RemoteGuardTests(unittest.TestCase):
    def test_only_plain_public_http_urls_pass(self):
        for url in ("ftp://example.org/x", "https://user:pw@example.org/", "https://example.org:22/",
                    "javascript:alert(1)", "", "https://"):
            with self.subTest(url=url), self.assertRaises(remote.RemoteError):
                remote.check_url(url)
        self.assertEqual(remote.check_url("http://radio.example:8000/live").port, 8000)

    def test_internal_addresses_are_refused_even_when_only_one_of_them_is(self):
        for values in (("127.0.0.1",), ("10.0.0.5",), ("192.168.1.2",), ("169.254.169.254",), ("::1",),
                       ("::ffff:127.0.0.1",), ("93.184.216.34", "10.0.0.1"), ("100.64.0.1",)):
            with self.subTest(values=values), mock.patch("socket.getaddrinfo", return_value=addresses(*values)):
                with self.assertRaises(remote.RemoteError):
                    remote.resolve("host.example")
        with mock.patch("socket.getaddrinfo", return_value=addresses("93.184.216.34")):
            self.assertEqual(remote.resolve("host.example"), "93.184.216.34")

    def test_every_redirect_is_checked_again(self):
        class Connection:
            def __init__(self, host, port, address, timeout):
                self.host = host

            def request(self, method, target, headers):
                self.target = target

            def getresponse(self):
                response = mock.Mock(status=302)
                response.getheader.return_value = "http://intern.example/admin"
                return response

            def close(self):
                pass

        def fake_resolve(host):
            if host == "intern.example":
                raise remote.RemoteError("Interne oder lokale Adressen sind nicht erlaubt")
            return "93.184.216.34"

        with mock.patch.object(remote, "_HTTPS", Connection), mock.patch.object(remote, "resolve", fake_resolve):
            with self.assertRaisesRegex(remote.RemoteError, "Interne"):
                remote.open_url("https://public.example/start")

    def test_stream_slots_are_limited_per_account_and_in_total(self):
        slots = remote.StreamSlots(per_user=2, total=3)
        self.assertTrue(slots.acquire("a"))
        self.assertTrue(slots.acquire("a"))
        self.assertFalse(slots.acquire("a"))
        self.assertTrue(slots.acquire("b"))
        self.assertFalse(slots.acquire("c"))
        slots.release("a")
        self.assertTrue(slots.acquire("c"))


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class FeedParserTests(unittest.TestCase):
    def test_episodes_come_newest_first_with_duration_and_without_video(self):
        feed = screensaver_podcasts.parse_feed(FEED, FEED_URL)
        self.assertEqual(feed["title"], "Ruhige Stunde")
        self.assertEqual(feed["author"], "Studio Nord")
        self.assertEqual([episode["title"] for episode in feed["episodes"]], ["Folge 2", "Folge 1"])
        self.assertEqual(feed["episodes"][1]["duration"], 3570)
        self.assertEqual(feed["episodes"][1]["summary"], "Erste Folge")
        self.assertEqual(feed["episodes"][0]["image"], "https://podcast.example/f2.png")

    def test_entity_declarations_and_non_rss_are_refused(self):
        bomb = b'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY a "aaaa">]><rss><channel></channel></rss>'
        with self.assertRaises(remote.RemoteError):
            screensaver_podcasts.parse_feed(bomb, FEED_URL)
        with self.assertRaises(remote.RemoteError):
            screensaver_podcasts.parse_feed(b"<html><body>Hallo</body></html>", FEED_URL)

    def test_episode_ids_follow_the_guid_not_the_audio_url(self):
        first = screensaver_podcasts.parse_feed(FEED, FEED_URL)["episodes"][1]["id"]
        moved = screensaver_podcasts.parse_feed(FEED.replace(b"cdn.example/f1.mp3", b"neu.example/f1.mp3"), FEED_URL)
        self.assertEqual(moved["episodes"][1]["id"], first)


@unittest.skipUnless(FLASK_AVAILABLE, "Flask is not installed")
class PodcastRadioApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name)
        self.previous = main.DATA_DIR
        main.DATA_DIR = self.data
        main.app.config["TESTING"] = True
        self.client = main.app.test_client()
        self.user = {"id": "user-1", "username": "david"}
        self.original_current_user = main._current_user
        self.original_update_user = main.update_user
        main._current_user = lambda: self.user
        main.update_user = lambda user: self.user.update(user)
        with self.client.session_transaction() as session:
            session["csrf_token"] = "token"
        self.headers = {"X-CSRF-Token": "token"}
        self.fetch = mock.patch.object(remote, "fetch", side_effect=self._fake_fetch)
        self.fetch.start()
        self.fetched = []

    def tearDown(self):
        self.fetch.stop()
        main.DATA_DIR = self.previous
        main._current_user = self.original_current_user
        main.update_user = self.original_update_user
        self.temp.cleanup()

    def _fake_fetch(self, url, limit, headers=None):
        self.fetched.append(url)
        if url == FEED_URL:
            return FEED, "application/rss+xml", url
        if url.endswith(".png") or url.endswith(".jpg"):
            return PNG, "image/png", url
        raise remote.RemoteError("Server nicht erreichbar")

    def _settings(self, **values):
        response = self.client.post("/api/screensaver/player/settings", headers=self.headers, json=values)
        self.assertEqual(response.status_code, 200)
        return response.get_json()["settings"]

    def _subscribe(self):
        response = self.client.post("/api/screensaver/player/podcasts", headers=self.headers, json={"url": FEED_URL})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response.get_json()["feed"]

    # ─── Podcasts ───────────────────────────────────────────────────
    def test_subscribing_lists_the_feed_and_its_episodes_become_tracks(self):
        feed = self._subscribe()
        self.assertEqual((feed["title"], feed["count"], feed["unheard"]), ("Ruhige Stunde", 2, 2))
        self.assertEqual(self.client.post("/api/screensaver/player/podcasts", headers=self.headers,
                                          json={"url": FEED_URL}).status_code, 409)
        self._settings(audio="podcast", podcast=feed["id"])
        payload = self.client.get("/api/screensaver/player/tracks").get_json()
        self.assertEqual(payload["mode"], "podcast")
        self.assertEqual([track["title"] for track in payload["tracks"]], ["Folge 2", "Folge 1"])
        self.assertEqual(payload["tracks"][0]["album"], "Ruhige Stunde")
        self.assertTrue(payload["tracks"][0]["src"].startswith("/api/screensaver/player/podcasts/episode/"))
        self.assertNotIn("cdn.example", str(payload))

    def test_refused_feeds_are_not_subscribed(self):
        response = self.client.post("/api/screensaver/player/podcasts", headers=self.headers,
                                    json={"url": "https://nirgends.example/feed"})
        self.assertEqual(response.status_code, 400)
        response = self.client.post("/api/screensaver/player/podcasts", headers=self.headers,
                                    json={"url": "file:///etc/passwd"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get("/api/screensaver/player/podcasts").get_json()["feeds"], [])

    def test_progress_is_kept_per_episode_and_95_percent_counts_as_heard(self):
        feed = self._subscribe()
        self._settings(audio="podcast", podcast=feed["id"])
        first, second = self.client.get("/api/screensaver/player/tracks").get_json()["tracks"]
        for track, position in ((first, 600), (second, 3400)):
            response = self.client.put("/api/screensaver/player/state", headers=self.headers, json={
                "mode": "podcast", "track": track["id"], "position": position, "duration": 3570})
            self.assertEqual(response.status_code, 200)
        payload = self.client.get("/api/screensaver/player/tracks").get_json()
        tracks = {track["id"]: track for track in payload["tracks"]}
        self.assertEqual((tracks[first["id"]]["position"], tracks[first["id"]]["done"]), (600, False))
        self.assertEqual((tracks[second["id"]]["position"], tracks[second["id"]]["done"]), (0, True))
        self.assertEqual(payload["state"]["track"], second["id"])
        # Die Musik behaelt ihren eigenen Stand.
        self.assertEqual(self.client.get("/api/screensaver/player/settings").status_code, 200)
        self._settings(audio="music")
        self.assertEqual(self.client.get("/api/screensaver/player/tracks").get_json()["state"]["track"], "")

    def test_rate_is_remembered_per_podcast_and_clamped(self):
        feed = self._subscribe()
        response = self.client.patch(f"/api/screensaver/player/podcasts/{feed['id']}", headers=self.headers,
                                     json={"rate": 5})
        self.assertEqual(response.get_json()["feed"]["rate"], 2.0)
        self._settings(audio="podcast", podcast=feed["id"])
        self.assertEqual(self.client.get("/api/screensaver/player/tracks").get_json()["rate"], 2.0)

    def test_episodes_are_relayed_with_range_and_only_from_own_feeds(self):
        feed = self._subscribe()
        self._settings(audio="podcast", podcast=feed["id"])
        track = self.client.get("/api/screensaver/player/tracks").get_json()["tracks"][0]
        upstream = FakeUpstream(b"x" * 100, status=206, headers={
            "Content-Range": "bytes 100-199/5000", "Content-Length": "100", "Accept-Ranges": "bytes"})
        with mock.patch.object(remote, "open_url", return_value=upstream) as opened:
            response = self.client.get(track["src"], headers={"Range": "bytes=100-199"})
            self.assertEqual(response.status_code, 206)
            self.assertEqual(response.headers["Content-Range"], "bytes 100-199/5000")
            self.assertEqual(response.data, b"x" * 100)
            response.close()
        self.assertEqual(opened.call_args.args[0], "https://cdn.example/f2.mp3")
        self.assertEqual(opened.call_args.kwargs["headers"]["Range"], "bytes=100-199")
        self.assertTrue(upstream.closed)
        self.assertEqual(remote.SLOTS.open("user-1"), 0)
        # Fremde und erfundene Kennungen kommen nicht durch.
        self.assertEqual(self.client.get("/api/screensaver/player/podcasts/episode/" + "a" * 40).status_code, 404)
        self.user = {"id": "user-2", "username": "gast"}
        self.assertEqual(self.client.get(track["src"]).status_code, 404)

    def test_unsubscribing_drops_the_episodes(self):
        feed = self._subscribe()
        self._settings(audio="podcast", podcast=feed["id"])
        self.assertEqual(self.client.delete(f"/api/screensaver/player/podcasts/{feed['id']}",
                                            headers=self.headers).status_code, 200)
        payload = self.client.get("/api/screensaver/player/tracks").get_json()
        self.assertEqual(payload["tracks"], [])
        self.assertEqual(payload["missing"], [feed["id"]])

    def test_artwork_is_served_only_for_own_feeds(self):
        feed = self._subscribe()
        cover = self.client.get(feed["cover"])
        self.assertEqual((cover.status_code, cover.mimetype, cover.data), (200, "image/png", PNG))
        cover.close()
        self.assertEqual(self.client.get("/api/screensaver/player/art/" + "b" * 40).status_code, 404)
        self.user = {"id": "user-2", "username": "gast"}
        self.assertEqual(self.client.get(feed["cover"]).status_code, 404)

    # ─── Radio ──────────────────────────────────────────────────────
    def test_curated_stations_come_first_and_are_tracks_without_a_duration(self):
        self._settings(audio="radio")
        payload = self.client.get("/api/screensaver/player/tracks").get_json()
        labels = [track["title"] for track in payload["tracks"]]
        for name in ("Groove Salad", "Drone Zone", "Radio Swiss Classic", "Radio Swiss Jazz", "BR-Klassik",
                     "NDR Kultur", "SWR Kultur"):
            self.assertIn(name, labels)
        self.assertTrue(all(track["live"] for track in payload["tracks"]))
        self.assertNotIn("somafm.com", str(payload))

    def test_the_chosen_station_is_where_playback_starts(self):
        stations = self.client.get("/api/screensaver/player/radio").get_json()["stations"]
        self._settings(audio="radio", station=stations[4]["id"])
        self.assertEqual(self.client.get("/api/screensaver/player/tracks").get_json()["state"]["track"],
                         stations[4]["id"])

    def test_custom_stations_are_tested_once_and_belong_to_their_account(self):
        with mock.patch.object(screensaver_radio, "open_stream", return_value=FakeUpstream()):
            response = self.client.post("/api/screensaver/player/radio", headers=self.headers,
                                        json={"label": "Mein Sender", "url": "https://radio.example:8000/live"})
        self.assertEqual(response.status_code, 201)
        own = response.get_json()["station"]
        self.assertTrue(own["custom"])
        with mock.patch.object(screensaver_radio, "open_stream", side_effect=remote.RemoteError("kein Stream")):
            refused = self.client.post("/api/screensaver/player/radio", headers=self.headers,
                                       json={"url": "https://radio.example/kaputt"})
        self.assertEqual(refused.status_code, 400)
        curated = self.client.get("/api/screensaver/player/radio").get_json()["stations"][0]
        self.assertEqual(self.client.delete(f"/api/screensaver/player/radio/{curated['id']}",
                                            headers=self.headers).status_code, 404)
        self.user = {"id": "user-2", "username": "gast"}
        self.assertNotIn(own["id"], [entry["id"] for entry in
                                     self.client.get("/api/screensaver/player/radio").get_json()["stations"]])

    def test_streams_are_relayed_without_range_and_release_their_slot(self):
        station = self.client.get("/api/screensaver/player/radio").get_json()["stations"][0]
        upstream = FakeUpstream(b"mp3" * 1000)
        with mock.patch.object(remote, "open_url", return_value=upstream) as opened:
            response = self.client.get(f"/api/screensaver/player/radio/{station['id']}/stream",
                                       headers={"Range": "bytes=0-"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.mimetype, "audio/mpeg")
            self.assertEqual(response.data, b"mp3" * 1000)
            response.close()
        self.assertNotIn("Range", opened.call_args.kwargs["headers"])
        self.assertTrue(upstream.closed)
        self.assertEqual(remote.SLOTS.open("user-1"), 0)

    def test_too_many_streams_are_refused(self):
        station = self.client.get("/api/screensaver/player/radio").get_json()["stations"][0]
        with mock.patch.object(remote.SLOTS, "acquire", return_value=False):
            response = self.client.get(f"/api/screensaver/player/radio/{station['id']}/stream")
        self.assertEqual(response.status_code, 429)

    def test_playlists_are_resolved_and_hls_is_refused(self):
        playlist = FakeUpstream(b"[playlist]\nFile1=https://stream.example/live.mp3\n",
                                content_type="audio/x-scpls", url="https://radio.example/listen.pls")
        stream = FakeUpstream(b"mp3")
        with mock.patch.object(remote, "open_url", side_effect=[playlist, stream]) as opened:
            self.assertIs(screensaver_radio.open_stream("https://radio.example/listen.pls"), stream)
        self.assertEqual(opened.call_args.args[0], "https://stream.example/live.mp3")
        hls = FakeUpstream(b"#EXTM3U\n#EXT-X-VERSION:3\nchunk.m3u8\n", content_type="application/vnd.apple.mpegurl",
                           url="https://radio.example/live.m3u8")
        with mock.patch.object(remote, "open_url", return_value=hls), self.assertRaisesRegex(remote.RemoteError, "HLS"):
            screensaver_radio.open_stream("https://radio.example/live.m3u8")

    # ─── Presets und alter Stand ────────────────────────────────────
    def test_presets_carry_the_audio_source(self):
        feed = self._subscribe()
        self._settings(audio="podcast", podcast=feed["id"])
        created = self.client.post("/api/screensaver/presets", headers=self.headers, json={"label": "Abend"})
        self.assertEqual(created.status_code, 201)
        stored = self.user["screensaver_presets"][0]["player"]
        self.assertEqual((stored["audio"], stored["podcast"]), ("podcast", feed["id"]))
        self._settings(audio="music")
        applied = self.client.post("/api/screensaver/presets/abend/apply", headers=self.headers).get_json()
        self.assertEqual((applied["player"]["audio"], applied["player"]["podcast"]), ("podcast", feed["id"]))

    def test_the_state_file_from_before_podcasts_still_resumes_music(self):
        state = self.data / "user-1" / "screensaver" / "player.json"
        state.parent.mkdir(parents=True)
        state.write_text('{"track": "' + "c" * 40 + '", "position": 42.0, "updated": 1}')
        payload = self.client.get("/api/screensaver/player/tracks").get_json()
        self.assertEqual((payload["state"]["track"], payload["state"]["position"]), ("c" * 40, 42.0))


if __name__ == "__main__":
    unittest.main()

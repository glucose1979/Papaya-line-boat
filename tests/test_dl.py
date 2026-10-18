import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LINE_CHANNEL_SECRET", "test-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OWNER_USER_ID", "Uowner")
os.environ.setdefault("TARGET_GROUP_ID", "Cgroup")

import app
import video_download


PUBLIC_IP = "93.184.216.34"


def public_resolve(hostname, port):

    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (PUBLIC_IP, 0))]


def private_resolve(hostname, port):

    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]


class DownloadPageTests(unittest.TestCase):

    def setUp(self):
        video_download.reset_state()
        self.client = app.app.test_client()
        self._password_backup = os.environ.get("FIX_PASSWORD")
        os.environ.pop("FIX_PASSWORD", None)

    def tearDown(self):
        video_download.reset_state()
        if self._password_backup is None:
            os.environ.pop("FIX_PASSWORD", None)
        else:
            os.environ["FIX_PASSWORD"] = self._password_backup

    def _inline_spawn(self, target, args):
        target(*args)
        return None

    def test_page_is_large_traditional_chinese_and_unlisted(self):
        page = self.client.get("/dl")
        home = self.client.get("/")
        body = page.get_data(as_text=True)

        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.content_type)
        self.assertIn("noindex", page.headers.get("X-Robots-Tag", ""))
        self.assertIn("nofollow", page.headers.get("X-Robots-Tag", ""))
        self.assertIn('content="noindex, nofollow, noarchive"', body)
        self.assertIn("下載影片", body)
        self.assertIn("影片連結", body)
        self.assertIn("密碼", body)
        self.assertIn(">下載</button>", body)
        self.assertIn("localStorage", body)
        self.assertIn("dl-password", body)
        self.assertIn("font-size: 22px", body)
        self.assertIn("#c40000", body)
        self.assertIn("YouTube", body)
        self.assertIn("Instagram", body)
        self.assertIn("TikTok", body)
        self.assertIn("Facebook", body)
        self.assertIn("connect-src 'self'", page.headers.get("Content-Security-Policy", ""))
        self.assertNotIn("/webhook", body)
        self.assertNotIn("/transcribe", body)
        self.assertNotIn("/fix", body)
        self.assertNotIn('href="/"', body)
        self.assertEqual(home.status_code, 200)
        self.assertNotIn("/dl", home.get_data(as_text=True))
        self.assertNotIn("下載", home.get_data(as_text=True))

    def test_password_matches_transcribe_failures(self):
        missing = self.client.post(
            "/dl/start",
            json={"url": "https://example.com/a", "password": "nope"},
        )
        self.assertEqual(missing.status_code, 403)
        self.assertEqual(missing.get_json(), {"error": "disabled"})

        os.environ["FIX_PASSWORD"] = "secret"
        wrong = self.client.post(
            "/dl/start",
            json={"url": "https://example.com/a", "password": "wrong"},
        )
        absent = self.client.post(
            "/dl/start",
            json={"url": "https://example.com/a"},
        )
        header_only = self.client.post(
            "/dl/start",
            json={"url": "https://example.com/a"},
            headers={"X-Password": "secret"},
        )

        for response in (wrong, absent, header_only):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {"error": "unauthorized"})

        self.assertEqual(video_download._jobs, {})

    def test_rejects_bad_urls_before_download(self):
        os.environ["FIX_PASSWORD"] = "secret"
        calls = []

        def explode(*args, **kwargs):
            calls.append(args)
            raise AssertionError("download should not start")

        with patch.object(video_download, "download_video", side_effect=explode):
            empty = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "  "},
            )
            bad_scheme = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "ftp://example.com/a"},
            )
            loopback = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "http://127.0.0.1/secret"},
            )
            metadata = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "http://169.254.169.254/latest"},
            )
            lan = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "http://10.1.2.3/a"},
            )
            localhost = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "http://localhost/a"},
            )
            creds = self.client.post(
                "/dl/start",
                json={"password": "secret", "url": "https://user:pass@example.com/a"},
            )

        self.assertEqual(empty.status_code, 400)
        self.assertIn("貼上", empty.get_json()["error"])
        self.assertEqual(bad_scheme.status_code, 400)
        self.assertEqual(loopback.status_code, 400)
        self.assertEqual(metadata.status_code, 400)
        self.assertEqual(lan.status_code, 400)
        self.assertEqual(localhost.status_code, 400)
        self.assertEqual(creds.status_code, 400)
        self.assertEqual(calls, [])
        self.assertEqual(
            video_download.validate_url(
                "https://example.com/watch?v=1",
                resolve=public_resolve,
            ),
            "https://example.com/watch?v=1",
        )
        with self.assertRaises(video_download.DlError):
            video_download.validate_url(
                "https://evil.example/a",
                resolve=private_resolve,
            )

    def test_download_returns_utf8_mp4_and_deletes_later(self):
        os.environ["FIX_PASSWORD"] = "secret"
        seen = {}

        def fake(url, workdir, on_progress, ffmpeg_location, deadline):
            seen["url"] = url
            on_progress(40, "正在下載… 40%")
            path = os.path.join(workdir, "media.mp4")
            with open(path, "wb") as handle:
                handle.write(b"fake-mp4")
            open(os.path.join(workdir, "media.part"), "wb").close()
            return path, "診間說明.mp4"

        with patch.object(video_download, "validate_url", side_effect=lambda raw, resolve=None: raw.strip()), \
                patch.object(video_download, "_spawn", side_effect=self._inline_spawn), \
                patch.object(video_download, "download_video", side_effect=fake):
            started = self.client.post(
                "/dl/start",
                json={
                    "password": "secret",
                    "url": "https://example.com/watch?v=1",
                },
            )

        self.assertEqual(started.status_code, 200, started.get_data(as_text=True))
        payload = started.get_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(seen["url"], "https://example.com/watch?v=1")
        job_id = payload["job_id"]
        token = payload["token"]

        status = self.client.post(
            "/dl/status",
            json={"password": "secret", "job_id": job_id},
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.get_json()["status"], "ready")
        self.assertEqual(status.get_json()["filename"], "診間說明.mp4")
        self.assertEqual(status.get_json()["progress"], 100)

        downloaded = self.client.get(f"/dl/file/{job_id}?token={token}")
        self.assertEqual(downloaded.status_code, 200)
        self.assertEqual(downloaded.data, b"fake-mp4")
        self.assertEqual(downloaded.mimetype, "video/mp4")
        disposition = downloaded.headers["Content-Disposition"]
        encoded = quote("診間說明.mp4", safe="!#$&+-.^_`|~")
        self.assertIn("attachment", disposition)
        self.assertIn(f"filename*=UTF-8''{encoded}", disposition)
        self.assertIn("noindex", downloaded.headers.get("X-Robots-Tag", ""))
        self.assertIn("no-store", downloaded.headers.get("Cache-Control", ""))
        downloaded.close()

        wrong = self.client.get(f"/dl/file/{job_id}?token=nope")
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(wrong.get_json(), {"error": "unauthorized"})

        missing = self.client.get("/dl/file/not-a-real-job?token=nope")
        self.assertEqual(missing.status_code, 404)

        with video_download._lock:
            workdir = video_download._jobs[job_id]["workdir"]
        self.assertTrue(os.path.isfile(os.path.join(workdir, "media.mp4")))
        self.assertFalse(os.path.exists(os.path.join(workdir, "media.part")))

        video_download._sweep(time.time() + video_download.DL_JOB_TTL_SECONDS + 5)
        self.assertFalse(os.path.exists(workdir))
        self.assertNotIn(job_id, video_download._jobs)

    def test_chinese_errors_and_busy_and_logs(self):
        os.environ["FIX_PASSWORD"] = "super-secret-password"
        logged = []

        def fake_print(*args, **kwargs):
            logged.append(" ".join(str(arg) for arg in args))

        def fail(url, workdir, on_progress, ffmpeg_location, deadline):
            with open(os.path.join(workdir, "partial.bin"), "wb") as handle:
                handle.write(b"partial")
            raise video_download.DlError(video_download.MSG_LOGIN, 422)

        with patch("builtins.print", side_effect=fake_print), \
                patch.object(video_download, "validate_url", side_effect=lambda raw, resolve=None: raw.strip()), \
                patch.object(video_download, "_spawn", side_effect=self._inline_spawn), \
                patch.object(video_download, "download_video", side_effect=fail):
            started = self.client.post(
                "/dl/start",
                json={
                    "password": "super-secret-password",
                    "url": "https://example.com/private-video?token=abc",
                },
            )

        self.assertEqual(started.status_code, 200)
        job_id = started.get_json()["job_id"]
        status = self.client.post(
            "/dl/status",
            json={"password": "super-secret-password", "job_id": job_id},
        )
        self.assertEqual(status.get_json()["ok"], False)
        self.assertEqual(status.get_json()["error"], video_download.MSG_LOGIN)
        joined = "\n".join(logged)
        self.assertNotIn("super-secret-password", joined)
        self.assertNotIn("private-video", joined)
        self.assertIn("host=example.com", joined)
        with video_download._lock:
            self.assertIsNone(video_download._jobs[job_id]["workdir"])

        os.environ["FIX_PASSWORD"] = "secret"
        with video_download._lock:
            video_download._jobs["busy-job"] = {
                "token": "t",
                "status": "downloading",
                "progress": 1,
                "message": "正在下載，請稍候…",
                "error": None,
                "path": None,
                "filename": None,
                "workdir": None,
                "created_at": time.time(),
                "bytes": None,
            }
        busy = self.client.post(
            "/dl/start",
            json={"password": "secret", "url": "https://example.com/other"},
        )
        self.assertEqual(busy.status_code, 429)
        self.assertIn("另一部", busy.get_json()["error"])

    def test_error_mapping_and_format_and_names(self):
        self.assertEqual(
            video_download.map_download_error(
                "ERROR: Unsupported URL: https://example.com/private-login-blocked"
            ),
            video_download.MSG_UNSUPPORTED,
        )
        self.assertEqual(
            video_download.map_download_error(
                "Sign in to confirm you’re not a bot"
            ),
            video_download.MSG_BLOCKED,
        )
        self.assertEqual(
            video_download.map_download_error(
                "ERROR: unable to download video data: HTTP Error 403: Forbidden"
            ),
            video_download.MSG_BLOCKED,
        )
        self.assertEqual(
            video_download.map_download_error(
                "Private video. Please sign in to watch."
            ),
            video_download.MSG_LOGIN,
        )
        self.assertEqual(
            video_download.map_download_error(
                "This video is age-restricted. Confirm your age."
            ),
            video_download.MSG_AGE,
        )
        self.assertEqual(
            video_download.map_download_error(
                "Video unavailable in your country (geo restricted)"
            ),
            video_download.MSG_GEO,
        )
        self.assertEqual(
            video_download.map_download_error(
                "File is larger than max-filesize (999 bytes > 10 bytes)"
            ),
            video_download.MSG_TOO_BIG,
        )
        self.assertEqual(
            video_download.map_download_error(
                "Requested format is not available"
            ),
            video_download.MSG_NO_MP4,
        )
        self.assertEqual(
            video_download.map_download_error("urlopen error timed out"),
            video_download.MSG_TIMEOUT,
        )
        self.assertEqual(
            video_download.map_download_error("This live event will begin soon"),
            video_download.MSG_LIVE,
        )
        self.assertEqual(
            video_download.map_download_error(
                "Unable to download webpage: HTTP Error 404: Not Found"
            ),
            video_download.MSG_UNAVAILABLE,
        )
        self.assertEqual(
            video_download.map_download_error("something else went wrong"),
            video_download.MSG_GENERIC,
        )
        self.assertNotIn("something else", video_download.MSG_GENERIC)

        self.assertEqual(
            video_download.precheck_message(
                {"_type": "video", "duration": video_download.DL_MAX_DURATION_SECONDS + 1},
                incomplete=False,
            ),
            video_download.MSG_TOO_LONG,
        )
        self.assertIsNone(
            video_download.precheck_message(
                {"_type": "video", "duration": 30},
                incomplete={"ext", "vcodec"},
            )
        )
        self.assertEqual(
            video_download.precheck_message(
                {"_type": "video", "is_live": True, "duration": 10},
                incomplete=True,
            ),
            video_download.MSG_LIVE,
        )
        self.assertEqual(
            video_download.precheck_message({"_type": "playlist"}, incomplete=False),
            video_download.MSG_PLAYLIST,
        )

        self.assertEqual(video_download.download_filename("診間說明"), "診間說明.mp4")
        self.assertEqual(video_download.download_filename("a/b:c*?"), "a b c.mp4")
        self.assertEqual(video_download.download_filename("   "), "影片.mp4")
        self.assertLessEqual(len(video_download.download_filename("名" * 80)), 64)

        logger = video_download._QuietLog()
        merged = video_download.build_ydl_opts(
            tempfile.gettempdir(),
            "/usr/bin/ffmpeg",
            lambda *args: None,
            time.monotonic() + 30,
            logger,
        )
        single = video_download.build_ydl_opts(
            tempfile.gettempdir(),
            None,
            lambda *args: None,
            time.monotonic() + 30,
            logger,
        )
        self.assertIn("avc1", merged["format"])
        self.assertIn("m4a", merged["format"])
        self.assertIn("+", merged["format"])
        self.assertEqual(merged["merge_output_format"], "mp4")
        self.assertEqual(merged["ffmpeg_location"], "/usr/bin/ffmpeg")
        self.assertNotIn("+", single["format"])
        self.assertNotIn("merge_output_format", single)
        self.assertTrue(single["format"].startswith("b[ext=mp4]"))
        self.assertFalse(merged["geo_bypass"])
        self.assertTrue(merged["noplaylist"])
        self.assertEqual(
            merged["match_filter"](
                {
                    "_type": "video",
                    "duration": video_download.DL_MAX_DURATION_SECONDS + 5,
                },
                incomplete={"ext"},
            ),
            video_download.MSG_TOO_LONG,
        )
        progress = {}

        def remember(percent, message):
            progress["percent"] = percent
            progress["message"] = message

        past = video_download.build_ydl_opts(
            tempfile.gettempdir(),
            None,
            remember,
            time.monotonic() - 1,
            logger,
        )
        with self.assertRaises(Exception) as caught:
            past["progress_hooks"][0]({
                "status": "downloading",
                "downloaded_bytes": 4,
                "total_bytes": 10,
            })
        self.assertIn("太久", str(caught.exception))

        active = video_download.build_ydl_opts(
            tempfile.gettempdir(),
            None,
            remember,
            time.monotonic() + 30,
            logger,
        )
        active["progress_hooks"][0]({
            "status": "downloading",
            "downloaded_bytes": 4,
            "total_bytes": 10,
        })
        self.assertEqual(progress["percent"], 40)
        self.assertIn("40%", progress["message"])

        import yt_dlp

        with yt_dlp.YoutubeDL(merged) as ydl:
            self.assertEqual(ydl.params["merge_output_format"], "mp4")
            self.assertIn("avc1", ydl.params["format"])

    def test_transcribe_route_still_rejects_missing_password(self):
        response = self.client.post(
            "/transcribe",
            data={"password": "x", "file": (io.BytesIO(b"abc"), "note.mp3")},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "disabled"})


class DirectMp4DownloadTests(unittest.TestCase):

    def test_ytdlp_saves_a_single_mp4(self):
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            self.skipTest("ffmpeg is not installed")

        work = tempfile.mkdtemp(prefix="dl-test-")
        sample = os.path.join(work, "clip.mp4")
        out = tempfile.mkdtemp(prefix="dl-out-")

        try:
            built = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x120:r=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=44100",
                    "-t",
                    "1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "baseline",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "64k",
                    "-shortest",
                    sample,
                ],
                capture_output=True,
                timeout=30,
            )
            if built.returncode != 0 or not os.path.isfile(sample):
                self.skipTest("could not build a sample mp4")

            payload = Path(sample).read_bytes()

            class Handler(BaseHTTPRequestHandler):

                def do_GET(self):
                    self.send_response(200)
                    self.send_header("Content-Type", "video/mp4")
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

                def log_message(self, format, *args):
                    return

            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_address[1]

            try:
                path, filename = video_download.download_video(
                    f"http://127.0.0.1:{port}/clip.mp4",
                    out,
                    lambda *args: None,
                    ffmpeg,
                    time.monotonic() + 60,
                )
            finally:
                server.shutdown()
                server.server_close()

            self.assertTrue(filename.endswith(".mp4"))
            self.assertTrue(os.path.isfile(path))
            self.assertGreater(os.path.getsize(path), 100)
            self.assertTrue(path.endswith(".mp4"))
            names = os.listdir(out)
            self.assertEqual(names, [os.path.basename(path)])

        finally:
            shutil.rmtree(work, ignore_errors=True)
            shutil.rmtree(out, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

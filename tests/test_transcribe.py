import io
import os
import re
import sys
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LINE_CHANNEL_SECRET", "test-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OWNER_USER_ID", "Uowner")
os.environ.setdefault("TARGET_GROUP_ID", "Cgroup")

import app


class FakeTranscription:

    def __init__(self, text, seconds=1.5):
        self.text = text
        self.usage = {
            "type": "duration",
            "seconds": seconds,
        }


def wav_bytes(seconds=4, rate=16000):

    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x01" * (rate * seconds))

    return buffer.getvalue()


class TranscribeEndpointTests(unittest.TestCase):

    def setUp(self):
        self.client = app.app.test_client()
        self.calls = []
        self._scripted = []
        self._original_create = app.client.audio.transcriptions.create
        app.client.audio.transcriptions.create = self._create
        self._password_backup = os.environ.get("FIX_PASSWORD")
        self._model_backup = os.environ.get("TRANSCRIBE_MODEL")
        os.environ.pop("FIX_PASSWORD", None)
        os.environ.pop("TRANSCRIBE_MODEL", None)
        self._max_content = app.app.config["MAX_CONTENT_LENGTH"]

    def tearDown(self):
        app.client.audio.transcriptions.create = self._original_create
        app.app.config["MAX_CONTENT_LENGTH"] = self._max_content
        if self._password_backup is None:
            os.environ.pop("FIX_PASSWORD", None)
        else:
            os.environ["FIX_PASSWORD"] = self._password_backup
        if self._model_backup is None:
            os.environ.pop("TRANSCRIBE_MODEL", None)
        else:
            os.environ["TRANSCRIBE_MODEL"] = self._model_backup

    def _create(self, **kwargs):
        recorded = {
            key: value
            for key, value in kwargs.items()
            if key != "file"
        }
        upload = kwargs.get("file")
        if isinstance(upload, tuple):
            recorded["filename"] = upload[0]
        self.calls.append(recorded)
        if not self._scripted:
            raise AssertionError("unexpected OpenAI call")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def _post(self, data=None, **kwargs):
        return self.client.post("/transcribe", data=data, **kwargs)

    def test_options_preflight_matches_fix_cors(self):
        response = self.client.open(
            "/transcribe",
            method="OPTIONS",
            headers={
                "Origin": app.FIX_ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN,
        )
        self.assertIn(
            "POST",
            response.headers.get("Access-Control-Allow-Methods", ""),
        )
        self.assertIn(
            "OPTIONS",
            response.headers.get("Access-Control-Allow-Methods", ""),
        )
        self.assertIn(
            "Content-Type",
            response.headers.get("Access-Control-Allow-Headers", ""),
        )

    def test_disabled_when_password_unset(self):
        response = self._post(
            data={
                "password": "anything",
                "lang": "zh",
                "file": (io.BytesIO(b"abc"), "note.mp3"),
            }
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "disabled"})
        self.assertEqual(self.calls, [])
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN,
        )

    def test_wrong_or_missing_password_matches_fix(self):
        os.environ["FIX_PASSWORD"] = "secret"

        wrong = self._post(
            data={
                "password": "wrong",
                "lang": "zh",
                "file": (io.BytesIO(b"abc"), "note.mp3"),
            }
        )
        missing = self._post(
            data={
                "lang": "zh",
                "file": (io.BytesIO(b"abc"), "note.mp3"),
            }
        )
        header_only = self._post(
            data={
                "lang": "zh",
                "file": (io.BytesIO(b"abc"), "note.mp3"),
            },
            headers={"X-Password": "secret"},
        )
        json_body = self.client.post(
            "/transcribe",
            json={
                "password": "secret",
                "lang": "zh",
            },
        )

        for response in (wrong, missing, header_only, json_body):
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.get_json(), {"error": "unauthorized"})
            self.assertEqual(
                response.headers.get("Access-Control-Allow-Origin"),
                app.FIX_ALLOWED_ORIGIN,
            )

        self.assertEqual(self.calls, [])

    def test_success_traditional_chinese(self):
        os.environ["FIX_PASSWORD"] = "secret"
        self._scripted = [FakeTranscription("后面的软件和信息", seconds=3.25)]

        response = self._post(
            data={
                "password": "secret",
                "lang": "zh-TW",
                "file": (io.BytesIO(b"not-real-audio"), "visit.mp3"),
            },
            headers={"Origin": app.FIX_ALLOWED_ORIGIN},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"ok": True, "text": "後面的軟體和資訊"},
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["model"], app.TRANSCRIBE_MODEL_DEFAULT)
        self.assertEqual(self.calls[0]["language"], "zh")
        self.assertEqual(self.calls[0]["response_format"], "json")
        self.assertIn("繁體中文", self.calls[0]["prompt"])
        self.assertEqual(self.calls[0]["filename"], "audio.mp3")
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN,
        )

    def test_english_and_model_override(self):
        os.environ["FIX_PASSWORD"] = "secret"
        os.environ["TRANSCRIBE_MODEL"] = "whisper-1"
        self._scripted = [
            FakeTranscription("hello", seconds=1),
            FakeTranscription("there", seconds=1),
        ]

        response = self._post(
            data={
                "password": "secret",
                "lang": "en-US",
                "file": (io.BytesIO(b"audio-bytes"), "talk.mp3"),
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {"ok": True, "text": "hello"},
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["model"], "whisper-1")
        self.assertEqual(self.calls[0]["language"], "en")
        self.assertEqual(self.calls[0]["response_format"], "verbose_json")
        self.assertNotIn("繁體", self.calls[0]["prompt"])

    def test_omitted_lang_is_auto(self):
        os.environ["FIX_PASSWORD"] = "secret"
        self._scripted = [FakeTranscription("Hello 软件")]

        response = self._post(
            data={
                "password": "secret",
                "file": (io.BytesIO(b"abc"), "note.wav"),
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["text"], "Hello 軟體")
        self.assertNotIn("language", self.calls[0])
        self.assertIn("中文", self.calls[0]["prompt"])

    def test_invalid_lang_missing_file_and_bad_type(self):
        os.environ["FIX_PASSWORD"] = "secret"

        invalid_lang = self._post(
            data={
                "password": "secret",
                "lang": "ja",
                "file": (io.BytesIO(b"abc"), "note.mp3"),
            }
        )
        self.assertEqual(invalid_lang.status_code, 400)
        self.assertEqual(
            invalid_lang.get_json(),
            {"ok": False, "error": "語言請選中文或英文"},
        )

        missing = self._post(data={"password": "secret", "lang": "zh"})
        self.assertEqual(missing.status_code, 400)
        self.assertEqual(
            missing.get_json(),
            {"ok": False, "error": "請上傳錄音檔"},
        )

        bad_type = self._post(
            data={
                "password": "secret",
                "lang": "zh",
                "file": (io.BytesIO(b"abc"), "notes.txt"),
            }
        )
        self.assertEqual(bad_type.status_code, 400)
        self.assertIn("不支援", bad_type.get_json()["error"])

        empty = self._post(
            data={
                "password": "secret",
                "lang": "en",
                "file": (io.BytesIO(b""), "empty.mp3"),
            }
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(
            empty.get_json(),
            {"ok": False, "error": "錄音檔是空的"},
        )
        self.assertEqual(self.calls, [])

    def test_mime_without_extension(self):
        os.environ["FIX_PASSWORD"] = "secret"
        self._scripted = [FakeTranscription("ok")]

        response = self._post(
            data={
                "password": "secret",
                "lang": "en",
                "file": (io.BytesIO(b"webm-bytes"), "blob", "audio/webm"),
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls[0]["filename"], "audio.webm")

    def test_duration_cap_and_missing_ffmpeg(self):
        os.environ["FIX_PASSWORD"] = "secret"

        with patch.object(
            app,
            "_probe_duration_seconds",
            return_value=app.TRANSCRIBE_MAX_SECONDS + 1,
        ):
            too_long = self._post(
                data={
                    "password": "secret",
                    "lang": "zh",
                    "file": (io.BytesIO(b"abc"), "long.mp3"),
                }
            )

        self.assertEqual(too_long.status_code, 413)
        self.assertIn("60 分鐘", too_long.get_json()["error"])

        with patch.object(app, "_ffmpeg_exe", return_value=None), \
                patch.object(app, "TRANSCRIBE_OPENAI_MAX_BYTES", 8):
            too_big = self._post(
                data={
                    "password": "secret",
                    "lang": "zh",
                    "file": (io.BytesIO(b"0123456789abcdef"), "big.mp3"),
                }
            )

        self.assertEqual(too_big.status_code, 413)
        self.assertIn("24 MB", too_big.get_json()["error"])
        self.assertEqual(self.calls, [])

    def test_upload_cap_is_json_only_for_transcribe(self):
        os.environ["FIX_PASSWORD"] = "secret"
        app.app.config["MAX_CONTENT_LENGTH"] = 300

        too_big = self._post(
            data={
                "password": "secret",
                "lang": "zh",
                "file": (io.BytesIO(b"x" * 800), "big.mp3"),
            }
        )
        self.assertEqual(too_big.status_code, 413)
        self.assertEqual(
            too_big.get_json(),
            {"ok": False, "error": "檔案太大，請壓到 100 MB 以內"},
        )
        self.assertEqual(
            too_big.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN,
        )

        other = self.client.post("/webhook", data=b"y" * 800)
        self.assertEqual(other.status_code, 413)
        self.assertNotIn(
            "檔案太大",
            other.get_data(as_text=True),
        )
        self.assertEqual(self.calls, [])

    def test_splits_when_larger_than_openai_limit(self):
        os.environ["FIX_PASSWORD"] = "secret"
        payload = wav_bytes(seconds=4)
        self._scripted = [
            FakeTranscription("后面", seconds=2),
            FakeTranscription("的软件", seconds=2),
            FakeTranscription("extra", seconds=2),
            FakeTranscription("extra", seconds=2),
            FakeTranscription("extra", seconds=2),
        ]

        with patch.object(app, "TRANSCRIBE_OPENAI_MAX_BYTES", 90_000), \
                patch.object(app, "TRANSCRIBE_CHUNK_TARGET_BYTES", 40_000):
            response = self._post(
                data={
                    "password": "secret",
                    "lang": "zh",
                    "file": (io.BytesIO(payload), "visit.wav"),
                }
            )

        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        self.assertGreaterEqual(len(self.calls), 2)
        self.assertEqual(self.calls[0]["language"], "zh")
        self.assertIn("繁體中文", self.calls[0]["prompt"])
        self.assertIn("後面", self.calls[1]["prompt"])
        self.assertNotIn("secret", self.calls[1]["prompt"])
        self.assertEqual(response.get_json()["ok"], True)
        self.assertIn("後面", response.get_json()["text"])
        self.assertIn("軟體", response.get_json()["text"])

    def test_upstream_failure_and_empty_transcript(self):
        os.environ["FIX_PASSWORD"] = "secret"
        self._scripted = [RuntimeError("leak 機密稿")]
        logged = []

        def fake_print(*args, **kwargs):
            logged.append(" ".join(str(arg) for arg in args))

        with patch("builtins.print", side_effect=fake_print):
            failed = self._post(
                data={
                    "password": "secret",
                    "lang": "zh",
                    "file": (io.BytesIO(b"abc"), "note.mp3"),
                }
            )

        self.assertEqual(failed.status_code, 502)
        self.assertEqual(
            failed.get_json(),
            {"ok": False, "error": "語音轉文字失敗，請稍後再試"},
        )
        joined = "\n".join(logged)
        self.assertIn("RuntimeError", joined)
        self.assertNotIn("機密稿", joined)
        self.assertNotIn("secret", joined)

        self._scripted = [FakeTranscription("   ", seconds=0.5)]
        empty = self._post(
            data={
                "password": "secret",
                "lang": "en",
                "file": (io.BytesIO(b"abc"), "note.mp3"),
            }
        )
        self.assertEqual(empty.status_code, 502)
        self.assertEqual(
            empty.get_json(),
            {"ok": False, "error": "沒有辨識到文字"},
        )

    def test_deadline_returns_before_calling_openai(self):
        os.environ["FIX_PASSWORD"] = "secret"

        with patch.object(app, "TRANSCRIBE_DEADLINE_SECONDS", 0):
            response = self._post(
                data={
                    "password": "secret",
                    "lang": "zh",
                    "file": (io.BytesIO(b"abc"), "note.mp3"),
                }
            )

        self.assertEqual(response.status_code, 504)
        self.assertEqual(response.get_json()["ok"], False)
        self.assertEqual(self.calls, [])

    def test_logs_size_duration_and_model_not_text_or_password(self):
        os.environ["FIX_PASSWORD"] = "super-secret-password"
        self._scripted = [FakeTranscription("敏感逐字稿內容", seconds=4.5)]
        logged = []

        def fake_print(*args, **kwargs):
            logged.append(" ".join(str(arg) for arg in args))

        with patch("builtins.print", side_effect=fake_print):
            response = self._post(
                data={
                    "password": "super-secret-password",
                    "lang": "zh",
                    "file": (io.BytesIO(b"1234567890"), "note.m4a"),
                }
            )

        self.assertEqual(response.status_code, 200)
        joined = "\n".join(logged)
        self.assertIn("OpenAI transcribe:", joined)
        self.assertIn("model=gpt-4o-mini-transcribe", joined)
        self.assertIn("bytes=10", joined)
        self.assertIn("duration_sec=4.5", joined)
        self.assertIn("chunks=1", joined)
        self.assertIn("lang=zh", joined)
        self.assertNotIn("super-secret-password", joined)
        self.assertNotIn("敏感逐字稿內容", joined)
        self.assertNotIn("敏感逐字稿", joined)

    def test_traditional_conversion_keeps_existing_wording(self):
        self.assertEqual(
            app._to_traditional_taiwan("后面的软件和皇后"),
            "後面的軟體和皇后",
        )
        self.assertEqual(
            app._to_traditional_taiwan("攝護腺肥大需要治療"),
            "攝護腺肥大需要治療",
        )
        self.assertEqual(
            app._to_traditional_taiwan("Hello world"),
            "Hello world",
        )

    def test_repo_has_no_hardcoded_secrets(self):
        key_re = re.compile(r"sk-[A-Za-z0-9]{20,}")
        pem_re = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
        assigned_re = re.compile(
            r"(OPENAI_API_KEY|FIX_PASSWORD|LINE_CHANNEL_SECRET|"
            r"LINE_CHANNEL_ACCESS_TOKEN|OWNER_USER_ID|TARGET_GROUP_ID)"
            r"\s*=\s*['\"][^'\"]+['\"]"
        )
        checked = 0

        for path in ROOT.rglob("*"):
            if not path.is_file():
                continue
            if any(part in {".git", "__pycache__", "tests"} for part in path.parts):
                continue
            if path.suffix not in {".py", ".md", ".txt", ".json", ".yml", ".yaml"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            checked += 1
            self.assertIsNone(key_re.search(text), path)
            self.assertIsNone(pem_re.search(text), path)
            self.assertIsNone(assigned_re.search(text), path)

        self.assertGreaterEqual(checked, 3)


if __name__ == "__main__":
    unittest.main()

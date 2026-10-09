import os
import sys
import unittest
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


class FakeFixResponse:

    def __init__(
        self,
        text,
        status="completed",
        incomplete_reason=None
    ):
        self.output_text = text
        self.status = status
        self.incomplete_details = (
            None
            if incomplete_reason is None
            else {"reason": incomplete_reason}
        )
        self.usage = {
            "output_tokens": 20,
            "output_tokens_details": {
                "reasoning_tokens": 4
            }
        }
        self.output = [
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": text
                    }
                ]
            }
        ]


class FixEndpointTests(unittest.TestCase):

    def setUp(self):
        self.client = app.app.test_client()
        self.calls = []
        self._scripted = []
        self._original_create = app.client.responses.create
        app.client.responses.create = self._create
        self._password_backup = os.environ.get("FIX_PASSWORD")
        os.environ.pop("FIX_PASSWORD", None)

    def tearDown(self):
        app.client.responses.create = self._original_create
        if self._password_backup is None:
            os.environ.pop("FIX_PASSWORD", None)
        else:
            os.environ["FIX_PASSWORD"] = self._password_backup

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("unexpected OpenAI call")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def test_app_imports(self):
        self.assertTrue(hasattr(app, "fix"))
        self.assertEqual(app.FIX_ALLOWED_ORIGIN, "https://glucose1979.github.io")

    def test_disabled_when_password_unset(self):
        os.environ.pop("FIX_PASSWORD", None)

        response = self.client.post(
            "/fix",
            json={
                "text": "你好",
                "lang": "zh-TW",
                "password": "anything"
            }
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.calls, [])
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN
        )

    def test_wrong_password_is_401(self):
        os.environ["FIX_PASSWORD"] = "secret"

        response = self.client.post(
            "/fix",
            json={
                "text": "你好",
                "lang": "zh-TW",
                "password": "wrong"
            }
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(self.calls, [])
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN
        )

    def test_options_preflight_cors(self):
        response = self.client.open(
            "/fix",
            method="OPTIONS",
            headers={
                "Origin": app.FIX_ALLOWED_ORIGIN,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type"
            }
        )

        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN
        )
        self.assertIn(
            "POST",
            response.headers.get("Access-Control-Allow-Methods", "")
        )
        self.assertIn(
            "Content-Type",
            response.headers.get("Access-Control-Allow-Headers", "")
        )

    def test_empty_and_too_long(self):
        os.environ["FIX_PASSWORD"] = "secret"

        empty = self.client.post(
            "/fix",
            json={
                "text": "   ",
                "lang": "zh-TW",
                "password": "secret"
            }
        )
        self.assertEqual(empty.status_code, 400)
        self.assertEqual(self.calls, [])

        too_long = self.client.post(
            "/fix",
            json={
                "text": "木" * (app.FIX_MAX_CHARS + 1),
                "lang": "zh-TW",
                "password": "secret"
            }
        )
        self.assertEqual(too_long.status_code, 413)
        self.assertEqual(self.calls, [])

    def test_rejects_invalid_lang(self):
        os.environ["FIX_PASSWORD"] = "secret"

        response = self.client.post(
            "/fix",
            json={
                "text": "hello",
                "lang": "ja-JP",
                "password": "secret"
            }
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.calls, [])

    def test_success_and_empty_retry(self):
        os.environ["FIX_PASSWORD"] = "secret"
        self._scripted = [
            FakeFixResponse(
                "",
                status="incomplete",
                incomplete_reason="max_output_tokens"
            ),
            FakeFixResponse("修正後的句子。")
        ]

        response = self.client.post(
            "/fix",
            json={
                "text": "修正前的句子",
                "lang": "zh-TW",
                "password": "secret"
            },
            headers={"Origin": app.FIX_ALLOWED_ORIGIN}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {"text": "修正後的句子。"})
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            self.calls[0]["reasoning"]["effort"],
            app.FIX_REASONING_EFFORT
        )
        self.assertEqual(
            self.calls[1]["reasoning"]["effort"],
            app.FIX_RETRY_REASONING_EFFORT
        )
        self.assertEqual(self.calls[0]["model"], app.FIX_MODEL)
        self.assertNotIn("tools", self.calls[0])
        self.assertEqual(
            response.headers.get("Access-Control-Allow-Origin"),
            app.FIX_ALLOWED_ORIGIN
        )

    def test_password_not_logged(self):
        os.environ["FIX_PASSWORD"] = "super-secret-password"
        self._scripted = [FakeFixResponse("ok")]
        logged = []

        def fake_print(*args, **kwargs):
            logged.append(" ".join(str(a) for a in args))

        with patch("builtins.print", side_effect=fake_print):
            response = self.client.post(
                "/fix",
                json={
                    "text": "sensitive transcript content",
                    "lang": "en-US",
                    "password": "super-secret-password"
                }
            )

        self.assertEqual(response.status_code, 200)
        joined = "\n".join(logged)
        self.assertNotIn("super-secret-password", joined)
        self.assertNotIn("sensitive transcript content", joined)


if __name__ == "__main__":
    unittest.main()

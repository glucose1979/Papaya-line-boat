import base64
import hashlib
import hmac
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("LINE_CHANNEL_SECRET", "test-secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "test-token")
os.environ.setdefault("OPENAI_API_KEY", "test-key")
os.environ.setdefault("OWNER_USER_ID", "Uowner")
os.environ.setdefault("TARGET_GROUP_ID", "Cgroup")

import app


class FakeResponse:

    def __init__(
        self,
        text,
        output=None,
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
            "output_tokens": 12,
            "output_tokens_details": {
                "reasoning_tokens": 3
            }
        }
        if output is None:
            output = [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": text,
                            "annotations": []
                        }
                    ]
                }
            ]
        self.output = output


class FakeHttpResponse:

    def raise_for_status(self):
        return None


class ChatTests(unittest.TestCase):

    def setUp(self):
        app.clear_chat_memory()
        self.calls = []
        self.http_calls = []
        self._original_create = app.client.responses.create
        self._original_post = app.requests.post
        app.client.responses.create = self._create
        app.requests.post = self._post
        self._scripted = []

    def tearDown(self):
        app.client.responses.create = self._original_create
        app.requests.post = self._original_post
        app.clear_chat_memory()

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("unexpected OpenAI call")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def _post(self, url, headers=None, json=None, timeout=None):
        self.http_calls.append({
            "url": url,
            "json": json
        })
        return FakeHttpResponse()

    def _script(self, *responses):
        self._scripted.extend(responses)

    def test_follow_up_uses_earlier_context(self):
        self._script(
            FakeResponse("請問是哪一家醫院？"),
            FakeResponse(
                "林口長庚泌尿科，侯鎮邦週三上午有門診。"
            )
        )

        first = app.chat_with_papaya(
            "幫我查侯鎮邦醫師的門診時間",
            "Uowner"
        )
        second = app.chat_with_papaya(
            "林口長庚醫院泌尿科",
            "Uowner"
        )

        self.assertEqual(first, "請問是哪一家醫院？")
        self.assertIn("侯鎮邦", second)

        follow_up = self.calls[1]["input"]
        roles = [item["role"] for item in follow_up]
        contents = [item["content"] for item in follow_up]

        self.assertEqual(
            roles,
            ["user", "assistant", "user"]
        )
        self.assertIn("侯鎮邦醫師的門診時間", contents[0])
        self.assertIn("哪一家醫院", contents[1])
        self.assertEqual(contents[2], "林口長庚醫院泌尿科")
        self.assertIn("木瓜姐", self.calls[1]["instructions"])
        self.assertIn(
            "不要重複追問",
            self.calls[1]["instructions"]
        )

    def test_other_user_does_not_see_history(self):
        self._script(
            FakeResponse("請問是哪一家醫院？"),
            FakeResponse("你是指誰？")
        )

        app.chat_with_papaya(
            "幫我查侯鎮邦醫師的門診時間",
            "Uowner"
        )
        app.chat_with_papaya(
            "林口長庚醫院泌尿科",
            "Usomeone"
        )

        follow_up = self.calls[1]["input"]
        self.assertEqual(len(follow_up), 1)
        self.assertEqual(
            follow_up[0]["content"],
            "林口長庚醫院泌尿科"
        )

    def test_history_expires_after_inactivity(self):
        self._script(
            FakeResponse("請問是哪一家醫院？"),
            FakeResponse("這是新的話題。")
        )

        app.chat_with_papaya(
            "幫我查侯鎮邦醫師的門診時間",
            "Uowner"
        )

        app._chat_memory["Uowner"]["updated_at"] = (
            app.time.time()
            - app.CHAT_MEMORY_TTL_SECONDS
            - 5
        )

        app.chat_with_papaya(
            "林口長庚醫院泌尿科",
            "Uowner"
        )

        follow_up = self.calls[1]["input"]
        self.assertEqual(
            [item["content"] for item in follow_up],
            ["林口長庚醫院泌尿科"]
        )

    def test_history_keeps_about_ten_exchanges(self):
        replies = [
            FakeResponse(f"回覆{i}")
            for i in range(12)
        ]
        self._script(*replies)

        for i in range(12):
            app.chat_with_papaya(f"問題{i}", "Uowner")

        latest = self.calls[-1]["input"]
        contents = [item["content"] for item in latest]
        self.assertNotIn("問題0", contents)
        self.assertNotIn("回覆0", contents)
        self.assertIn("問題1", contents)
        self.assertIn("回覆10", contents)
        self.assertEqual(contents[-1], "問題11")
        self.assertLessEqual(
            len(latest) - 1,
            app.CHAT_MEMORY_MAX_EXCHANGES * 2
        )

    def test_search_tool_is_optional_and_sources_are_kept(self):
        url = "https://www.cgmh.org.tw/linou/urology"
        self._script(
            FakeResponse(
                "侯鎮邦醫師在林口長庚泌尿科，週三上午有門診。",
                output=[
                    {
                        "type": "web_search_call",
                        "id": "ws_clinic",
                        "status": "completed",
                        "action": {
                            "type": "search",
                            "query": "侯鎮邦 林口長庚 泌尿科 門診"
                        }
                    },
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    "侯鎮邦醫師在林口長庚泌尿科，"
                                    "週三上午有門診。"
                                ),
                                "annotations": [
                                    {
                                        "type": "url_citation",
                                        "url": url,
                                        "title": "林口長庚門診表"
                                    }
                                ]
                            }
                        ]
                    }
                ]
            )
        )

        answer = app.chat_with_papaya(
            "林口長庚醫院泌尿科侯鎮邦的門診時間",
            "Uowner"
        )

        tool = self.calls[0]["tools"][0]
        self.assertEqual(tool["type"], "web_search")
        self.assertEqual(tool["search_context_size"], "low")
        self.assertEqual(self.calls[0]["tool_choice"], "auto")
        self.assertEqual(
            self.calls[0]["max_tool_calls"],
            app.CHAT_MAX_TOOL_CALLS
        )
        self.assertEqual(
            self.calls[0]["reasoning"]["effort"],
            "low"
        )
        self.assertEqual(
            self.calls[0]["max_output_tokens"],
            2000
        )
        self.assertIn("週三上午", answer)
        self.assertIn(url, answer)
        self.assertIn("林口長庚門診表", answer)
        self.assertIn("來源：", answer)

    def test_markdown_citation_becomes_plain_url(self):
        url = "https://news.example/today"
        raw = f"今天有一則好消息 [今日新聞]({url})。"
        annotation = type("Annotation", (), {
            "type": "url_citation",
            "url": url,
            "title": "今日新聞"
        })()
        part = type("Part", (), {
            "type": "output_text",
            "text": raw,
            "annotations": [annotation]
        })()
        message = type("Message", (), {
            "type": "message",
            "content": [part]
        })()
        search_call = type("SearchCall", (), {
            "type": "web_search_call"
        })()

        self._script(
            FakeResponse(
                raw,
                output=[search_call, message]
            )
        )

        answer = app.chat_with_papaya("今天有什麼新聞", "Uowner")

        self.assertIn(f"今日新聞 {url}", answer)
        self.assertNotIn("[今日新聞]", answer)
        self.assertEqual(answer.count(url), 1)
        self.assertNotIn("來源：", answer)

    def test_empty_reply_retries_with_tools(self):
        self._script(
            FakeResponse(
                "",
                output=[
                    {
                        "type": "web_search_call",
                        "id": "ws_empty",
                        "status": "completed"
                    }
                ],
                status="incomplete",
                incomplete_reason="max_output_tokens"
            ),
            FakeResponse("查到了，週三上午有門診。")
        )

        answer = app.chat_with_papaya(
            "侯鎮邦醫師門診時間",
            "Uowner"
        )

        self.assertEqual(answer, "查到了，週三上午有門診。")
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(
            self.calls[0]["reasoning"]["effort"],
            "low"
        )
        self.assertEqual(self.calls[0]["max_output_tokens"], 2000)
        self.assertEqual(
            self.calls[1]["reasoning"]["effort"],
            "minimal"
        )
        self.assertEqual(self.calls[1]["max_output_tokens"], 3000)
        self.assertEqual(
            self.calls[1]["tools"][0]["type"],
            "web_search"
        )
        self.assertEqual(
            self.calls[0]["input"],
            self.calls[1]["input"]
        )

        history = app.load_chat_history("Uowner")
        self.assertEqual(history[-1]["content"], answer)

    def test_empty_retry_drops_search_if_minimal_rejects_it(self):
        self._script(
            FakeResponse(""),
            Exception(
                "web_search is not supported with minimal reasoning"
            ),
            FakeResponse("沒查到網頁，但我記得你在問門診。")
        )

        answer = app.chat_with_papaya(
            "侯鎮邦醫師門診時間",
            "Uowner"
        )

        self.assertEqual(
            answer,
            "沒查到網頁，但我記得你在問門診。"
        )
        self.assertEqual(len(self.calls), 3)
        self.assertNotIn("tools", self.calls[2])
        self.assertEqual(
            self.calls[2]["reasoning"]["effort"],
            "minimal"
        )
        self.assertEqual(self.calls[2]["max_output_tokens"], 3000)

    def test_still_empty_after_retry_is_not_remembered(self):
        self._script(
            FakeResponse(""),
            FakeResponse("   ")
        )

        answer = app.chat_with_papaya("再試一次", "Uowner")

        self.assertEqual(answer, app.EMPTY_CHAT_REPLY)
        self.assertEqual(app.load_chat_history("Uowner"), [])

    def test_line_reply_respects_length_limit(self):
        short = "短回覆"
        chunks = app.split_line_text(short)
        self.assertEqual(chunks, [short])

        exact = "木" * app.LINE_TEXT_LIMIT
        self.assertEqual(app.split_line_text(exact), [exact])

        long_text = "甲" * (app.LINE_TEXT_LIMIT + 20)
        parts = app.split_line_text(long_text)
        self.assertEqual(len(parts), 2)
        self.assertTrue(
            all(len(part) <= app.LINE_TEXT_LIMIT for part in parts)
        )
        self.assertEqual("".join(parts), long_text)

        huge = "乙" * (app.LINE_TEXT_LIMIT * 6)
        huge_parts = app.split_line_text(huge)
        self.assertEqual(len(huge_parts), app.LINE_MAX_MESSAGES)
        self.assertTrue(
            all(
                len(part) <= app.LINE_TEXT_LIMIT
                for part in huge_parts
            )
        )
        self.assertTrue(
            huge_parts[-1].endswith(app.LINE_TRUNCATION_NOTE.strip())
            or app.LINE_TRUNCATION_NOTE.strip() in huge_parts[-1]
        )

        app.reply_line("reply-token", huge)
        messages = self.http_calls[0]["json"]["messages"]
        self.assertEqual(len(messages), app.LINE_MAX_MESSAGES)
        self.assertTrue(
            all(len(message["text"]) <= 5000 for message in messages)
        )
        self.assertTrue(
            all(message["type"] == "text" for message in messages)
        )


class WebhookTests(unittest.TestCase):

    def setUp(self):
        app.clear_chat_memory()
        self.calls = []
        self.http_calls = []
        self._original_create = app.client.responses.create
        self._original_post = app.requests.post
        app.client.responses.create = self._create
        app.requests.post = self._post
        self._scripted = []
        self.client = app.app.test_client()

    def tearDown(self):
        app.client.responses.create = self._original_create
        app.requests.post = self._original_post
        app.clear_chat_memory()

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("unexpected OpenAI call")
        result = self._scripted.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    def _post(self, url, headers=None, json=None, timeout=None):
        self.http_calls.append({
            "url": url,
            "json": json
        })
        return FakeHttpResponse()

    def _sign(self, body):
        digest = hmac.new(
            app.LINE_CHANNEL_SECRET.encode("utf-8"),
            body,
            hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _post_event(self, event):
        body = json.dumps({"events": [event]}).encode("utf-8")
        return self.client.post(
            "/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Line-Signature": self._sign(body)
            }
        )

    def _text_event(self, text, user_id="Uowner", source_type="user"):
        return {
            "type": "message",
            "replyToken": "reply-token",
            "source": {
                "type": source_type,
                "userId": user_id
            },
            "message": {
                "type": "text",
                "id": "m1",
                "text": text
            }
        }

    def test_webhook_follow_up_keeps_context(self):
        self._scripted.extend([
            FakeResponse("請問是哪一家醫院？"),
            FakeResponse(
                "林口長庚泌尿科，侯鎮邦週三上午有門診。"
                " 來源已附上。"
            )
        ])

        first = self._post_event(
            self._text_event("幫我查侯鎮邦醫師的門診時間")
        )
        second = self._post_event(
            self._text_event("林口長庚醫院泌尿科")
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        contents = [
            item["content"]
            for item in self.calls[1]["input"]
        ]
        self.assertIn("侯鎮邦醫師的門診時間", contents[0])
        self.assertIn("哪一家醫院", contents[1])
        self.assertEqual(contents[2], "林口長庚醫院泌尿科")
        reply = self.http_calls[1]["json"]["messages"][0]["text"]
        self.assertIn("林口長庚", reply)
        self.assertLessEqual(len(reply), 5000)

    def test_group_and_send_commands_skip_chat(self):
        self._scripted.append(
            FakeResponse("不該被呼叫")
        )

        group = self._post_event(
            self._text_event(
                "大家好",
                source_type="group"
            )
        )
        stranger = self._post_event(
            self._text_event("你好", user_id="Uother")
        )
        send = self._post_event(
            self._text_event("請發送：原封不動的句子")
        )

        self.assertEqual(group.status_code, 200)
        self.assertEqual(stranger.status_code, 200)
        self.assertEqual(send.status_code, 200)
        self.assertEqual(self.calls, [])
        self.assertEqual(app.load_chat_history("Uowner"), [])
        self.assertEqual(len(self.http_calls), 2)
        pushed = self.http_calls[0]["json"]
        self.assertEqual(pushed["to"], app.TARGET_GROUP_ID)
        self.assertEqual(
            pushed["messages"][0]["text"],
            "原封不動的句子"
        )
        reply = self.http_calls[1]["json"]["messages"][0]["text"]
        self.assertEqual(reply, "已原封不動送到群組。")


if __name__ == "__main__":
    unittest.main()

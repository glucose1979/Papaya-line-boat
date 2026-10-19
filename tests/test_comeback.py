import base64
import hashlib
import hmac
import json
import os
import random
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

import comeback
import app


_FORBIDDEN = (
    "幹",
    "媽",
    "娘",
    "爸",
    "死",
    "殺",
    "殘",
    "智障",
    "腦殘",
    "白痴",
    "白癡",
    "醜",
    "肥",
    "矮",
    "胖",
    "尻",
    "婊",
    "淫",
)

_USER_EXAMPLES = (
    "垃圾AI",
    "垃圾回覆",
    "垃圾AI回覆",
    "廢物AI",
    "爛AI",
    "AI很爛",
    "智障回覆",
    "垃圾機器人",
)

_NOT_INSULTS = (
    "大家好",
    "請發送",
    "請發送：原封不動",
    "垃圾車來了",
    "垃圾分類機器人",
    "這個機器人很可愛",
    "AI會寫程式",
    "回覆我一下",
    "請幫我回覆",
    "回覆差異很大",
    "廢話少說",
    "廢棄物處理",
    "train is late",
    "please wait",
    "email me",
    "again later",
    "AI is good",
    "今天天氣真好",
    "我喜歡這個機器人",
    "吵架模式開",
    "吵架模式關",
    "吵架模式狀態",
)


class MatcherTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("COMEBACK_MODE", None)
        os.environ.pop("COMEBACK_CLASSIFY_MODEL", None)
        comeback.reset_comeback_state()

    def tearDown(self):
        os.environ.pop("COMEBACK_MODE", None)
        os.environ.pop("COMEBACK_CLASSIFY_MODEL", None)
        comeback.reset_comeback_state()

    def test_user_examples_match(self):
        for text in _USER_EXAMPLES:
            self.assertTrue(comeback.is_insult(text), text)
            self.assertFalse(comeback.is_ambiguous_insult(text), text)

    def test_spacing_and_case_do_not_matter(self):
        for text in (
            "垃圾 ai",
            "垃圾  AI",
            "垃圾　回覆",
            "ＡＩ很爛",
            "ai 是垃圾",
            "AI 很 爛",
            "  廢物AI  ",
            "Garbage AI",
            "AI SUCKS",
            "A.I. is trash",
            "a.i. sucks",
            "this AI is dumb",
            "useless AI",
        ):
            self.assertTrue(comeback.is_insult(text), text)

    def test_similar_phrasing_matches(self):
        for text in (
            "這AI好爛",
            "AI是垃圾",
            "回覆真的很垃圾",
            "你的回覆好爛",
            "廢物機器人",
            "人工智障",
            "沒用的AI",
            "這機器人真的很廢",
            "AI是一個垃圾",
            "回覆品質很爛",
            "回覆得真糟",
            "AI有點爛",
            "垃圾的回覆",
            "机器人很差",
            "木瓜姊很爛",
            "木瓜姐是廢物",
        ):
            self.assertTrue(comeback.is_insult(text), text)

    def test_heads_and_tails_match_targets(self):
        for head in comeback.INSULT_HEADS:
            for target in ("AI", "回覆", "機器人"):
                self.assertTrue(
                    comeback.is_insult(f"{head}{target}"),
                    f"{head}{target}",
                )
        for tail in comeback.INSULT_TAILS:
            for target in ("AI", "回覆", "機器人"):
                self.assertTrue(
                    comeback.is_insult(f"{target}很{tail}"),
                    f"{target}很{tail}",
                )

    def test_english_insults_match_ai(self):
        for word in comeback.ENGLISH_INSULTS:
            self.assertTrue(
                comeback.is_insult(f"{word} AI"),
                word,
            )
            self.assertTrue(
                comeback.is_insult(f"AI is {word}"),
                word,
            )

    def test_ordinary_messages_do_not_match(self):
        for text in _NOT_INSULTS:
            self.assertFalse(comeback.is_insult(text), text)

    def test_non_text_is_not_an_insult(self):
        self.assertFalse(comeback.is_insult(None))
        self.assertFalse(comeback.is_insult(""))
        self.assertFalse(comeback.is_insult(123))
        self.assertFalse(comeback.is_ambiguous_insult(None))

    def test_ambiguous_cues_are_separate_from_keywords(self):
        for text in (
            "這AI有點不行",
            "機器人有點扯",
            "回覆讓人無言",
            "這AI壞掉了吧",
        ):
            self.assertFalse(comeback.is_insult(text), text)
            self.assertTrue(comeback.is_ambiguous_insult(text), text)

        for text in (
            "大家好",
            "AI會寫程式",
            "垃圾分類機器人",
            "垃圾車來了",
            "回覆差異很大",
            "請幫我回覆這段",
        ):
            self.assertFalse(comeback.is_ambiguous_insult(text), text)

    def test_long_message_is_not_sent_to_the_classifier(self):
        text = "這AI有點不行" + "喔" * 40
        self.assertGreater(len(comeback.normalize_insult_text(text)), 40)
        self.assertFalse(comeback.is_ambiguous_insult(text))

    def test_display_name_ignores_case_and_extra_spaces(self):
        for name in (
            "Hung Chang Chen",
            "hung chang chen",
            "HUNG CHANG CHEN",
            "  Hung   Chang    Chen  ",
            "Hung\u3000\u3000Chang\u3000Chen",
            "Ｈｕｎｇ　Ｃｈａｎｇ　Ｃｈｅｎ",
        ):
            self.assertTrue(comeback.is_target_sender(name), name)

        for name in (
            "Hung Chen",
            "HungChangChen",
            "Hung Chang Chen Jr",
            "",
            None,
            "Someone Else",
        ):
            self.assertFalse(comeback.is_target_sender(name), repr(name))

    def test_toggle_commands(self):
        self.assertTrue(comeback.parse_toggle("吵架模式開"))
        self.assertFalse(comeback.parse_toggle("吵架模式關"))
        self.assertTrue(comeback.parse_toggle("  吵架模式開。 "))
        self.assertFalse(comeback.parse_toggle("吵架 模式 關！"))
        self.assertIsNone(comeback.parse_toggle("吵架模式開啟"))
        self.assertIsNone(comeback.parse_toggle("請吵架模式開"))
        self.assertIsNone(comeback.parse_toggle("大家好"))
        self.assertIsNone(comeback.parse_toggle(None))
        self.assertIsNone(comeback.parse_toggle("吵架模式狀態"))

    def test_status_command_matches_like_the_toggles(self):
        self.assertTrue(comeback.is_status_command("吵架模式狀態"))
        self.assertTrue(comeback.is_status_command("  吵架模式狀態。 "))
        self.assertTrue(comeback.is_status_command("吵架 模式 狀態！"))
        self.assertFalse(comeback.is_status_command("吵架模式開"))
        self.assertFalse(comeback.is_status_command("吵架模式關"))
        self.assertFalse(comeback.is_status_command("請吵架模式狀態"))
        self.assertFalse(comeback.is_status_command("吵架模式狀態如何"))
        self.assertFalse(comeback.is_status_command(None))

    def test_default_on_and_restart_returns_to_env(self):
        self.assertTrue(comeback.comeback_enabled())

        comeback.set_comeback_enabled(False)
        self.assertFalse(comeback.comeback_enabled())

        comeback.reset_comeback_state()
        self.assertTrue(comeback.comeback_enabled())

        os.environ["COMEBACK_MODE"] = "off"
        self.assertFalse(comeback.comeback_enabled())

        comeback.set_comeback_enabled(True)
        self.assertTrue(comeback.comeback_enabled())

        comeback.reset_comeback_state()
        self.assertFalse(comeback.comeback_enabled())

        os.environ["COMEBACK_MODE"] = "on"
        self.assertTrue(comeback.comeback_enabled())

    def test_comeback_lines_are_the_owner_list(self):
        lines = comeback.COMEBACK_LINES
        self.assertEqual(
            lines,
            (
                "垃圾回覆，專送垃圾人。",
                "你丟什麼，我就回什麼。",
                "嫌爛？這則就是你的份。",
                "水準在這，回覆就在這。",
                "垃圾進，垃圾出，公平。",
                "這種話，只配這種回。",
            ),
        )
        for line in lines:
            self.assertLessEqual(len(line), 20, line)
            for word in _FORBIDDEN:
                self.assertNotIn(word, line)

    def test_pick_stays_inside_the_list(self):
        rng = random.Random(0)
        picked = {comeback.pick_comeback(rng) for _ in range(40)}
        self.assertGreater(len(picked), 1)
        self.assertTrue(picked <= set(comeback.COMEBACK_LINES))

    def test_display_name_cache_expires(self):
        calls = []

        class Response:

            def raise_for_status(self):
                return None

            def json(self):
                return {"displayName": "Hung Chang Chen"}

        def http_get(url, headers=None, timeout=None):
            calls.append(url)
            return Response()

        first = comeback.fetch_group_display_name(
            "Cg",
            "Uu",
            http_get,
            "tok",
            now=100,
        )
        second = comeback.fetch_group_display_name(
            "Cg",
            "Uu",
            http_get,
            "tok",
            now=100 + 10,
        )
        third = comeback.fetch_group_display_name(
            "Cg",
            "Uu",
            http_get,
            "tok",
            now=100 + comeback.DISPLAY_NAME_TTL_SECONDS + 1,
        )

        self.assertEqual(first, "Hung Chang Chen")
        self.assertEqual(second, first)
        self.assertEqual(third, first)
        self.assertEqual(len(calls), 2)
        self.assertEqual(
            calls[0],
            "https://api.line.me/v2/bot/group/Cg/member/Uu",
        )

    def test_profile_failure_is_not_cached(self):
        calls = []

        class Ok:

            def raise_for_status(self):
                return None

            def json(self):
                return {"displayName": "Hung Chang Chen"}

        def http_get(url, headers=None, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("down")
            return Ok()

        with self.assertRaises(RuntimeError):
            comeback.fetch_group_display_name(
                "Cg",
                "Uu",
                http_get,
                "tok",
            )

        name = comeback.fetch_group_display_name(
            "Cg",
            "Uu",
            http_get,
            "tok",
        )

        self.assertEqual(name, "Hung Chang Chen")
        self.assertEqual(len(calls), 2)

    def test_classify_yes_no_and_errors(self):
        seen = {}

        class Response:

            def __init__(self, text):
                self.output_text = text

        class Client:

            def __init__(self, result):
                self.result = result
                self.responses = self

            def create(self, **kwargs):
                seen["kwargs"] = kwargs
                result = self.result
                if isinstance(result, Exception):
                    raise result
                return result

        self.assertTrue(
            comeback.classify_insult(Client(Response("yes")), "這AI有點不行")
        )
        self.assertFalse(
            comeback.classify_insult(Client(Response("no")), "這AI有點不行")
        )
        self.assertTrue(
            comeback.classify_insult(Client(Response("是")), "這AI有點不行")
        )
        self.assertFalse(
            comeback.classify_insult(
                Client(RuntimeError("boom")),
                "這AI有點不行",
            )
        )
        self.assertEqual(seen["kwargs"]["model"], "gpt-4o-mini")
        self.assertNotIn("reasoning", seen["kwargs"])

        os.environ["COMEBACK_CLASSIFY_MODEL"] = "gpt-5-mini"
        self.assertFalse(
            comeback.classify_insult(Client(Response("no")), "這AI有點不行")
        )
        self.assertEqual(seen["kwargs"]["model"], "gpt-5-mini")
        self.assertEqual(
            seen["kwargs"]["reasoning"],
            {"effort": "minimal"},
        )


class HandleTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("COMEBACK_MODE", None)
        comeback.reset_comeback_state()
        self.replies = []
        self.lookups = []
        self.classified = []

    def tearDown(self):
        os.environ.pop("COMEBACK_MODE", None)
        comeback.reset_comeback_state()

    def _lookup(self, group_id, user_id):
        self.lookups.append((group_id, user_id))
        return self.names.get(user_id, "")

    def _classify(self, text):
        self.classified.append(text)
        return self.classify_result

    def _handle(self, text, user_id="Uhung", owner="Uowner"):
        return comeback.handle_group_text(
            text,
            user_id,
            "Cgroup",
            self.replies.append,
            self._lookup,
            self._classify,
            owner,
        )

    def test_strong_insult_from_target_replies_once(self):
        self.names = {"Uhung": "  hung   CHANG  chen "}
        self.classify_result = False

        result = self._handle("你這個垃圾AI")

        self.assertEqual(result, "comeback")
        self.assertEqual(len(self.replies), 1)
        self.assertIn(self.replies[0], comeback.COMEBACK_LINES)
        self.assertEqual(self.classified, [])
        self.assertEqual(self.lookups, [("Cgroup", "Uhung")])

    def test_other_sender_and_ordinary_text_stay_quiet(self):
        self.names = {"Uother": "Someone Else"}
        self.classify_result = True

        self.assertEqual(self._handle("大家好", "Uother"), "ignore")
        self.assertEqual(self._handle("垃圾AI", "Uother"), "not_target")
        self.assertEqual(self.replies, [])
        self.assertEqual(self.classified, [])
        self.assertEqual(len(self.lookups), 1)

    def test_ambiguous_classifies_only_after_the_name_matches(self):
        self.names = {
            "Uother": "Someone Else",
            "Uhung": "Hung Chang Chen",
        }
        self.classify_result = False

        self.assertEqual(
            self._handle("這AI有點不行", "Uother"),
            "not_target",
        )
        self.assertEqual(self.classified, [])

        self.assertEqual(
            self._handle("這AI有點不行", "Uhung"),
            "not_insult",
        )
        self.assertEqual(self.classified, ["這AI有點不行"])
        self.assertEqual(self.replies, [])

        self.classify_result = True
        self.assertEqual(
            self._handle("機器人有點扯", "Uhung"),
            "comeback",
        )
        self.assertEqual(len(self.replies), 1)

    def test_owner_toggles_and_restart_uses_env_again(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = False

        self.assertEqual(self._handle("吵架模式關", "Uowner"), "toggle_off")
        self.assertEqual(self.replies, [comeback.TOGGLE_OFF_REPLY])
        self.assertEqual(self._handle("垃圾AI"), "disabled")
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(self.lookups, [])

        self.assertEqual(
            self._handle("吵架模式關", "Uother"),
            "toggle_ignored",
        )
        self.assertFalse(comeback.comeback_enabled())

        self.assertEqual(self._handle("吵架模式開", "Uowner"), "toggle_on")
        self.assertEqual(self.replies[-1], comeback.TOGGLE_ON_REPLY)
        self.assertEqual(self._handle("垃圾AI"), "comeback")

        comeback.reset_comeback_state()
        self.assertTrue(comeback.comeback_enabled())

    def test_status_reports_the_real_switch_and_ignores_others(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = False

        self.assertEqual(
            self._handle("吵架模式狀態", "Uowner"),
            "status_on",
        )
        self.assertEqual(self.replies, [comeback.STATUS_ON_REPLY])
        self.assertEqual(self.lookups, [])
        self.assertEqual(self.classified, [])
        self.assertTrue(comeback.comeback_enabled())

        self.assertEqual(self._handle("吵架模式關", "Uowner"), "toggle_off")
        self.assertEqual(
            self._handle("吵架模式狀態。", "Uowner"),
            "status_off",
        )
        self.assertEqual(self.replies[-1], comeback.STATUS_OFF_REPLY)
        self.assertFalse(comeback.comeback_enabled())
        self.assertEqual(self.lookups, [])

        before = len(self.replies)
        self.assertEqual(
            self._handle("吵架模式狀態", "Uother"),
            "status_ignored",
        )
        self.assertEqual(len(self.replies), before)
        self.assertFalse(comeback.comeback_enabled())

    def test_missing_owner_id_lets_anyone_toggle(self):
        self.names = {}
        self.classify_result = False

        result = self._handle("吵架模式關", "Uother", owner="")

        self.assertEqual(result, "toggle_off")
        self.assertEqual(self.replies, [comeback.TOGGLE_OFF_REPLY])
        self.assertFalse(comeback.comeback_enabled())

    def test_profile_error_does_not_reply(self):
        self.classify_result = False

        def broken(group_id, user_id):
            raise RuntimeError("line down")

        result = comeback.handle_group_text(
            "垃圾AI",
            "Uhung",
            "Cgroup",
            self.replies.append,
            broken,
            self._classify,
            "Uowner",
        )

        self.assertEqual(result, "lookup_error")
        self.assertEqual(self.replies, [])


class FakeHttpResponse:

    def __init__(self, payload=None, fail=False):
        self._payload = payload or {}
        self._fail = fail

    def raise_for_status(self):
        if self._fail:
            raise RuntimeError("http failed")

    def json(self):
        return self._payload


class FakeChatResponse:

    def __init__(self, text):
        self.output_text = text
        self.status = "completed"
        self.incomplete_details = None
        self.usage = {
            "output_tokens": 12,
            "output_tokens_details": {"reasoning_tokens": 0},
        }
        self.output = []


class WebhookTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("COMEBACK_MODE", None)
        comeback.reset_comeback_state()
        app.clear_chat_memory()
        self.calls = []
        self.posts = []
        self.gets = []
        self._scripted = []
        self.names = {"Uhung": "Hung Chang Chen"}
        self._original_create = app.client.responses.create
        self._original_post = app.requests.post
        self._original_get = app.requests.get
        app.client.responses.create = self._create
        app.requests.post = self._post
        app.requests.get = self._get
        self.client = app.app.test_client()

    def tearDown(self):
        os.environ.pop("COMEBACK_MODE", None)
        comeback.reset_comeback_state()
        app.client.responses.create = self._original_create
        app.requests.post = self._original_post
        app.requests.get = self._original_get
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
        self.posts.append({
            "url": url,
            "json": json,
        })
        return FakeHttpResponse()

    def _get(self, url, headers=None, timeout=None):
        self.gets.append({
            "url": url,
            "headers": headers,
            "timeout": timeout,
        })
        user_id = url.rstrip("/").rsplit("/", 1)[-1]
        return FakeHttpResponse({
            "displayName": self.names.get(user_id, ""),
        })

    def _sign(self, body):
        digest = hmac.new(
            app.LINE_CHANNEL_SECRET.encode("utf-8"),
            body,
            hashlib.sha256,
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    def _post_event(self, event):
        body = json.dumps({"events": [event]}).encode("utf-8")
        return self.client.post(
            "/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Line-Signature": self._sign(body),
            },
        )

    def _text_event(
        self,
        text,
        user_id="Uowner",
        source_type="user",
        group_id="Cgroup",
    ):
        source = {
            "type": source_type,
            "userId": user_id,
        }
        if source_type == "group":
            source["groupId"] = group_id
        return {
            "type": "message",
            "replyToken": "reply-token",
            "source": source,
            "message": {
                "type": "text",
                "id": "m1",
                "text": text,
            },
        }

    def test_group_insult_uses_reply_api_not_push(self):
        response = self._post_event(
            self._text_event(
                "垃圾 AI 回覆",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.gets), 1)
        self.assertEqual(
            self.gets[0]["url"],
            "https://api.line.me/v2/bot/group/Cgroup/member/Uhung",
        )
        self.assertEqual(
            self.gets[0]["headers"]["Authorization"],
            "Bearer test-token",
        )
        self.assertEqual(len(self.posts), 1)
        posted = self.posts[0]
        self.assertEqual(
            posted["url"],
            "https://api.line.me/v2/bot/message/reply",
        )
        self.assertNotIn("push", posted["url"])
        self.assertEqual(posted["json"]["replyToken"], "reply-token")
        messages = posted["json"]["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["type"], "text")
        self.assertIn(messages[0]["text"], comeback.COMEBACK_LINES)

        again = self._post_event(
            self._text_event(
                "AI很爛",
                user_id="Uhung",
                source_type="group",
            )
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.gets), 1)
        self.assertEqual(len(self.posts), 2)

    def test_wrong_name_and_plain_group_text_stay_silent(self):
        self.names = {"Uother": "Other Person"}

        plain = self._post_event(
            self._text_event(
                "大家好",
                user_id="Uother",
                source_type="group",
            )
        )
        insult = self._post_event(
            self._text_event(
                "垃圾機器人",
                user_id="Uother",
                source_type="group",
            )
        )
        send = self._post_event(
            self._text_event(
                "請發送：不要推出去",
                user_id="Uowner",
                source_type="group",
            )
        )

        self.assertEqual(plain.status_code, 200)
        self.assertEqual(insult.status_code, 200)
        self.assertEqual(send.status_code, 200)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.gets), 1)

    def test_owner_toggle_and_private_chat_still_chat(self):
        off = self._post_event(
            self._text_event(
                "吵架模式關",
                user_id="Uowner",
                source_type="group",
            )
        )
        self.assertEqual(off.status_code, 200)
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "吵架模式關了。",
        )
        self.assertNotIn("push", self.posts[0]["url"])

        blocked = self._post_event(
            self._text_event(
                "廢物AI",
                user_id="Uhung",
                source_type="group",
            )
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.gets, [])

        stranger = self._post_event(
            self._text_event(
                "吵架模式開",
                user_id="Uhung",
                source_type="group",
            )
        )
        self.assertEqual(stranger.status_code, 200)
        self.assertEqual(len(self.posts), 1)
        self.assertFalse(comeback.comeback_enabled())

        self._scripted.append(FakeChatResponse("私訊照常回。"))
        private = self._post_event(self._text_event("垃圾AI"))

        self.assertEqual(private.status_code, 200)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.posts[-1]["json"]["messages"][0]["text"],
            "私訊照常回。",
        )
        self.assertNotIn("私訊照常回。", comeback.COMEBACK_LINES)

    def test_private_toggle_is_global_and_skips_chat(self):
        off = self._post_event(self._text_event("吵架模式關"))

        self.assertEqual(off.status_code, 200)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(
            self.posts[0]["url"],
            "https://api.line.me/v2/bot/message/reply",
        )
        self.assertEqual(
            self.posts[0]["json"]["replyToken"],
            "reply-token",
        )
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "吵架模式關了。",
        )
        self.assertFalse(comeback.comeback_enabled())
        self.assertEqual(app.load_chat_history("Uowner"), [])

        blocked = self._post_event(
            self._text_event(
                "垃圾AI",
                user_id="Uhung",
                source_type="group",
            )
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.gets, [])

        stranger = self._post_event(
            self._text_event(
                "吵架模式開",
                user_id="Uother",
            )
        )
        self.assertEqual(stranger.status_code, 200)
        self.assertFalse(comeback.comeback_enabled())
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.calls, [])

        on = self._post_event(self._text_event("  吵架模式開。 "))

        self.assertEqual(on.status_code, 200)
        self.assertTrue(comeback.comeback_enabled())
        self.assertEqual(
            self.posts[-1]["json"]["messages"][0]["text"],
            "吵架模式開了。",
        )
        self.assertEqual(self.calls, [])

        hit = self._post_event(
            self._text_event(
                "廢物AI",
                user_id="Uhung",
                source_type="group",
            )
        )
        self.assertEqual(hit.status_code, 200)
        self.assertEqual(
            self.posts[-1]["url"],
            "https://api.line.me/v2/bot/message/reply",
        )
        self.assertIn(
            self.posts[-1]["json"]["messages"][0]["text"],
            comeback.COMEBACK_LINES,
        )
        self.assertEqual(len(self.gets), 1)

    def test_status_replies_with_the_real_switch_and_skips_chat(self):
        private = self._post_event(self._text_event("吵架模式狀態"))

        self.assertEqual(private.status_code, 200)
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(
            self.posts[0]["url"],
            "https://api.line.me/v2/bot/message/reply",
        )
        self.assertNotIn("push", self.posts[0]["url"])
        self.assertEqual(
            self.posts[0]["json"]["replyToken"],
            "reply-token",
        )
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "吵架模式現在是開的。",
        )
        self.assertEqual(app.load_chat_history("Uowner"), [])
        self.assertTrue(comeback.comeback_enabled())

        self._post_event(self._text_event("吵架模式關"))
        group = self._post_event(
            self._text_event(
                "吵架模式狀態！",
                user_id="Uowner",
                source_type="group",
            )
        )

        self.assertEqual(group.status_code, 200)
        self.assertEqual(
            self.posts[-1]["url"],
            "https://api.line.me/v2/bot/message/reply",
        )
        self.assertEqual(
            self.posts[-1]["json"]["messages"][0]["text"],
            "吵架模式現在是關的。",
        )
        self.assertEqual(self.calls, [])
        self.assertEqual(self.gets, [])
        self.assertFalse(comeback.comeback_enabled())

        before = len(self.posts)
        stranger_group = self._post_event(
            self._text_event(
                "吵架模式狀態",
                user_id="Uhung",
                source_type="group",
            )
        )
        stranger_private = self._post_event(
            self._text_event(
                "吵架模式狀態",
                user_id="Uother",
            )
        )

        self.assertEqual(stranger_group.status_code, 200)
        self.assertEqual(stranger_private.status_code, 200)
        self.assertEqual(len(self.posts), before)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.gets, [])
        self.assertFalse(comeback.comeback_enabled())

    def test_profile_failure_still_returns_200(self):
        def broken_get(url, headers=None, timeout=None):
            raise RuntimeError("line down")

        app.requests.get = broken_get

        response = self._post_event(
            self._text_event(
                "爛AI",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()

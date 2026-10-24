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

    def test_profanity_matches_phrases_spaces_and_homophones(self):
        for text in (
            "操你媽",
            "幹你娘",
            "幹你老師",
            "幹",
            "靠北",
            "靠杯",
            "添你娘",
            "X你娘",
            "x你娘",
            "操 你 媽",
            "操-你-媽",
            "操*你*媽",
            "草你媽",
            "淦你娘",
            "干你老師",
            "乾你娘",
            "舔你娘",
            "靠背",
            "幹你",
            "操你全家",
            "幹！",
            "ｃｎｍ",
            "×你娘",
            "*你娘",
            "這群人幹",
        ):
            self.assertTrue(comeback.is_profanity(text), text)

        for text in (
            "大家好",
            "垃圾AI",
            "幹部來了",
            "幹嘛",
            "幹什麼",
            "樹幹",
            "若干人",
            "你好",
            "你娘",
            None,
            "",
        ):
            self.assertFalse(comeback.is_profanity(text), repr(text))

    def test_lewd_innuendo_and_taunts_are_separate_from_profanity(self):
        for text in (
            "幫我吹喇叭可以嗎",
            "聽到幫我吹喇叭你就嚇一跳？",
            "你喜歡被舔嗎還是幫我吹雞吧",
            "吹 喇 叭",
            "幫我吹簫",
            "舔我",
            "幫我舔",
        ):
            self.assertTrue(comeback.is_lewd_harassment(text), text)
            self.assertFalse(comeback.is_profanity(text), text)
            self.assertFalse(comeback.should_classify(text), text)

        for text in (
            "喇叭壞了",
            "幫我吹牛",
            "大家好",
            "怎麼不講話了喔",
            "還是你不知道這是什麼意思？",
            None,
            "",
        ):
            self.assertFalse(comeback.is_lewd_harassment(text), repr(text))

        self.assertTrue(comeback.is_direct_taunt("笑她不敢回"))
        self.assertTrue(comeback.is_direct_taunt("你就嚇一跳"))
        self.assertFalse(comeback.is_direct_taunt("我不敢回去"))
        self.assertFalse(comeback.should_classify("笑她不敢回"))
        self.assertTrue(comeback.is_followup_taunt("怎麼不講話了喔"))
        self.assertTrue(
            comeback.is_followup_taunt("還是你不知道這是什麼意思？")
        )
        self.assertFalse(comeback.is_followup_taunt("笑她不敢回"))
        for phrase in (
            "幫我吹喇叭",
            "吹雞",
            "被舔",
            "你喜歡被舔嗎",
            "怎麼不講話了",
            "笑她不敢回",
        ):
            self.assertIn(phrase, comeback.CLASSIFY_INSTRUCTIONS)

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

    def test_classifier_accepts_any_text_up_to_300(self):
        legacy = "這AI有點不行" + "喔" * 40
        self.assertGreater(
            len(comeback.normalize_insult_text(legacy)),
            40,
        )
        self.assertFalse(comeback.is_ambiguous_insult(legacy))
        self.assertTrue(comeback.should_classify(legacy))
        self.assertTrue(comeback.should_classify("問了也是白問"))
        self.assertTrue(comeback.should_classify("大家好"))
        self.assertTrue(comeback.should_classify("喔" * 300))
        self.assertFalse(comeback.should_classify("喔" * 301))
        self.assertFalse(comeback.should_classify("垃圾AI"))
        self.assertFalse(comeback.should_classify("  "))
        self.assertFalse(comeback.should_classify(None))
        long_hit = "垃圾AI" + ("喔" * 400)
        self.assertTrue(comeback.is_insult(long_hit))
        self.assertFalse(comeback.should_classify(long_hit))

    def test_classify_instructions_list_meaning_examples(self):
        for phrase in (
            "垃圾AI",
            "垃圾垃圾回覆",
            "木瓜姊好爛",
            "機器人真的很蠢",
            "AI的回答有夠廢",
            "這台機器人在亂講",
            "回得什麼鬼",
            "人工智障",
            "問了也是白問",
        ):
            self.assertIn(phrase, comeback.CLASSIFY_INSTRUCTIONS)
        self.assertIn("回覆訊息", comeback.CLASSIFY_INSTRUCTIONS)
        self.assertIn("垃圾分類", comeback.CLASSIFY_INSTRUCTIONS)
        self.assertIn("垃圾車", comeback.CLASSIFY_INSTRUCTIONS)
        self.assertNotIn("沒有那個標記時", comeback.CLASSIFY_INSTRUCTIONS)
        for phrase in (
            "醜八怪",
            "你性功能障礙",
            "白癡",
            "大白癡",
            "低能兒",
            "胸大無腦",
            "白癡加三級",
        ):
            self.assertIn(phrase, comeback.CLASSIFY_INSTRUCTIONS)
        self.assertIn("真的很貼", comeback.REPLY_INSTRUCTIONS)
        self.assertIn("50", comeback.REPLY_INSTRUCTIONS)
        self.assertIn("敬告", comeback.REPLY_INSTRUCTIONS)

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
                "連 AI 都懶得跟你計較，你自己品味一下。",
                "你的見識，就跟你的發言一樣廉價。",
                "罵得這麼用力，可惜沒半個人在聽。",
            ),
        )
        self.assertEqual(len(lines), 9)
        for line in lines:
            for word in _FORBIDDEN:
                self.assertNotIn(word, line)

    def test_group_reply_uses_the_fullwidth_prefix(self):
        self.assertEqual(
            comeback.COMEBACK_PREFIX,
            "敬告 Hung Chang Chen：",
        )
        self.assertNotIn(":", comeback.COMEBACK_PREFIX)
        for line in comeback.COMEBACK_LINES:
            sent = comeback.format_group_comeback(line)
            self.assertEqual(
                sent,
                "敬告 Hung Chang Chen：" + line,
            )
            self.assertFalse(sent.startswith("敬告 Hung Chang Chen:"))

    def test_pick_stays_inside_the_list(self):
        rng = random.Random(0)
        picked = {comeback.pick_comeback(rng) for _ in range(40)}
        self.assertGreater(len(picked), 1)
        self.assertTrue(picked <= set(comeback.COMEBACK_LINES))

    def test_pick_skips_recent_lines_until_the_pool_is_empty(self):
        kept = comeback.COMEBACK_LINES[0]
        avoided = comeback.COMEBACK_LINES[1:]
        rng = random.Random(0)
        for _ in range(10):
            self.assertEqual(
                comeback.pick_comeback(rng, avoid=avoided),
                kept,
            )
        self.assertIn(
            comeback.pick_comeback(rng, avoid=comeback.COMEBACK_LINES),
            comeback.COMEBACK_LINES,
        )

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

    def test_group_summary_is_cached_per_group_id(self):
        calls = []

        class Response:

            def __init__(self, group_id, group_name):
                self._payload = {
                    "groupId": group_id,
                    "groupName": group_name,
                }

            def raise_for_status(self):
                return None

            def json(self):
                return self._payload

        def http_get(url, headers=None, timeout=None):
            calls.append(url)
            if url.endswith("/Ctrain/summary"):
                return Response("Ctrain", "自動回覆訓練區")
            return Response("Cother", "燒豬群")

        first = comeback.get_group_summary(
            "Ctrain",
            http_get,
            "tok",
            now=100,
        )
        second = comeback.get_group_summary(
            "Ctrain",
            http_get,
            "tok",
            now=100 + 10,
        )
        other = comeback.get_group_summary(
            "Cother",
            http_get,
            "tok",
            now=100 + 10,
        )
        expired = comeback.get_group_summary(
            "Ctrain",
            http_get,
            "tok",
            now=100 + comeback.DISPLAY_NAME_TTL_SECONDS + 1,
        )

        self.assertEqual(first.groupName, "自動回覆訓練區")
        self.assertTrue(comeback.is_training_group(first.groupName))
        self.assertEqual(second.groupName, first.groupName)
        self.assertEqual(other.groupName, "燒豬群")
        self.assertFalse(comeback.is_training_group(other.groupName))
        self.assertFalse(comeback.is_training_group("自動回覆訓練區 "))
        self.assertEqual(expired.groupName, "自動回覆訓練區")
        self.assertEqual(
            calls,
            [
                "https://api.line.me/v2/bot/group/Ctrain/summary",
                "https://api.line.me/v2/bot/group/Cother/summary",
                "https://api.line.me/v2/bot/group/Ctrain/summary",
            ],
        )
        self.assertEqual(
            calls[0],
            "https://api.line.me/v2/bot/group/Ctrain/summary",
        )

        comeback.reset_comeback_state()
        self.assertIsNone(comeback.get_cached_group_name("Ctrain"))

    def test_group_summary_failure_is_not_cached(self):
        calls = []

        class Ok:

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "groupId": "Ctrain",
                    "groupName": "自動回覆訓練區",
                }

        def http_get(url, headers=None, timeout=None):
            calls.append(url)
            if len(calls) == 1:
                raise RuntimeError("down")
            return Ok()

        with self.assertRaises(RuntimeError):
            comeback.get_group_summary("Ctrain", http_get, "tok")

        summary = comeback.get_group_summary("Ctrain", http_get, "tok")

        self.assertEqual(summary.groupName, "自動回覆訓練區")
        self.assertEqual(len(calls), 2)
        empty = comeback.get_group_summary("", http_get, "tok")
        self.assertEqual(empty.groupName, "")
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

        long_text = "問" * 400
        comeback.classify_insult(
            Client(Response("no")),
            long_text,
            quoted=True,
        )
        self.assertTrue(
            seen["kwargs"]["input"].startswith("（這則是回覆訊息）\n")
        )
        self.assertEqual(
            seen["kwargs"]["input"].split("\n", 1)[1],
            "問" * comeback.CLASSIFY_MAX_CHARS,
        )


class HandleTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("COMEBACK_MODE", None)
        comeback.reset_comeback_state()
        self.replies = []
        self.lookups = []
        self.classified = []
        self.quoted_flags = []

    def tearDown(self):
        os.environ.pop("COMEBACK_MODE", None)
        comeback.reset_comeback_state()

    def _lookup(self, group_id, user_id):
        self.lookups.append((group_id, user_id))
        return self.names.get(user_id, "")

    def _classify(self, text, quoted=False):
        self.classified.append(text)
        self.quoted_flags.append(bool(quoted))
        return self.classify_result

    def _handle(
        self,
        text,
        user_id="Uhung",
        owner="Uowner",
        quoted=False,
        training=False,
        generate=None,
    ):
        return comeback.handle_group_text(
            text,
            user_id,
            "Cgroup",
            self.replies.append,
            self._lookup,
            self._classify,
            owner,
            quoted=quoted,
            training=training,
            generate=generate,
        )

    def test_strong_insult_from_target_replies_once(self):
        self.names = {"Uhung": "  hung   CHANG  chen "}
        self.classify_result = False

        result = self._handle("你這個垃圾AI")

        self.assertEqual(result, "comeback")
        self.assertEqual(len(self.replies), 1)
        self.assertEqual(
            self.replies[0],
            comeback.format_group_comeback(
                self.replies[0][len(comeback.COMEBACK_PREFIX):]
            ),
        )
        self.assertIn(
            self.replies[0][len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )
        self.assertEqual(self.classified, [])
        self.assertEqual(self.lookups, [("Cgroup", "Uhung")])

    def test_other_sender_and_ordinary_text_stay_quiet(self):
        self.names = {"Uother": "Someone Else"}
        self.classify_result = True

        self.assertEqual(self._handle("大家好", "Uother"), "not_target")
        self.assertEqual(self._handle("垃圾AI", "Uother"), "not_target")
        self.assertEqual(self.replies, [])
        self.assertEqual(self.classified, [])
        self.assertEqual(len(self.lookups), 2)

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

    def test_target_meaning_is_classified_and_others_are_not(self):
        self.names = {
            "Uhung": "Hung Chang Chen",
            "Uother": "Someone Else",
        }
        self.classify_result = True

        self.assertEqual(self._handle("問了也是白問"), "comeback")
        self.assertEqual(self.classified, ["問了也是白問"])
        self.assertEqual(self.quoted_flags, [False])
        self.assertTrue(
            self.replies[0].startswith(comeback.COMEBACK_PREFIX)
        )

        self.assertEqual(
            self._handle("問了也是白問", "Uother"),
            "not_target",
        )
        self.assertEqual(self.classified, ["問了也是白問"])

        self.classify_result = False
        self.assertEqual(self._handle("回得什麼鬼"), "not_insult")
        self.assertEqual(self.replies, [self.replies[0]])

    def test_over_cap_skips_lookup_and_classifier(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = True

        self.assertEqual(self._handle("問" * 301), "ignore")
        self.assertEqual(self.lookups, [])
        self.assertEqual(self.classified, [])
        self.assertEqual(self.replies, [])

        self.assertEqual(
            self._handle("垃圾AI" + ("喔" * 400)),
            "comeback",
        )
        self.assertEqual(self.classified, [])
        self.assertEqual(len(self.replies), 1)

    def test_quoted_dismissive_is_marked_for_the_classifier(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = True

        self.assertEqual(
            self._handle("好爛", quoted=True),
            "comeback",
        )
        self.assertEqual(self.classified, ["好爛"])
        self.assertEqual(self.quoted_flags, [True])

    def test_disabled_plain_text_does_not_look_up(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = True

        self.assertEqual(self._handle("吵架模式關", "Uowner"), "toggle_off")
        self.assertEqual(self._handle("問了也是白問"), "disabled")
        self.assertEqual(self.lookups, [])
        self.assertEqual(self.classified, [])

    def test_training_group_counts_any_sender_and_uses_their_name(self):
        self.names = {
            "Uowner": "侯鎮邦",
            "Uother": "Someone Else",
        }
        self.classify_result = True

        result = self._handle("垃圾AI", "Uowner", training=True)

        self.assertEqual(result, "comeback")
        self.assertEqual(self.classified, [])
        prefix = "敬告 侯鎮邦："
        self.assertEqual(ord(prefix[-1]), 0xFF1A)
        self.assertNotIn(":", prefix)
        self.assertTrue(self.replies[0].startswith(prefix))
        self.assertFalse(
            self.replies[0].startswith(comeback.COMEBACK_PREFIX)
        )
        self.assertIn(
            self.replies[0][len(prefix):],
            comeback.COMEBACK_LINES,
        )

        self.assertEqual(
            self._handle("問了也是白問", "Uother", training=True),
            "comeback",
        )
        self.assertEqual(self.classified, ["問了也是白問"])
        self.assertTrue(self.replies[-1].startswith("敬告 Someone Else："))

        self.classified.clear()
        self.assertEqual(
            self._handle("垃圾AI", "Uowner", training=False),
            "not_target",
        )
        self.assertEqual(self.classified, [])

        self.assertEqual(
            self._handle("吵架模式狀態", "Uowner", training=True),
            "status_on",
        )
        self.assertEqual(self.replies[-1], comeback.STATUS_ON_REPLY)

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

    def test_generated_line_keeps_the_prefix_rules(self):
        self.names = {
            "Uhung": "Hung Chang Chen",
            "Uowner": "侯鎮邦",
            "Uother": "Someone Else",
        }
        self.classify_result = False
        seen = []

        def generate(text, quoted=False):
            seen.append((text, quoted))
            return "就這水準？"

        result = self._handle("垃圾AI", generate=generate)

        self.assertEqual(result, "comeback")
        self.assertEqual(seen, [("垃圾AI", False)])
        self.assertEqual(
            self.replies[0],
            comeback.COMEBACK_PREFIX + "就這水準？",
        )
        self.assertLessEqual(len("就這水準？"), 50)
        self.assertNotIn("就這水準？", comeback.COMEBACK_LINES)

        self.assertEqual(
            self._handle(
                "垃圾AI",
                "Uowner",
                training=True,
                generate=generate,
            ),
            "comeback",
        )
        self.assertEqual(
            self.replies[-1],
            "敬告 侯鎮邦：就這水準？",
        )

        before = len(seen)
        self.assertEqual(
            self._handle("垃圾AI", "Uother", generate=generate),
            "not_target",
        )
        self.assertEqual(len(seen), before)
        self.assertEqual(
            self._handle("吵架模式狀態", "Uowner", generate=generate),
            "status_on",
        )
        self.assertEqual(len(seen), before)

    def test_profanity_uses_the_fixed_body_and_skips_the_model(self):
        self.names = {
            "Uhung": "Hung Chang Chen",
            "Uowner": "侯鎮邦",
            "Uother": "Someone Else",
        }
        self.classify_result = True
        seen = []

        def generate(text, quoted=False):
            seen.append(text)
            return "就這水準？"

        result = self._handle("操你媽", generate=generate)

        self.assertEqual(result, "profanity")
        self.assertEqual(seen, [])
        self.assertEqual(self.classified, [])
        self.assertEqual(
            self.replies[0],
            comeback.COMEBACK_PREFIX + "你自己操自己",
        )

        self.assertEqual(
            self._handle("幹你娘", "Uowner", training=True, generate=generate),
            "profanity",
        )
        self.assertEqual(
            self.replies[-1],
            "敬告 侯鎮邦：你自己操自己",
        )
        self.assertEqual(seen, [])

        self.assertEqual(
            self._handle("靠北", "Uother", generate=generate),
            "not_target",
        )
        self.assertEqual(seen, [])
        self.assertEqual(self.replies, self.replies[:2])

        self.assertEqual(
            self._handle("幹" + ("喔" * 400), generate=generate),
            "profanity",
        )
        self.assertEqual(seen, [])

        self.replies.clear()
        self.assertEqual(
            self._handle("吵架模式關", "Uowner"),
            "toggle_off",
        )
        lookups_before = len(self.lookups)
        self.assertEqual(self._handle("幹", generate=generate), "disabled")
        self.assertEqual(len(self.lookups), lookups_before)
        self.assertEqual(seen, [])

    def test_bare_personal_insults_go_to_the_classifier(self):
        self.names = {
            "Uhung": "Hung Chang Chen",
            "Uother": "Someone Else",
        }
        for text in (
            "醜八怪",
            "白癡",
            "大白癡",
            "低能兒",
            "胸大無腦",
            "白癡加三級",
            "低能兒 胸大無腦",
        ):
            self.assertFalse(comeback.is_insult(text), text)
            self.assertFalse(comeback.is_profanity(text), text)
            self.assertTrue(comeback.should_classify(text), text)
        self.assertTrue(comeback.is_bare_insult("你性功能障礙"))
        self.assertFalse(comeback.should_classify("你性功能障礙"))

        self.classify_result = True
        result = self._handle(
            "醜八怪",
            generate=lambda text, quoted=False: "鏡子同意",
        )

        self.assertEqual(result, "comeback")
        self.assertEqual(self.classified, ["醜八怪"])
        self.assertEqual(
            self.replies[0],
            comeback.COMEBACK_PREFIX + "鏡子同意",
        )
        self.assertEqual(
            comeback.recent_replies("Cgroup"),
            ("鏡子同意",),
        )

        self.classify_result = False
        self.classified.clear()
        self.assertEqual(self._handle("低能兒"), "not_insult")
        self.assertEqual(self.classified, ["低能兒"])
        self.assertEqual(len(self.replies), 1)

        self.classify_result = True
        self.assertEqual(
            self._handle("大白癡", "Uother"),
            "not_target",
        )
        self.assertEqual(self.classified, ["低能兒"])

    def test_recent_replies_stay_per_group_and_reset(self):
        for index in range(6):
            comeback.remember_reply("Gone", str(index))
        comeback.remember_reply("Gother", "別群")
        comeback.remember_reply("", "不算")

        self.assertEqual(
            comeback.recent_replies("Gone"),
            ("1", "2", "3", "4", "5"),
        )
        self.assertEqual(comeback.recent_replies("Gother"), ("別群",))
        self.assertEqual(comeback.recent_replies(""), ())

        comeback.reset_comeback_state()

        self.assertEqual(comeback.recent_replies("Gone"), ())
        self.assertEqual(comeback.recent_replies("Gother"), ())

    def test_context_is_kept_for_both_groups_and_includes_the_bot(self):
        self.names = {
            "Uhung": "Hung Chang Chen",
            "Uother": "路人",
            "Utrain": "朱靜羊",
        }
        self.classify_result = False
        for index in range(6):
            comeback.remember_context("Gone", "甲", str(index))
        self.assertEqual(
            comeback.recent_context("Gone"),
            tuple(("甲", str(index)) for index in range(1, 6)),
        )
        self.assertIn("罵什麼", comeback.REPLY_INSTRUCTIONS)
        self.assertIn("不要拿句裡單獨一個字", comeback.REPLY_INSTRUCTIONS)

        self._handle(
            "垃圾AI",
            generate=lambda text, quoted=False: "就這句。",
        )
        self.assertEqual(
            comeback.recent_context("Cgroup")[0],
            ("Hung Chang Chen", "垃圾AI"),
        )
        self.assertEqual(
            comeback.recent_context("Cgroup")[1][0],
            "木瓜姊",
        )
        self.assertTrue(
            comeback.recent_context("Cgroup")[1][1].startswith(
                comeback.COMEBACK_PREFIX
            )
        )

        self.assertEqual(
            self._handle("今天天氣不錯", "Uother"),
            "not_target",
        )
        self.assertEqual(
            comeback.recent_context("Cgroup")[-1],
            ("路人", "今天天氣不錯"),
        )

        comeback.handle_group_text(
            "我都不看垃圾AI產出來的東西",
            "Utrain",
            "Ctrain",
            self.replies.append,
            self._lookup,
            self._classify,
            "Uowner",
            training=True,
            generate=lambda text, quoted=False: "沒看就判。",
        )
        trained = comeback.recent_context("Ctrain")
        self.assertEqual(
            trained[0],
            ("朱靜羊", "我都不看垃圾AI產出來的東西"),
        )
        self.assertEqual(trained[1][0], "木瓜姊")
        self.assertTrue(trained[1][1].startswith("敬告 朱靜羊："))
        self.assertNotEqual(
            comeback.recent_context("Cgroup"),
            comeback.recent_context("Ctrain"),
        )

        comeback.reset_comeback_state()
        self.assertEqual(comeback.recent_context("Cgroup"), ())
        self.assertEqual(comeback.recent_context("Ctrain"), ())

    def test_profanity_still_repeats_when_it_was_just_used(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        comeback.remember_reply("Cgroup", comeback.PROFANITY_REPLY)

        result = self._handle(
            "操你媽",
            generate=lambda text, quoted=False: "就這水準？",
        )

        self.assertEqual(result, "profanity")
        self.assertEqual(
            self.replies,
            [comeback.COMEBACK_PREFIX + comeback.PROFANITY_REPLY],
        )
        self.assertEqual(
            comeback.recent_replies("Cgroup")[-1],
            comeback.PROFANITY_REPLY,
        )

    def test_chosen_fixed_line_is_not_cut_to_ten_characters(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = False
        line = comeback.COMEBACK_LINES[6]

        result = self._handle(
            "垃圾AI",
            generate=lambda text, quoted=False: line,
        )

        self.assertEqual(result, "comeback")
        self.assertEqual(
            self.replies[0],
            comeback.COMEBACK_PREFIX + line,
        )

    def test_bad_generation_falls_back_to_a_fixed_line(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = False

        def explode(text, quoted=False):
            raise RuntimeError("timeout")

        result = self._handle("垃圾AI", generate=explode)

        self.assertEqual(result, "comeback")
        self.assertIn(
            self.replies[0][len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )

        def too_long(text, quoted=False):
            return "長" * 51

        self.replies.clear()
        result = self._handle("廢物AI", generate=too_long)

        self.assertEqual(result, "comeback")
        self.assertIn(
            self.replies[0][len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )

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

    def test_slurs_and_sexual_jabs_trigger_without_the_classifier(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        self.classify_result = False

        for text in (
            "性無能",
            "你簡直性無能",
            "賤貨",
            "超級賤貨",
            "哪個賤貨在群裡",
        ):
            self.assertTrue(comeback.is_bare_insult(text), text)
            self.assertFalse(comeback.is_profanity(text), text)
            self.assertFalse(comeback.should_classify(text), text)
            self.replies.clear()
            self.classified.clear()
            result = self._handle(
                text,
                generate=lambda text, quoted=False: "就這句。",
            )
            self.assertEqual(result, "comeback", text)
            self.assertEqual(self.classified, [])
            self.assertEqual(
                self.replies,
                [comeback.COMEBACK_PREFIX + "就這句。"],
            )

    def test_training_profile_failure_still_replies(self):
        self.classify_result = False

        def broken(group_id, user_id):
            raise RuntimeError("404 profile")

        with self.assertLogs("papaya.comeback", level="INFO") as logs:
            result = comeback.handle_group_text(
                "哪個賤貨在群裡",
                "Unew",
                "Ctrain",
                self.replies.append,
                broken,
                self._classify,
                "Uowner",
                training=True,
                generate=lambda text, quoted=False: "就這句。",
            )

        self.assertEqual(result, "comeback")
        self.assertEqual(self.classified, [])
        self.assertEqual(self.replies, ["敬告 群友：就這句。"])
        output = "\n".join(logs.output)
        self.assertIn("profile error: 404 profile", output)
        self.assertIn("path=generated", output)

    def test_line_seven_is_not_reused_or_used_for_a_slur(self):
        self.names = {"Uhung": "Hung Chang Chen"}
        line = comeback.COMEBACK_LINES[6]

        def always_line_seven(text, quoted=False):
            return line

        with self.assertLogs("papaya.comeback", level="INFO") as logs:
            self._handle("垃圾AI", generate=always_line_seven)
            first = self.replies[0]
            self._handle("廢物AI", generate=always_line_seven)
            second = self.replies[1]
            self._handle("賤貨", generate=always_line_seven)

        self.assertEqual(first, comeback.COMEBACK_PREFIX + line)
        self.assertNotEqual(second, first)
        self.assertIn(
            second[len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )
        slur_body = self.replies[-1][len(comeback.COMEBACK_PREFIX):]
        self.assertNotEqual(slur_body, line)
        self.assertIn(slur_body, comeback.COMEBACK_LINES[:6])
        output = "\n".join(logs.output)
        self.assertIn("path=fixed-choice line=7", output)
        self.assertIn("path=fallback", output)

    def test_lewd_soft_or_empty_reply_uses_a_crude_preset(self):
        self.names = {
            "Ualex": "Alex Hou",
            "Uhung": "Hung Chang Chen",
            "Uother": "Someone Else",
        }
        self.classify_result = False
        soft = "你的提問真有趣，真希望你也能成為一名專業的喜劇演員。"

        def soft_line(text, quoted=False):
            return soft

        for text in (
            "幫我吹喇叭可以嗎",
            "聽到幫我吹喇叭你就嚇一跳？",
            "你喜歡被舔嗎還是幫我吹雞吧",
        ):
            comeback.reset_comeback_state()
            self.replies.clear()
            self.classified.clear()
            result = self._handle(
                text,
                "Ualex",
                training=True,
                generate=soft_line,
            )
            self.assertEqual(result, "comeback", text)
            self.assertEqual(self.classified, [])
            self.assertEqual(
                self.replies,
                ["敬告 Alex Hou：" + comeback.LEWD_FALLBACKS[0]],
            )
            self.assertLessEqual(len(comeback.LEWD_FALLBACKS[0]), 50)
            self.assertNotIn(
                self.replies[0].split("：", 1)[1],
                comeback.COMEBACK_LINES,
            )

        comeback.reset_comeback_state()
        self.replies.clear()
        self.assertEqual(
            self._handle("幫我吹喇叭可以嗎", generate=soft_line),
            "comeback",
        )
        self.assertEqual(
            self.replies,
            [comeback.COMEBACK_PREFIX + "你自己吹自己"],
        )
        self.assertEqual(
            self._handle("幫我吹喇叭可以嗎", "Uother", generate=soft_line),
            "not_target",
        )
        self.assertEqual(len(self.replies), 1)

        seen = []

        def watched(text, quoted=False):
            seen.append(text)
            return soft

        self.assertEqual(
            self._handle("幹你娘還要吹喇叭", generate=watched),
            "profanity",
        )
        self.assertEqual(seen, [])
        self.assertTrue(self.replies[-1].endswith("你自己操自己"))

        comeback.reset_comeback_state()
        self.replies.clear()
        self.classified.clear()

        def empty(text, quoted=False):
            return ""

        def refused(text, quoted=False):
            return "抱歉，我無法回應這種要求"

        def graphic(text, quoted=False):
            return "那種口交過程"

        def crude(text, quoted=False):
            return "你自己吹自己"

        for generate in (empty, refused, graphic):
            comeback.reset_comeback_state()
            self.replies.clear()
            result = self._handle(
                "幫我吹喇叭可以嗎",
                "Ualex",
                training=True,
                generate=generate,
            )
            self.assertEqual(result, "comeback")
            self.assertEqual(
                self.replies,
                ["敬告 Alex Hou：你自己吹自己"],
            )

        comeback.reset_comeback_state()
        self.replies.clear()
        self.assertEqual(
            self._handle(
                "幫我吹喇叭可以嗎",
                "Ualex",
                training=True,
                generate=crude,
            ),
            "comeback",
        )
        self.assertEqual(self.replies, ["敬告 Alex Hou：你自己吹自己"])

        comeback.reset_comeback_state()
        self.replies.clear()
        bodies = []
        for text in (
            "幫我吹喇叭可以嗎",
            "你喜歡被舔嗎",
            "幫我吹雞吧",
        ):
            self._handle(
                text,
                "Ualex",
                training=True,
                generate=soft_line,
            )
            bodies.append(self.replies[-1].split("：", 1)[1])
        self.assertEqual(bodies, list(comeback.LEWD_FALLBACKS))

    def test_followup_taunts_need_a_recent_lewd_line(self):
        self.names = {
            "Ualex": "Alex Hou",
            "Uhung": "Hung Chang Chen",
        }
        self.classify_result = False

        self.assertEqual(
            self._handle("怎麼不講話了喔", "Ualex", training=True),
            "not_insult",
        )
        self.assertEqual(
            self._handle(
                "還是你不知道這是什麼意思？",
                "Ualex",
                training=True,
            ),
            "not_insult",
        )
        self.assertEqual(self.replies, [])
        self.assertEqual(
            self.classified,
            ["怎麼不講話了喔", "還是你不知道這是什麼意思？"],
        )

        comeback.reset_comeback_state()
        self.replies.clear()
        self.classified.clear()
        self._handle(
            "幫我吹喇叭可以嗎",
            "Ualex",
            training=True,
            generate=lambda text, quoted=False: "",
        )
        self.classified.clear()

        def sharp(text, quoted=False):
            return "就這水準？"

        for text in (
            "怎麼不講話了喔",
            "還是你不知道這是什麼意思？",
        ):
            result = self._handle(
                text,
                "Ualex",
                training=True,
                generate=sharp,
            )
            self.assertEqual(result, "comeback", text)
            self.assertTrue(self.replies[-1].endswith("就這水準？"), text)

        self.assertEqual(self.classified, [])

        comeback.reset_comeback_state()
        self.replies.clear()
        self.classified.clear()
        self.assertEqual(
            self._handle(
                "笑她不敢回",
                generate=sharp,
            ),
            "comeback",
        )
        self.assertEqual(self.classified, [])
        self.assertEqual(
            self.replies,
            [comeback.COMEBACK_PREFIX + "就這水準？"],
        )
        self.assertEqual(
            self._handle("我不敢回去"),
            "not_insult",
        )

        comeback.reset_comeback_state()
        self.replies.clear()
        result = self._handle(
            "垃圾AI",
            generate=lambda text, quoted=False: "抱歉，我無法回應",
        )
        self.assertEqual(result, "comeback")
        body = self.replies[-1][len(comeback.COMEBACK_PREFIX):]
        self.assertIn(body, comeback.COMEBACK_LINES)
        self.assertNotEqual(body, "你自己吹自己")

    def test_training_profile_failure_still_replies_to_lewd(self):
        self.classify_result = False

        def broken(group_id, user_id):
            raise RuntimeError("404 profile")

        result = comeback.handle_group_text(
            "幫我吹喇叭可以嗎",
            "Unew",
            "Ctrain",
            self.replies.append,
            broken,
            self._classify,
            "Uowner",
            training=True,
            generate=lambda text, quoted=False: "",
        )

        self.assertEqual(result, "comeback")
        self.assertEqual(self.classified, [])
        self.assertEqual(self.replies, ["敬告 群友：你自己吹自己"])

    def test_second_identical_paste_replies_then_resets(self):
        self.names = {
            "Uhung": "Hung Chang Chen",
            "Ualex": "Alex Hou",
            "Uother": "Someone Else",
        }
        self.classify_result = False
        line = comeback.COPY_PASTE_REPLY
        seen = []

        def generate(text, quoted=False):
            seen.append(text)
            return "就這水準？"

        self.assertEqual(self._handle("  今天天氣真好  "), "not_insult")
        self.assertEqual(self.replies, [])
        self.assertEqual(self.classified, ["今天天氣真好"])

        self.classified.clear()
        result = self._handle("今天天氣真好", generate=generate)
        self.assertEqual(result, "copy_paste")
        self.assertEqual(self.classified, [])
        self.assertEqual(seen, [])
        self.assertEqual(
            self.replies,
            [comeback.COMEBACK_PREFIX + line],
        )
        self.assertEqual(ord(comeback.COMEBACK_PREFIX[-1]), 0xFF1A)
        self.assertIn("死", line)
        self.assertLessEqual(len(line), 50)

        self.classified.clear()
        self.assertEqual(
            self._handle("今天天氣真好", generate=generate),
            "copy_paste",
        )
        self.assertEqual(self.classified, [])
        self.assertEqual(len(self.replies), 2)
        self.assertEqual(
            self.replies[-1],
            comeback.COMEBACK_PREFIX + line,
        )

        self.classified.clear()
        self.assertEqual(
            self._handle("今天天氣真好", generate=generate),
            "copy_paste",
        )
        self.assertEqual(len(self.replies), 3)
        self.assertEqual(
            self.replies[-1],
            comeback.COMEBACK_PREFIX + line,
        )
        self.assertEqual(seen, [])

        before = len(self.replies)
        self.assertEqual(self._handle("同一句", "Uother"), "not_target")
        self.assertEqual(self._handle("同一句", "Uother"), "not_target")
        self.assertEqual(len(self.replies), before)

        comeback.reset_comeback_state()
        self.replies.clear()
        self.classified.clear()
        self.assertEqual(
            self._handle("同一句", "Ualex", training=True),
            "not_insult",
        )
        self.assertEqual(
            self._handle("同一句", "Ualex", training=True, generate=generate),
            "copy_paste",
        )
        self.assertEqual(self.replies, ["敬告 Alex Hou：" + line])
        self.assertEqual(seen, [])

        comeback.reset_comeback_state()
        self.replies.clear()
        self.assertEqual(self._handle("幹你娘", generate=generate), "profanity")
        self.assertEqual(self._handle("幹你娘", generate=generate), "profanity")
        self.assertEqual(
            self.replies,
            [comeback.COMEBACK_PREFIX + "你自己操自己"] * 2,
        )
        self.assertEqual(seen, [])

        comeback.reset_comeback_state()
        self.replies.clear()
        seen.clear()
        self.assertEqual(self._handle("垃圾AI", generate=generate), "comeback")
        self.assertEqual(
            self._handle("垃圾AI", generate=generate),
            "copy_paste",
        )
        self.assertEqual(seen, ["垃圾AI"])
        self.assertEqual(self.replies[-1], comeback.COMEBACK_PREFIX + line)

        comeback.reset_comeback_state()
        self.replies.clear()
        self.classified.clear()
        self.assertEqual(self._handle("複製 貼上"), "not_insult")
        self.assertEqual(self._handle("複製貼上"), "not_insult")
        self.assertEqual(self.replies, [])

        self.assertEqual(self._handle("吵架模式關", "Uowner"), "toggle_off")
        self.assertEqual(self._handle("關掉之後"), "disabled")
        self.assertEqual(self._handle("關掉之後"), "disabled")
        self.assertEqual(self._handle("吵架模式開", "Uowner"), "toggle_on")
        self.assertEqual(self._handle("關掉之後"), "not_insult")
        self.assertEqual(len(self.replies), 2)

    def test_copy_paste_is_per_sender_and_forgets_old_text(self):
        self.assertFalse(comeback.observe_copy_paste("G", "U1", "  甲  "))
        self.assertFalse(comeback.observe_copy_paste("G", "U2", "甲"))
        self.assertFalse(comeback.observe_copy_paste("G2", "U1", "甲"))
        self.assertTrue(comeback.observe_copy_paste("G", "U1", "甲"))
        self.assertTrue(comeback.observe_copy_paste("G", "U1", "甲"))
        self.assertTrue(comeback.observe_copy_paste("G", "U1", "甲"))

        self.assertFalse(comeback.observe_copy_paste("G", "U3", "乙"))
        for index in range(comeback.COPY_PASTE_HISTORY - 1):
            self.assertFalse(
                comeback.observe_copy_paste("G", "U3", "其他%d" % index)
            )
        self.assertTrue(comeback.observe_copy_paste("G", "U3", "乙"))
        self.assertTrue(comeback.observe_copy_paste("G", "U3", "乙"))

        self.assertFalse(comeback.observe_copy_paste("G", "U4", "丙"))
        for index in range(comeback.COPY_PASTE_HISTORY):
            self.assertFalse(
                comeback.observe_copy_paste("G", "U4", "別的%d" % index)
            )
        self.assertFalse(comeback.observe_copy_paste("G", "U4", "丙"))

        comeback.reset_comeback_state()
        self.assertFalse(comeback.observe_copy_paste("G", "U1", "甲"))
        self.assertFalse(comeback.observe_copy_paste("", "U1", "甲"))
        self.assertFalse(comeback.observe_copy_paste("G", "", "甲"))
        self.assertEqual(comeback.normalize_paste_text("  甲  "), "甲")
        self.assertEqual(comeback.normalize_paste_text(None), "")

        self.assertTrue(
            comeback.accept_webhook_event({"webhookEventId": "a"})
        )
        self.assertFalse(
            comeback.accept_webhook_event({"webhookEventId": "a"})
        )
        self.assertFalse(
            comeback.accept_webhook_event({
                "webhookEventId": "fresh",
                "deliveryContext": {"isRedelivery": True},
            })
        )
        self.assertTrue(
            comeback.accept_webhook_event({
                "webhookEventId": "fresh",
                "deliveryContext": {"isRedelivery": False},
            })
        )
        self.assertTrue(comeback.accept_webhook_event({"type": "message"}))
        self.assertFalse(
            comeback.accept_webhook_event({
                "deliveryContext": {"isRedelivery": True},
            })
        )
        self.assertFalse(comeback.accept_webhook_event(None))

        comeback.reset_comeback_state()
        self.assertTrue(
            comeback.accept_webhook_event({"webhookEventId": "a"})
        )
        for index in range(comeback.SEEN_WEBHOOK_EVENT_LIMIT):
            self.assertTrue(
                comeback.accept_webhook_event({
                    "webhookEventId": "n%d" % index,
                })
            )
        self.assertTrue(
            comeback.accept_webhook_event({"webhookEventId": "a"})
        )


class GenerateTests(unittest.TestCase):

    def setUp(self):
        os.environ.pop("COMEBACK_REPLY_MODEL", None)

    def tearDown(self):
        os.environ.pop("COMEBACK_REPLY_MODEL", None)

    def _client(self, outputs):
        seen = []

        class Response:

            def __init__(self, text):
                self.output_text = text

        class Client:

            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                seen.append(kwargs)
                result = outputs.pop(0)
                if isinstance(result, Exception):
                    raise result
                return Response(result)

        return Client(), seen

    def test_short_reply_is_used_as_the_body(self):
        client, seen = self._client(["「就這水準？」"])

        body = comeback.generate_comeback(client, "垃圾AI")

        self.assertEqual(body, "就這水準？")
        self.assertLessEqual(len(body), comeback.COMEBACK_MAX_BODY_CHARS)
        self.assertEqual(seen[0]["model"], "gpt-4o-mini")
        self.assertNotIn("reasoning", seen[0])
        self.assertIn("垃圾AI", seen[0]["input"])
        self.assertIn("50", seen[0]["instructions"])
        self.assertIn("敬告", seen[0]["instructions"])
        self.assertEqual(len(seen), 1)

    def test_long_reply_retries_once_then_uses_the_short_one(self):
        client, seen = self._client([
            "長" * 51,
            "你也配嫌。",
        ])

        body = comeback.generate_comeback(
            client,
            "這台機器人在亂講",
            quoted=True,
        )

        self.assertEqual(body, "你也配嫌。")
        self.assertEqual(len(seen), 2)
        self.assertIn("（這則是回覆訊息）", seen[1]["input"])
        self.assertIn("50 個字以內", seen[1]["input"])

    def test_still_long_reply_is_truncated_cleanly(self):
        client, seen = self._client([
            "啊" * 51,
            ("測" * 49) + "，多餘",
        ])

        body = comeback.generate_comeback(client, "回得什麼鬼")

        self.assertEqual(body, "測" * 49)
        self.assertLessEqual(len(body), 50)
        self.assertEqual(len(seen), 2)
        self.assertFalse(body.endswith("，"))

    def test_failure_and_forbidden_text_fall_back(self):
        client, seen = self._client([RuntimeError("timeout")])

        self.assertEqual(
            comeback.generate_comeback(client, "垃圾AI"),
            "",
        )
        self.assertEqual(len(seen), 1)

        client, seen = self._client(["去你媽的", "智障一個"])

        self.assertEqual(
            comeback.generate_comeback(client, "人工智障"),
            "",
        )
        self.assertEqual(len(seen), 2)

    def test_echoed_prefix_is_removed_before_counting(self):
        client, _seen = self._client([
            "敬告 Hung Chang Chen：就這句。",
        ])

        self.assertEqual(
            comeback.generate_comeback(client, "木瓜姊好爛"),
            "就這句。",
        )

    def test_json_choice_uses_that_fixed_line(self):
        line = comeback.COMEBACK_LINES[6]
        client, seen = self._client([
            '{"choice": 7, "text": "不要用這句"}',
        ])

        body = comeback.generate_comeback(client, "垃圾AI")

        self.assertEqual(body, line)
        self.assertEqual(len(seen), 1)
        self.assertIn("7. " + line, seen[0]["instructions"])
        self.assertIn("choice", seen[0]["instructions"])

    def test_json_choice_zero_keeps_a_short_new_line(self):
        client, seen = self._client([
            '{"choice": 0, "text": "就這句。"}',
        ])

        self.assertEqual(
            comeback.generate_comeback(client, "回得什麼鬼"),
            "就這句。",
        )
        self.assertEqual(len(seen), 1)

    def test_fenced_string_choice_selects_the_numbered_line(self):
        client, seen = self._client([
            '```json\n{"choice": "4", "text": ""}\n```',
        ])

        self.assertEqual(
            comeback.generate_comeback(client, "AI很爛"),
            comeback.COMEBACK_LINES[3],
        )
        self.assertEqual(len(seen), 1)

    def test_long_json_text_retries_then_accepts_a_line(self):
        client, seen = self._client([
            '{"choice": 0, "text": "' + ("啊" * 51) + '"}',
            '{"choice": 2, "text": ""}',
        ])

        body = comeback.generate_comeback(
            client,
            "這台機器人在亂講",
            quoted=True,
        )

        self.assertEqual(body, comeback.COMEBACK_LINES[1])
        self.assertEqual(len(seen), 2)
        self.assertIn("（這則是回覆訊息）", seen[1]["input"])
        self.assertIn("50 個字以內", seen[1]["input"])

    def test_reply_call_uses_temperature_one(self):
        client, seen = self._client(['{"choice": 0, "text": "就這句。"}'])

        comeback.generate_comeback(client, "醜八怪")

        self.assertEqual(seen[0]["temperature"], 1.0)
        self.assertNotIn("reasoning", seen[0])

    def test_recent_fixed_line_is_not_reused(self):
        line = comeback.COMEBACK_LINES[6]
        client, seen = self._client([
            '{"choice": 7, "text": "鏡子同意"}',
        ])

        body = comeback.generate_comeback(
            client,
            "醜八怪",
            avoid=[line],
        )

        self.assertEqual(body, "鏡子同意")
        self.assertNotEqual(body, line)
        self.assertEqual(len(seen), 1)
        self.assertIn("最近用過、不要再用：\n" + line, seen[0]["input"])
        self.assertEqual(seen[0]["temperature"], 1.0)

    def test_repeated_choice_without_a_fresh_line_is_rejected(self):
        line = comeback.COMEBACK_LINES[6]
        client, seen = self._client([
            '{"choice": 7, "text": ""}',
            '{"choice": 7, "text": ""}',
        ])

        self.assertEqual(
            comeback.generate_comeback(client, "大白癡", avoid=(line,)),
            "",
        )
        self.assertEqual(len(seen), 2)
        self.assertIn(line, seen[0]["input"])
        self.assertIn(line, seen[1]["input"])
        self.assertIn("50 個字以內", seen[1]["input"])

    def test_repeated_free_text_retries_then_uses_a_new_line(self):
        client, seen = self._client([
            "就這水準？",
            "換句話說。",
        ])

        body = comeback.generate_comeback(
            client,
            "低能兒",
            avoid=["就這水準？", "連 AI 都懶得跟你計較，你自己品味一下。"],
        )

        self.assertEqual(body, "換句話說。")
        self.assertEqual(len(seen), 2)
        self.assertIn(
            "就這水準？\n連 AI 都懶得跟你計較，你自己品味一下。",
            seen[0]["input"],
        )

    def test_choice_seven_is_not_used_for_an_unrelated_slur(self):
        line = comeback.COMEBACK_LINES[6]
        client, seen = self._client([
            '{"choice": 7, "text": "就這句。"}',
        ])

        body = comeback.generate_comeback(client, "超級賤貨")

        self.assertEqual(body, "就這句。")
        self.assertNotEqual(body, line)
        self.assertEqual(len(seen), 1)

        client, seen = self._client([
            '{"choice": 7, "text": ""}',
            '{"choice": 7, "text": ""}',
        ])
        self.assertEqual(
            comeback.generate_comeback(client, "性無能"),
            "",
        )
        self.assertEqual(len(seen), 2)

    def test_context_is_passed_into_the_reply_prompt(self):
        client, seen = self._client([
            '{"choice": 0, "text": "沒看就先判。"}',
        ])

        body = comeback.generate_comeback(
            client,
            "我都不看垃圾AI產出來的東西",
            context=(
                ("路人", "這模型還可以"),
                ("Hung Chang Chen", "我都不看垃圾AI產出來的東西"),
            ),
        )

        self.assertEqual(body, "沒看就先判。")
        self.assertIn(
            "最近對話（舊到新）：\n路人：這模型還可以\n"
            "Hung Chang Chen：我都不看垃圾AI產出來的東西",
            seen[0]["input"],
        )
        self.assertIn("對方剛說：", seen[0]["input"])
        self.assertIn("50", seen[0]["instructions"])

    def test_crude_mode_drops_a_soft_line_and_keeps_a_clapback(self):
        soft = (
            '{"choice": 0, "text": "'
            "你的提問真有趣，真希望你也能成為一名專業的喜劇演員。"
            '"}'
        )
        client, seen = self._client([
            soft,
            '{"choice": 0, "text": "抱歉，我無法"}',
        ])

        self.assertEqual(
            comeback.generate_comeback(
                client,
                "幫我吹喇叭可以嗎",
                crude=True,
            ),
            "",
        )
        self.assertEqual(len(seen), 2)
        self.assertIn("你自己吹自己", seen[0]["instructions"])
        self.assertIn("50", seen[0]["instructions"])
        self.assertNotIn("禁止髒話", seen[0]["instructions"])
        self.assertNotIn("垃圾回覆，專送垃圾人", seen[0]["instructions"])
        self.assertEqual(seen[0]["temperature"], 1.0)

        client, seen = self._client([
            '{"choice": 7, "text": "尺寸自己心裡有數"}',
        ])
        self.assertEqual(
            comeback.generate_comeback(client, "吹雞", crude=True),
            "尺寸自己心裡有數",
        )
        self.assertEqual(len(seen), 1)

        client, seen = self._client([
            '{"choice": 0, "text": "就這句。"}',
        ])
        comeback.generate_comeback(client, "垃圾AI")
        self.assertIn("禁止髒話", seen[0]["instructions"])
        self.assertIn("真的很貼", seen[0]["instructions"])

    def test_classify_input_includes_context_only_when_given(self):
        seen = {}

        class Response:

            def __init__(self, text):
                self.output_text = text

        class Client:

            def __init__(self):
                self.responses = self

            def create(self, **kwargs):
                seen["kwargs"] = kwargs
                return Response("yes")

        comeback.classify_insult(
            Client(),
            "怎麼不講話了喔",
            context=(("Alex Hou", "幫我吹喇叭可以嗎"),),
        )
        self.assertIn("最近對話（舊到新）：", seen["kwargs"]["input"])
        self.assertIn("Alex Hou：幫我吹喇叭可以嗎", seen["kwargs"]["input"])
        self.assertIn("這則訊息：\n怎麼不講話了喔", seen["kwargs"]["input"])

        comeback.classify_insult(Client(), "問了也是白問")
        self.assertEqual(seen["kwargs"]["input"], "問了也是白問")
        self.assertNotIn("最近對話", seen["kwargs"]["input"])


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
        self.group_names = {}
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
        if url.rstrip("/").endswith("/summary"):
            group_id = url.rstrip("/").rsplit("/", 2)[-2]
            return FakeHttpResponse({
                "groupId": group_id,
                "groupName": self.group_names.get(group_id, ""),
            })
        user_id = url.rstrip("/").rsplit("/", 1)[-1]
        return FakeHttpResponse({
            "displayName": self.names.get(user_id, ""),
        })

    def _member_gets(self):
        return [
            item for item in self.gets
            if "/member/" in item["url"]
        ]

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
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["model"], "gpt-4o-mini")
        self.assertNotIn("你就是「木瓜姐」", self.calls[0]["instructions"])
        self.assertEqual(len(self._member_gets()), 1)
        self.assertEqual(
            self._member_gets()[0]["url"],
            "https://api.line.me/v2/bot/group/Cgroup/member/Uhung",
        )
        self.assertEqual(
            self._member_gets()[0]["headers"]["Authorization"],
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
        self.assertTrue(
            messages[0]["text"].startswith(
                "敬告 Hung Chang Chen："
            )
        )
        self.assertIn(
            messages[0]["text"][len("敬告 Hung Chang Chen："):],
            comeback.COMEBACK_LINES,
        )

        again = self._post_event(
            self._text_event(
                "AI很爛",
                user_id="Uhung",
                source_type="group",
            )
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self.gets), 2)
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
        self.assertEqual(len(self._member_gets()), 2)
        self.assertEqual(
            len([
                item for item in self.gets
                if item["url"].endswith("/summary")
            ]),
            1,
        )

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
        self.assertEqual(self._member_gets(), [])

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
        self.assertEqual(self._member_gets(), [])

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
        sent = self.posts[-1]["json"]["messages"][0]["text"]
        self.assertTrue(sent.startswith(comeback.COMEBACK_PREFIX))
        self.assertIn(
            sent[len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )
        self.assertNotEqual(sent, comeback.TOGGLE_ON_REPLY)
        self.assertNotEqual(sent, comeback.STATUS_ON_REPLY)
        self.assertEqual(len(self._member_gets()), 1)

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
        self.assertEqual(self._member_gets(), [])
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
        self.assertEqual(self._member_gets(), [])
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

    def test_meaning_insult_is_classified_only_for_the_target(self):
        self._scripted.append(FakeChatResponse("yes"))
        event = self._text_event(
            "問了也是白問",
            user_id="Uhung",
            source_type="group",
        )
        event["message"]["quotedMessageId"] = "quoted-1"

        response = self._post_event(event)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("問了也是白問", self.calls[0]["instructions"])
        self.assertIn("最近對話（舊到新）：", self.calls[0]["input"])
        self.assertIn("（這則是回覆訊息）\n", self.calls[0]["input"])
        self.assertIn("問了也是白問", self.calls[0]["input"])
        self.assertEqual(len(self.posts), 1)
        sent = self.posts[0]["json"]["messages"][0]["text"]
        self.assertTrue(sent.startswith(comeback.COMEBACK_PREFIX))
        self.assertIn(
            sent[len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )

        before_calls = len(self.calls)
        other = self._text_event(
            "問了也是白問",
            user_id="Uother",
            source_type="group",
        )
        other["message"]["quotedMessageId"] = "quoted-2"
        self.names["Uother"] = "Other Person"
        quiet = self._post_event(other)

        self.assertEqual(quiet.status_code, 200)
        self.assertEqual(len(self.calls), before_calls)
        self.assertEqual(len(self.posts), 1)

    def test_training_group_uses_the_sender_name_and_logs_the_group(self):
        self.group_names["Ctrain"] = "自動回覆訓練區"
        self.names["Uowner"] = "侯鎮邦"
        text = "垃圾AI"

        with self.assertLogs("papaya.group", level="INFO") as captured:
            spoken = self._post_event(
                self._text_event(
                    text,
                    user_id="Uowner",
                    source_type="group",
                    group_id="Ctrain",
                )
            )
            image = {
                "type": "message",
                "replyToken": "reply-token",
                "source": {
                    "type": "group",
                    "userId": "Uowner",
                    "groupId": "Ctrain",
                },
                "message": {
                    "type": "image",
                    "id": "img-secret",
                },
            }
            quiet = self._post_event(image)

        self.assertEqual(spoken.status_code, 200)
        self.assertEqual(quiet.status_code, 200)
        logged = "\n".join(captured.output)
        self.assertEqual(logged.count("groupId=Ctrain"), 2)
        self.assertEqual(logged.count("groupName=自動回覆訓練區"), 2)
        self.assertNotIn(text, logged)
        self.assertNotIn("img-secret", logged)
        self.assertEqual(len(self.posts), 1)
        sent = self.posts[0]["json"]["messages"][0]["text"]
        self.assertTrue(sent.startswith("敬告 侯鎮邦："))
        self.assertFalse(sent.startswith(comeback.COMEBACK_PREFIX))
        self.assertIn(
            sent[len("敬告 侯鎮邦："):],
            comeback.COMEBACK_LINES,
        )
        self.assertEqual(
            [
                item["url"] for item in self.gets
                if item["url"].endswith("/summary")
            ],
            ["https://api.line.me/v2/bot/group/Ctrain/summary"],
        )
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["model"], "gpt-4o-mini")

    def test_other_named_group_still_ignores_other_senders(self):
        self.group_names["Cpig"] = "燒豬群"
        self.names["Uowner"] = "侯鎮邦"

        response = self._post_event(
            self._text_event(
                "垃圾AI",
                user_id="Uowner",
                source_type="group",
                group_id="Cpig",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.calls, [])

        self.names["Uhung"] = "Hung Chang Chen"
        hit = self._post_event(
            self._text_event(
                "垃圾AI",
                user_id="Uhung",
                source_type="group",
                group_id="Cpig",
            )
        )

        self.assertEqual(hit.status_code, 200)
        self.assertEqual(len(self.posts), 1)
        self.assertTrue(
            self.posts[0]["json"]["messages"][0]["text"].startswith(
                comeback.COMEBACK_PREFIX
            )
        )

    def test_webhook_sends_the_generated_body_with_the_prefix(self):
        self._scripted.append(FakeChatResponse("就這水準？"))

        response = self._post_event(
            self._text_event(
                "垃圾AI",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "敬告 Hung Chang Chen：就這水準？",
        )
        self.assertEqual(self.calls[0]["model"], "gpt-4o-mini")
        self.assertEqual(self.calls[0]["temperature"], 1.0)
        self.assertIn("垃圾AI", self.calls[0]["input"])

        self.group_names["Ctrain"] = "自動回覆訓練區"
        self.names["Uowner"] = "侯鎮邦"
        self._scripted.append(FakeChatResponse("你也配嫌。"))
        trained = self._post_event(
            self._text_event(
                "機器人真的很蠢",
                user_id="Uowner",
                source_type="group",
                group_id="Ctrain",
            )
        )

        self.assertEqual(trained.status_code, 200)
        self.assertEqual(
            self.posts[-1]["json"]["messages"][0]["text"],
            "敬告 侯鎮邦：你也配嫌。",
        )

    def test_webhook_truncates_then_falls_back_on_timeout(self):
        self._scripted.extend([
            FakeChatResponse("啊" * 51),
            FakeChatResponse(("測" * 49) + "，多餘"),
        ])

        clipped = self._post_event(
            self._text_event(
                "垃圾AI",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(clipped.status_code, 200)
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "敬告 Hung Chang Chen：" + ("測" * 49),
        )
        self.assertEqual(len(self.calls), 2)

        self._scripted.append(RuntimeError("timeout"))
        failed = self._post_event(
            self._text_event(
                "AI很爛",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(len(self.calls), 3)
        self.assertIn(
            "最近用過、不要再用：\n" + ("測" * 49),
            self.calls[-1]["input"],
        )
        self.assertEqual(failed.status_code, 200)
        sent = self.posts[-1]["json"]["messages"][0]["text"]
        self.assertTrue(sent.startswith(comeback.COMEBACK_PREFIX))
        self.assertIn(
            sent[len(comeback.COMEBACK_PREFIX):],
            comeback.COMEBACK_LINES,
        )

    def test_profanity_replies_without_calling_openai(self):
        before = len(self.calls)
        response = self._post_event(
            self._text_event(
                "操 你 媽",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls), before)
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "敬告 Hung Chang Chen：你自己操自己",
        )

        self.names["Uother"] = "Other Person"
        quiet = self._post_event(
            self._text_event(
                "幹你娘",
                user_id="Uother",
                source_type="group",
            )
        )
        self.assertEqual(quiet.status_code, 200)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.calls), before)

        self.group_names["Ctrain"] = "自動回覆訓練區"
        self.names["Uowner"] = "侯鎮邦"
        trained = self._post_event(
            self._text_event(
                "靠杯",
                user_id="Uowner",
                source_type="group",
                group_id="Ctrain",
            )
        )
        self.assertEqual(trained.status_code, 200)
        self.assertEqual(
            self.posts[-1]["json"]["messages"][0]["text"],
            "敬告 侯鎮邦：你自己操自己",
        )
        self.assertEqual(len(self.calls), before)

    def test_webhook_sends_the_numbered_fixed_line(self):
        line = comeback.COMEBACK_LINES[3]
        self._scripted.append(
            FakeChatResponse('{"choice": 4, "text": "別用我"}')
        )

        response = self._post_event(
            self._text_event(
                "垃圾AI",
                user_id="Uhung",
                source_type="group",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            comeback.COMEBACK_PREFIX + line,
        )

    def test_webhook_lewd_soft_line_falls_back_to_the_crude_preset(self):
        self.group_names["Ctrain"] = "自動回覆訓練區"
        self.names["Ualex"] = "Alex Hou"
        soft = (
            '{"choice": 0, "text": "'
            "你的提問真有趣，真希望你也能成為一名專業的喜劇演員。"
            '"}'
        )
        self._scripted.append(FakeChatResponse(soft))
        self._scripted.append(FakeChatResponse(
            '{"choice": 0, "text": "抱歉，我無法回應這種要求"}'
        ))

        response = self._post_event(
            self._text_event(
                "幫我吹喇叭可以嗎",
                user_id="Ualex",
                source_type="group",
                group_id="Ctrain",
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.calls), 2)
        self.assertIn("你自己吹自己", self.calls[0]["instructions"])
        self.assertNotIn("禁止髒話", self.calls[0]["instructions"])
        self.assertEqual(self.calls[0]["temperature"], 1.0)
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(
            self.posts[0]["json"]["messages"][0]["text"],
            "敬告 Alex Hou：你自己吹自己",
        )
        self.assertNotIn("push", self.posts[0]["url"])

    def test_one_webhook_event_gets_at_most_one_paste_reply(self):
        text = "難民第三代的邦哥逃避"
        self._scripted.append(FakeChatResponse("no"))
        first = self._text_event(
            text,
            user_id="Uhung",
            source_type="group",
        )
        first["webhookEventId"] = "evt-1"
        first["deliveryContext"] = {"isRedelivery": False}

        quiet = self._post_event(first)

        self.assertEqual(quiet.status_code, 200)
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.calls), 1)

        duplicate = self._post_event(first)
        redelivery = self._text_event(
            text,
            user_id="Uhung",
            source_type="group",
        )
        redelivery["webhookEventId"] = "evt-1b"
        redelivery["deliveryContext"] = {"isRedelivery": True}
        skipped = self._post_event(redelivery)

        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(skipped.status_code, 200)
        self.assertEqual(self.posts, [])
        self.assertEqual(len(self.calls), 1)

        line = "敬告 Hung Chang Chen：" + comeback.COPY_PASTE_REPLY
        for event_id in ("evt-2", "evt-3", "evt-4"):
            event = self._text_event(
                text,
                user_id="Uhung",
                source_type="group",
            )
            event["webhookEventId"] = event_id
            event["replyToken"] = "token-" + event_id
            response = self._post_event(event)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                self.posts[-1]["json"]["messages"],
                [{"type": "text", "text": line}],
            )
            self.assertEqual(
                self.posts[-1]["json"]["replyToken"],
                "token-" + event_id,
            )

        self.assertEqual(len(self.posts), 3)
        self.assertEqual(len(self.calls), 1)

        burst = self._text_event(
            text,
            user_id="Uhung",
            source_type="group",
        )
        burst["webhookEventId"] = "evt-burst"
        burst["replyToken"] = "token-burst"
        body = json.dumps({
            "events": [burst, burst, burst, burst],
        }).encode("utf-8")
        batched = self.client.post(
            "/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Line-Signature": self._sign(body),
            },
        )

        self.assertEqual(batched.status_code, 200)
        self.assertEqual(len(self.posts), 4)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(
            self.posts[-1]["json"]["replyToken"],
            "token-burst",
        )
        self.assertEqual(len(self.posts[-1]["json"]["messages"]), 1)


if __name__ == "__main__":
    unittest.main()

"""Group-chat comeback mode for the LINE bot.

In a group, a message from the display name "Hung Chang Chen" that
insults the bot, the AI, or its replies gets one short reply. The
reply uses the webhook reply token. Push is never used.

The on/off switch starts from COMEBACK_MODE (default on) and can be
overridden in memory by the owner. A process restart drops the
override and the display-name cache, then starts from the env default
again. Render's one-worker gunicorn process is the intended setup.
"""

import os
import random
import re
import threading
import time
import unicodedata


TARGET_DISPLAY_NAME = "hung chang chen"
DISPLAY_NAME_TTL_SECONDS = 12 * 60 * 60
CLASSIFY_MODEL_DEFAULT = "gpt-4o-mini"
CLASSIFY_TIMEOUT_SECONDS = 8

TOGGLE_ON_TEXT = "吵架模式開"
TOGGLE_OFF_TEXT = "吵架模式關"
TOGGLE_ON_REPLY = "吵架模式開了。"
TOGGLE_OFF_REPLY = "吵架模式關了。"

# Short game-banter. No slurs, no family profanity, no appearance,
# disability, ethnicity, or sex, and no threats.
COMEBACK_LINES = (
    "垃圾回覆，專送垃圾人。",
    "你丟什麼，我就回什麼。",
    "嫌爛？這則就是你的份。",
    "水準在這，回覆就在這。",
    "垃圾進，垃圾出，公平。",
    "這種話，只配這種回。",
)

# Words that disparage the bot when they sit on a target.
INSULT_HEADS = (
    "垃圾",
    "廢物",
    "廢柴",
    "智障",
    "腦殘",
    "笨蛋",
    "白痴",
    "白癡",
    "低能",
    "沒用",
    "狗屁",
    "狗屎",
    "扯淡",
    "靠北",
    "三小",
    "有病",
    "機車",
    "爛",
    "廢",
    "蠢",
    "傻",
    "笨",
    "鳥",
)

INSULT_TAILS = (
    "垃圾",
    "廢物",
    "廢柴",
    "智障",
    "腦殘",
    "笨蛋",
    "白痴",
    "白癡",
    "低能",
    "沒用",
    "爛透了",
    "爛透",
    "狗屁",
    "狗屎",
    "扯淡",
    "靠北",
    "三小",
    "有病",
    "機車",
    "糟",
    "遜",
    "菜",
    "雷",
    "爛",
    "廢",
    "蠢",
    "傻",
    "笨",
    "差",
)

# "AI" is matched as its own token (also "A.I."), case-insensitive.
TARGETS = (
    "AI",
    "人工智慧",
    "機器人",
    "机器人",
    "回覆",
    "回复",
    "木瓜姊",
    "木瓜姐",
    "木瓜妹",
)

ENGLISH_INSULTS = (
    "garbage",
    "trash",
    "stupid",
    "dumb",
    "useless",
    "lame",
    "worst",
    "shitty",
    "bad",
)

# Soft phrasing that is not a keyword hit. Classified only after the
# display name matches, and only for a short message.
AMBIGUOUS_CUES = (
    "不行",
    "不太好",
    "討厭",
    "無言",
    "傻眼",
    "誇張",
    "壞掉",
    "沒救",
    "完蛋",
    "可惡",
    "噁心",
    "惡心",
    "瞎扯",
    "有點扯",
    "很扯",
    "真扯",
    "太扯",
    "有夠扯",
)

_BENIGN_PHRASES = (
    "垃圾分類",
    "垃圾車",
    "垃圾袋",
    "垃圾桶",
    "垃圾筒",
    "垃圾量",
    "回收垃圾",
)

_PARTICLES = (
    "真的有夠",
    "真的很",
    "真的太",
    "真的是",
    "怎麼這麼",
    "有一點",
    "實在太",
    "也太",
    "有夠",
    "就是",
    "還真",
    "真是",
    "實在",
    "真的",
    "有點",
    "真",
    "很",
    "好",
    "超",
    "太",
    "是",
    "也",
    "都",
    "挺",
    "蠻",
    "滿",
    "最",
    "夠",
    "略",
)

_MIDDLES = (
    "品質",
    "內容",
    "水準",
    "程度",
    "回答",
)

_FILLERS = (
    "一個",
    "一種",
    "這款",
    "那種",
    "這個",
    "這種",
    "那個",
    "個",
    "款",
)

_HEAD_GUARDS = {
    "廢": "(?![棄氣話])",
    "垃圾": "(?!分類|車|袋|桶|筒|量)",
}

_TAIL_GUARDS = {
    "廢": "(?![棄氣話])",
    "差": "(?!異)",
    "垃圾": "(?!分類|車|袋|桶|筒|量)",
}

_AI = "\uE000"

_OFF_VALUES = {
    "0",
    "off",
    "false",
    "no",
    "關",
}

CLASSIFY_INSTRUCTIONS = (
    "判斷這則群組訊息是不是在侮辱、貶低或嘲諷"
    "「AI、人工智慧、機器人，或這隻機器人的回覆」。\n"
    "是的話只回答 yes，否則只回答 no。不要解釋。\n"
    "稱讚、提問、閒聊、討論別的主題、垃圾分類或垃圾車、"
    "以及沒有針對 AI／機器人／回覆的話，回答 no。\n"
    "不確定就回答 no。"
)


def _alt(words, guards=None):

    guards = guards or {}
    ordered = sorted(words, key=len, reverse=True)
    parts = [
        re.escape(word) + guards.get(word, "")
        for word in ordered
    ]

    return "(?:" + "|".join(parts) + ")"


def _target_pattern():

    words = [word for word in TARGETS if word != "AI"]
    ordered = sorted(words, key=len, reverse=True)

    return "(?:" + _AI + "|" + "|".join(
        re.escape(word) for word in ordered
    ) + ")"


def _compile_strong():

    head = _alt(INSULT_HEADS, _HEAD_GUARDS)
    tail = _alt(INSULT_TAILS, _TAIL_GUARDS)
    target = _target_pattern()
    particle = _alt(_PARTICLES)
    middle = _alt(_MIDDLES)
    filler = _alt(_FILLERS)
    english = _alt(ENGLISH_INSULTS) + r"(?![a-z])"

    direct = rf"{head}(?:的)?{target}"
    follow = (
        rf"{target}(?:{middle})?(?:得|的)?"
        rf"(?:{particle}){{0,3}}(?:{filler})?{tail}"
    )
    en_direct = rf"{english}{_AI}"
    en_follow = (
        rf"(?:{_AI}(?:is)?(?:really|so|too|very|the){{0,2}}"
        rf"{english}|{_AI}(?:really|so|too|very)?sucks?(?![a-z]))"
    )

    return re.compile(
        rf"(?:{direct}|{follow}|{en_direct}|{en_follow}|人工智障)"
    )


_STRONG = _compile_strong()
_TARGET_RE = re.compile(_target_pattern() + r"|人工智障")

_enabled_override = None
_state_lock = threading.Lock()

_names = {}
_name_lock = threading.Lock()


def normalize_display_name(name):

    if not isinstance(name, str):
        return ""

    name = unicodedata.normalize("NFKC", name)
    name = name.strip().lower()
    name = re.sub(r"\s+", " ", name)

    return name


def is_target_sender(name):

    return normalize_display_name(name) == TARGET_DISPLAY_NAME


def normalize_insult_text(text):

    if not isinstance(text, str):
        return ""

    text = unicodedata.normalize("NFKC", text).lower()
    text = text.replace(_AI, "")
    text = re.sub(
        r"(?<![a-z0-9])a\s*\.\s*i\s*\.?(?![a-z0-9])",
        _AI,
        text,
    )
    text = re.sub(
        r"(?<![a-z0-9])ai(?![a-z0-9])",
        _AI,
        text,
    )
    text = re.sub(_AI + r"\.+", _AI, text)
    text = re.sub(r"\s+", "", text)

    return text


def is_insult(text):

    norm = normalize_insult_text(text)

    if not norm:
        return False

    return _STRONG.search(norm) is not None


def is_ambiguous_insult(text):

    if is_insult(text):
        return False

    norm = normalize_insult_text(text)

    if not norm or len(norm) > 40:
        return False

    if _TARGET_RE.search(norm) is None:
        return False

    for phrase in _BENIGN_PHRASES:
        norm = norm.replace(phrase, "")

    return any(cue in norm for cue in AMBIGUOUS_CUES)


def parse_toggle(text):

    if not isinstance(text, str):
        return None

    compact = unicodedata.normalize("NFKC", text)
    compact = re.sub(r"\s+", "", compact)
    compact = re.sub(r"[。.!！?？~～]+$", "", compact)

    if compact == TOGGLE_ON_TEXT:
        return True

    if compact == TOGGLE_OFF_TEXT:
        return False

    return None


def _env_enabled():

    raw = os.environ.get("COMEBACK_MODE")

    if raw is None:
        return True

    value = raw.strip().lower()

    if value == "":
        return True

    return value not in _OFF_VALUES


def comeback_enabled():

    with _state_lock:

        if _enabled_override is None:
            return _env_enabled()

        return _enabled_override


def set_comeback_enabled(enabled):

    global _enabled_override

    with _state_lock:
        _enabled_override = bool(enabled)


def reset_comeback_state():

    global _enabled_override

    with _state_lock:
        _enabled_override = None

    clear_display_name_cache()


def pick_comeback(rng=None):

    choose = random.choice if rng is None else rng.choice

    return choose(COMEBACK_LINES)


def get_cached_display_name(user_id, now=None):

    if not user_id:
        return None

    now = time.time() if now is None else now

    with _name_lock:

        entry = _names.get(user_id)

        if entry is None:
            return None

        cached_at, name = entry

        if now - cached_at > DISPLAY_NAME_TTL_SECONDS:
            _names.pop(user_id, None)
            return None

        return name


def cache_display_name(user_id, name, now=None):

    if not user_id:
        return

    now = time.time() if now is None else now

    if not isinstance(name, str):
        name = ""

    with _name_lock:
        _names[user_id] = (now, name)


def clear_display_name_cache():

    with _name_lock:
        _names.clear()


def fetch_group_display_name(
    group_id,
    user_id,
    http_get,
    token,
    now=None,
):

    if not group_id or not user_id:
        return ""

    cached = get_cached_display_name(user_id, now=now)

    if cached is not None:
        return cached

    url = (
        "https://api.line.me/v2/bot/group/"
        f"{group_id}/member/{user_id}"
    )

    response = http_get(
        url,
        headers={
            "Authorization": f"Bearer {token}"
        },
        timeout=5,
    )

    response.raise_for_status()

    payload = response.json()
    name = ""

    if isinstance(payload, dict):

        raw = payload.get("displayName")

        if isinstance(raw, str):
            name = raw

    cache_display_name(user_id, name, now=now)

    return name


def _classify_model():

    model = (os.environ.get("COMEBACK_CLASSIFY_MODEL") or "").strip()

    return model or CLASSIFY_MODEL_DEFAULT


def _answer_is_yes(answer):

    if not isinstance(answer, str):
        return False

    token = answer.strip().lower()

    if not token:
        return False

    first = token.split()[0].strip("。.!！,，:：")

    if first in {"yes", "y"} or first.startswith("yes"):
        return True

    compact = re.sub(r"\s+", "", token).strip("。.!！,，")

    return compact in {"是", "yes", "y"}


def classify_insult(openai_client, text):

    model = _classify_model()
    kwargs = {
        "model": model,
        "instructions": CLASSIFY_INSTRUCTIONS,
        "input": (text or "")[:500],
        "max_output_tokens": 32,
        "timeout": CLASSIFY_TIMEOUT_SECONDS,
    }

    if "gpt-5" in model:
        kwargs["reasoning"] = {"effort": "minimal"}
        kwargs["max_output_tokens"] = 200

    try:

        response = openai_client.responses.create(**kwargs)

    except Exception as exc:

        print(
            "Comeback classify error:",
            type(exc).__name__
        )

        return False

    answer = getattr(response, "output_text", None) or ""

    return _answer_is_yes(answer)


def _toggle_allowed(user_id, owner_user_id):

    if not isinstance(owner_user_id, str) or owner_user_id == "":
        return True

    return user_id == owner_user_id


def handle_group_text(
    text,
    user_id,
    group_id,
    reply,
    get_display_name,
    classify,
    owner_user_id,
):

    if not isinstance(text, str):
        text = "" if text is None else str(text)

    stripped = text.strip()

    if not stripped:
        return "ignore"

    toggle = parse_toggle(stripped)

    if toggle is not None:

        if not _toggle_allowed(user_id, owner_user_id):
            return "toggle_ignored"

        set_comeback_enabled(toggle)

        if toggle:
            reply(TOGGLE_ON_REPLY)
            return "toggle_on"

        reply(TOGGLE_OFF_REPLY)
        return "toggle_off"

    if not comeback_enabled():
        return "disabled"

    strong = is_insult(stripped)

    if not strong and not is_ambiguous_insult(stripped):
        return "ignore"

    try:

        name = get_display_name(group_id, user_id)

    except Exception as exc:

        print(
            "Comeback profile error:",
            type(exc).__name__
        )

        return "lookup_error"

    if not is_target_sender(name):
        return "not_target"

    if not strong:

        try:

            insulting = bool(classify(stripped))

        except Exception as exc:

            print(
                "Comeback classify error:",
                type(exc).__name__
            )

            return "classify_error"

        if not insulting:
            return "not_insult"

    reply(pick_comeback())

    return "comeback"

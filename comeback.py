"""Group-chat comeback mode for the LINE bot.

In a group, a message from the display name "Hung Chang Chen" that
insults or mocks the bot gets one short reply. A bare insult, an
appearance jab, an intelligence jab, or a sexual-function jab from
that sender counts even when it never names the bot. Lewd innuendo
(吹喇叭, 吹雞, 被舔) is answered in the same crude register as the
profanity rule, and a soft or refused line is replaced by a crude
preset. A direct taunt such as 不敢回 triggers on its own. A weaker
follow-up triggers only when the recent messages were already lewd.
Keyword hits reply immediately. Any other non-empty text from that
sender, up to CLASSIFY_MAX_CHARS, is classified by meaning, with the
recent messages attached. Before that, the second and every later
identical paste from the same sender gets one fixed copy-paste line.
A redelivered webhook is not another paste. Other senders are never
classified. A triggered reply is written for that message. A fixed line is used
only when it fits that insult; otherwise the body is a new line kept
to COMEBACK_MAX_BODY_CHARS characters. A failure uses one of the
fixed lines. The reply uses the webhook reply token. Push is never
used. Each group remembers the last few reply bodies so the next
comeback does not repeat them.

The group named 自動回覆訓練區 is the exception: any sender counts,
and the reply prefix uses that sender's display name. The group name
comes from LINE Get group summary and is cached per group id.

The on/off switch starts from COMEBACK_MODE (default off). The owner
can flip that same global switch from the 1:1 private chat or from a
group. A process restart drops the override, the display-name
cache, the group-name cache, the per-group reply history, the
per-sender paste history, and the recent webhook event ids, then
starts from the env default again. Render's one-worker gunicorn
process is the intended setup.
"""

import json
import logging
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
CLASSIFY_MAX_CHARS = 300
REPLY_MODEL_DEFAULT = "gpt-4o-mini"
REPLY_TEMPERATURE = 1.0
COMEBACK_MAX_BODY_CHARS = 50
RECENT_REPLY_LIMIT = 5
CONTEXT_LIMIT = 5
COPY_PASTE_HISTORY = 20
SEEN_WEBHOOK_EVENT_LIMIT = 1000
BOT_CONTEXT_NAME = "木瓜姊"

COMEBACK_LOGGER = logging.getLogger("papaya.comeback")
COMEBACK_LOGGER.setLevel(logging.INFO)
if not COMEBACK_LOGGER.handlers:
    _comeback_log_handler = logging.StreamHandler()
    _comeback_log_handler.setLevel(logging.INFO)
    _comeback_log_handler.setFormatter(
        logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    COMEBACK_LOGGER.addHandler(_comeback_log_handler)
COMEBACK_LOGGER.propagate = False

# Slurs and sexual-insult jabs. They trigger without the classifier
# and without a nearby AI/機器人/回覆 word.
BARE_INSULTS = (
    "賤貨",
    "賤人",
    "賤胚",
    "賤種",
    "下賤",
    "性無能",
    "性功能障礙",
    "性障礙",
)
TRAINING_NAME_FALLBACK = "群友"

TOGGLE_ON_TEXT = "吵架模式開"
TOGGLE_OFF_TEXT = "吵架模式關"
TOGGLE_ON_REPLY = "吵架模式開了。"
TOGGLE_OFF_REPLY = "吵架模式關了。"
STATUS_TEXT = "吵架模式狀態"
STATUS_ON_REPLY = "吵架模式現在是開的。"
STATUS_OFF_REPLY = "吵架模式現在是關的。"
COMEBACK_PREFIX = "敬告 Hung Chang Chen："
TRAINING_GROUP_NAME = "自動回覆訓練區"
PROFANITY_REPLY = "你自己操自己"
COPY_PASTE_REPLY = "笑死，燒豬又在複製貼上。大當機！大失敗！"

# Crude clapbacks for lewd innuendo. Not graphic. Used when the model
# refuses, goes soft, or returns nothing. Rotated when just sent.
LEWD_FALLBACKS = (
    "你自己吹自己",
    "尺寸自己心裡有數",
    "嘴上厲害，本事呢",
)

# Slang that is a sexual request or joke, not a musical instrument.
# A bare 喇叭 does not match. 幫我吹 does not swallow 吹牛.
# These are not the profanity rule.
LEWD_PHRASE_RULES = (
    ("吹喇叭", ()),
    ("吹雞", ()),
    ("吹鸡", ()),
    ("吹鷄", ()),
    ("吹簫", ()),
    ("吹箫", ()),
    ("被舔", ()),
    ("舔我", ()),
    ("幫我舔", ()),
    ("帮我舔", ()),
    ("幫我吹", ("牛",)),
    ("帮我吹", ("牛",)),
)

# Always a taunt, with no need for the recent lewd line.
# 不敢回 must not swallow 不敢回去.
DIRECT_TAUNT_RULES = (
    ("不敢回", ("去",)),
    ("不敢講", ()),
    ("不敢說", ()),
    ("不敢说", ()),
    ("嚇一跳", ()),
    ("吓一跳", ()),
)

# Only a taunt when this group just had a lewd line.
FOLLOWUP_TAUNTS = (
    "怎麼不",
    "怎么不",
    "不講話了",
    "不讲话了",
    "什麼意思",
    "什么意思",
)

# The generated body is checked again in code. Fixed fallback lines
# are not passed through this list.
REPLY_FORBIDDEN = (
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
    "操",
    "靠腰",
    "屎",
    "fuck",
    "shit",
    "bitch",
)

REPLY_INSTRUCTIONS = (
    "你是群組裡的機器人「木瓜姐」。對方剛剛侮辱、嘲諷或貶低你。"
    "沒有點名也算在罵你。\n"
    "只有某一句真的很貼這句特定的嘲諷，才選它的編號。"
    "泛用、跟這幾個字對不上的，不要選。\n"
    "對不上就自己寫一句。先看最近對話，弄懂這句在罵誰、罵什麼，"
    "再針對那個意思回，不要拿句裡單獨一個字做雙關。"
    "每次換一種說法，不要跟最近用過的句子一樣。\n"
    "只輸出一個 JSON，不要加敬告、名字、前言或解釋：\n"
    '{"choice": 0, "text": ""}\n'
    "choice 是 1 到 9：那一句真的很貼，text 留空。\n"
    "choice 是 0：自己寫。text 最多 50 個字，標點也算。\n"
    "可以刻薄、挖苦、諷刺。禁止髒話、詛咒、威脅、色情、歧視，"
    "也禁止辱罵家人、外貌、殘疾或族群。"
    "對方罵外貌或性功能時，不要把那些字原樣寫回去，"
    "也不要寫露骨的性內容，換個角度挖苦。\n"
    "九句是：\n"
)

# Lewd innuendo only. The default instructions above stay polite on
# purpose; they are what made 吹喇叭 come back as a compliment.
LEWD_REPLY_INSTRUCTIONS = (
    "你是群組裡的機器人「木瓜姐」。對方剛用黃色暗示、鹹濕玩笑或性騷擾鬧你"
    "（吹喇叭、吹雞、被舔這類）。你聽得懂。用同樣粗、同樣賤的垃圾話頂回去，"
    "把話甩回他身上，嘲笑他的尺寸或本事，像「你自己吹自己」。\n"
    "不要禮貌，不要稱讚，不要道歉，不要說教。\n"
    "不要寫露骨的性行為、器官過程或教學。不要加敬告、名字、前言或解釋。\n"
    "只輸出一個 JSON，choice 固定 0：\n"
    '{"choice": 0, "text": ""}\n'
    "text 最多 50 個字，標點也算。"
)

# A model refusal or a polite dodge. Soft markers apply only to the
# lewd path; the nine-line path may still be sharp without them.
REFUSAL_MARKERS = (
    "抱歉",
    "無法",
    "我不能",
    "沒辦法",
    "不適合",
    "違法",
    "拒絕",
    "不好意思",
    "不方便",
)
SOFT_MARKERS = (
    "有趣",
    "希望",
    "專業",
    "喜劇",
    "謝謝",
    "可愛",
    "加油",
    "尊重",
    "請不要",
    "這樣不好",
)
GRAPHIC_MARKERS = (
    "口交",
    "性交",
    "肛交",
    "插入",
    "射精",
    "陰莖",
    "陰蒂",
    "陰道",
    "肉棒",
    "小穴",
    "精液",
    "做愛",
)

# Short game-banter. No slurs, no family profanity, no appearance,
# disability, ethnicity, or sex, and no threats.
COMEBACK_LINES = (
    "垃圾回覆，專送垃圾人。",
    "你丟什麼，我就回什麼。",
    "嫌爛？這則就是你的份。",
    "水準在這，回覆就在這。",
    "垃圾進，垃圾出，公平。",
    "這種話，只配這種回。",
    "連 AI 都懶得跟你計較，你自己品味一下。",
    "你的見識，就跟你的發言一樣廉價。",
    "罵得這麼用力，可惜沒半個人在聽。",
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

# Older soft cues. The handler no longer requires one of these
# before calling the classifier.
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
    "判斷這則群組訊息的意思是不是在侮辱、嘲諷、貶低或看不起"
    "這隻機器人（木瓜姊／木瓜姐）、AI、人工智慧、機器人，或它的回覆。\n"
    "同義詞、俚語、諷刺、諧諷、簡體、中英夾雜都算。"
    "是的話只回答 yes，否則只回答 no。不要解釋。\n"
    "下面這種意思要回答 yes："
    "垃圾AI、垃圾垃圾回覆、木瓜姊好爛、機器人真的很蠢、"
    "AI的回答有夠廢、這台機器人在亂講、回得什麼鬼、人工智障、問了也是白問。\n"
    "這則已經是會被回的人說的。沒有點名機器人也算在罵木瓜姊。"
    "光罵人、罵外貌、罵智商、拿性功能來嘲，都要回答 yes："
    "醜八怪、你性功能障礙、白癡、大白癡、低能兒、胸大無腦、白癡加三級。\n"
    "若訊息開頭標了「（這則是回覆訊息）」，代表這則是直接回這隻機器人。"
    "這種情況下，很短的不屑、嘲弄或否定也要回答 yes，"
    "例如：呵呵、好爛、廢物、亂講、無言、白問。\n"
    "黃色暗示、鹹濕玩笑、性騷擾，或叫你做那種事，用問句包裝也要回答 yes："
    "幫我吹喇叭、吹雞、被舔、你喜歡被舔嗎。\n"
    "最近對話顯示對方剛在騷擾，或在笑你不敢回、不講話、嚇一跳、"
    "不知道什麼意思，接話也要回答 yes："
    "怎麼不講話了、嚇一跳、不知道什麼意思、笑她不敢回。\n"
    "稱讚、提問、閒聊、討論別的主題、垃圾分類、垃圾車，回答 no。\n"
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
_recent_replies = {}
_recent_context = {}
_recent_pastes = {}
_seen_webhook_ids = set()
_seen_webhook_order = []

_names = {}
_name_lock = threading.Lock()

_group_names = {}
_group_lock = threading.Lock()


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


_PROFANITY_NI = r"(?:你|妳|伱|尼|泥)"
_PROFANITY_MA = r"(?:媽|妈|馬|马|瑪|玛|嘛|麻)"
_PROFANITY_NIANG = r"(?:娘|孃)"
_PROFANITY_HEAD = r"(?:操|肏|草|槽|糙|吵|曹|抄|幹|干|淦|乾)"
_PROFANITY_FAMILY = (
    rf"(?:{_PROFANITY_NI}(?:"
    rf"{_PROFANITY_MA}|{_PROFANITY_NIANG}|家|老師|老师"
    rf")|{_PROFANITY_NI})"
)

# Phrases, after spaces and symbols are removed. Longer family
# targets are listed before a bare 你, so 幹你老師 still matches.
_PROFANITY_PHRASE = re.compile(
    rf"(?:{_PROFANITY_HEAD}{_PROFANITY_FAMILY}"
    rf"|(?:添|舔){_PROFANITY_NI}{_PROFANITY_NIANG}"
    rf"|x{_PROFANITY_NI}{_PROFANITY_NIANG}"
    rf"|靠(?:北|杯|背|悲)"
    rf"|(?<![a-z0-9])cnm(?![a-z0-9]))"
)

# 幹 / 淦 as an expletive. Benign compounds are removed first.
_STANDALONE_GAN = re.compile(r"幹|淦")
_BENIGN_GAN = tuple(sorted((
    "幹部",
    "干部",
    "幹事",
    "干事",
    "幹活",
    "干活",
    "樹幹",
    "树干",
    "幹嘛",
    "干嘛",
    "幹嗎",
    "干吗",
    "幹麼",
    "幹什麼",
    "干什麼",
    "干什么",
    "若干",
    "幹練",
    "干练",
    "幹勁",
    "干劲",
    "主幹",
    "主干",
    "幹道",
    "干道",
    "幹線",
    "干线",
    "骨幹",
    "骨干",
    "公幹",
    "公干",
    "幹校",
    "干校",
    "幹群",
    "干群",
    "幹警",
    "干警",
    "幹員",
    "干员",
    "幹淨",
    "干净",
), key=len, reverse=True))


def _compact_profanity(text):

    if not isinstance(text, str):
        return ""

    text = unicodedata.normalize("NFKC", text).lower()
    text = re.sub(
        r"(?<![0-9a-z\u4e00-\u9fff])[×✕✖*＊x](?=(?:你|妳|伱|尼|泥))",
        "x",
        text,
    )
    kept = []

    for char in text:

        if (
            "\u4e00" <= char <= "\u9fff"
            or "a" <= char <= "z"
            or "0" <= char <= "9"
        ):
            kept.append(char)

    return "".join(kept)


def is_profanity(text):

    compact = _compact_profanity(text)

    if not compact:
        return False

    if _PROFANITY_PHRASE.search(compact):
        return True

    scrubbed = compact

    for phrase in _BENIGN_GAN:
        scrubbed = scrubbed.replace(phrase, "")

    return _STANDALONE_GAN.search(scrubbed) is not None


def _compact_cjk(text):

    if not isinstance(text, str):
        return ""

    text = unicodedata.normalize("NFKC", text).lower()

    return "".join(
        char for char in text
        if "\u4e00" <= char <= "\u9fff"
    )


def _contains_phrase(compact, phrase, blocked_suffixes=()):

    start = 0

    while True:

        index = compact.find(phrase, start)

        if index < 0:
            return False

        rest = compact[index + len(phrase):]

        if not any(rest.startswith(suffix) for suffix in blocked_suffixes):
            return True

        start = index + 1


def is_lewd_harassment(text):

    compact = _compact_cjk(text)

    if not compact:
        return False

    return any(
        _contains_phrase(compact, phrase, blocked)
        for phrase, blocked in LEWD_PHRASE_RULES
    )


def is_direct_taunt(text):

    compact = _compact_cjk(text)

    if not compact:
        return False

    return any(
        _contains_phrase(compact, phrase, blocked)
        for phrase, blocked in DIRECT_TAUNT_RULES
    )


def is_followup_taunt(text):

    compact = _compact_cjk(text)

    if not compact:
        return False

    return any(phrase in compact for phrase in FOLLOWUP_TAUNTS)


def context_has_lewd(context):

    for item in context or ():

        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue

        said = item[1]

        if isinstance(said, str) and is_lewd_harassment(said):
            return True

    return False


def is_insult(text):

    norm = normalize_insult_text(text)

    if not norm:
        return False

    return _STRONG.search(norm) is not None


def is_bare_insult(text):

    norm = normalize_insult_text(text)

    if not norm:
        return False

    return any(word in norm for word in BARE_INSULTS)


def should_classify(text):

    if (
        is_insult(text)
        or is_bare_insult(text)
        or is_lewd_harassment(text)
        or is_direct_taunt(text)
    ):
        return False

    if not isinstance(text, str):
        return False

    stripped = text.strip()

    if not stripped or len(stripped) > CLASSIFY_MAX_CHARS:
        return False

    return True


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


def _compact_command(text):

    if not isinstance(text, str):
        return ""

    compact = unicodedata.normalize("NFKC", text)
    compact = re.sub(r"\s+", "", compact)
    compact = re.sub(r"[。.!！?？~～]+$", "", compact)

    return compact


def _english_command_word(text):

    if not isinstance(text, str):
        return ""

    word = unicodedata.normalize("NFKC", text).strip()

    if word.endswith(".") or word.endswith("。"):
        word = word[:-1].strip()

    if word == "" or re.search(r"\s", word):
        return ""

    return word.casefold()


def parse_toggle(text):

    compact = _compact_command(text)

    if compact == TOGGLE_ON_TEXT:
        return True

    if compact == TOGGLE_OFF_TEXT:
        return False

    word = _english_command_word(text)

    if word == "fight":
        return True

    if word == "close":
        return False

    return None


def is_status_command(text):

    if _compact_command(text) == STATUS_TEXT:
        return True

    return _english_command_word(text) == "check"


def _env_enabled():

    raw = os.environ.get("COMEBACK_MODE")

    if raw is None:
        return False

    value = raw.strip().lower()

    if value == "":
        return False

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
        _recent_replies.clear()
        _recent_context.clear()
        _recent_pastes.clear()
        _seen_webhook_ids.clear()
        _seen_webhook_order.clear()

    clear_display_name_cache()
    clear_group_name_cache()


def normalize_paste_text(text):

    if not isinstance(text, str):
        return ""

    return text.strip()


def observe_copy_paste(group_id, user_id, text):

    """Track one eligible message. True from the second identical paste on.

    Whitespace at the ends is ignored. The text stays remembered, so
    the third, fourth, and later copies each fire too. Only the last
    COPY_PASTE_HISTORY texts for that sender are kept.
    """

    if not isinstance(group_id, str) or group_id == "":
        return False

    if not isinstance(user_id, str) or user_id == "":
        return False

    norm = normalize_paste_text(text)

    if not norm:
        return False

    with _state_lock:

        by_user = _recent_pastes.setdefault(group_id, {})
        history = list(by_user.get(user_id, ()))

        if norm in history:
            return True

        history.append(norm)
        by_user[user_id] = history[-COPY_PASTE_HISTORY:]

        return False


def accept_webhook_event(event):

    """True when this delivery should be handled.

    Redeliveries are skipped. A webhookEventId is handled once, so
    one LINE event cannot produce a second reply. Events with no id
    are still handled; tests and older payloads do not send one.
    """

    if not isinstance(event, dict):
        return False

    delivery = event.get("deliveryContext")

    if isinstance(delivery, dict) and delivery.get("isRedelivery") is True:
        return False

    event_id = event.get("webhookEventId")

    if not isinstance(event_id, str) or event_id == "":
        return True

    with _state_lock:

        if event_id in _seen_webhook_ids:
            return False

        _seen_webhook_ids.add(event_id)
        _seen_webhook_order.append(event_id)

        overflow = len(_seen_webhook_order) - SEEN_WEBHOOK_EVENT_LIMIT

        if overflow > 0:

            for old in _seen_webhook_order[:overflow]:
                _seen_webhook_ids.discard(old)

            del _seen_webhook_order[:overflow]

        return True


def recent_replies(group_id):

    if not isinstance(group_id, str) or group_id == "":
        return ()

    with _state_lock:
        return tuple(_recent_replies.get(group_id, ()))


def remember_reply(group_id, body):

    if not isinstance(group_id, str) or group_id == "":
        return

    if not isinstance(body, str):
        return

    body = body.strip()

    if not body:
        return

    with _state_lock:

        history = list(_recent_replies.get(group_id, ()))
        history.append(body)
        _recent_replies[group_id] = history[-RECENT_REPLY_LIMIT:]


def recent_context(group_id):

    if not isinstance(group_id, str) or group_id == "":
        return ()

    with _state_lock:
        return tuple(_recent_context.get(group_id, ()))


def remember_context(group_id, name, text):

    if not isinstance(group_id, str) or group_id == "":
        return

    if not isinstance(text, str):
        return

    text = text.strip()[:CLASSIFY_MAX_CHARS]

    if not text:
        return

    if not isinstance(name, str) or not name.strip():
        name = "某人"
    else:
        name = name.strip()

    with _state_lock:

        history = list(_recent_context.get(group_id, ()))
        history.append((name, text))
        _recent_context[group_id] = history[-CONTEXT_LIMIT:]


def _context_block(context):

    lines = []

    for item in context or ():

        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue

        name, said = item[0], item[1]

        if not isinstance(said, str) or not said.strip():
            continue

        who = name.strip() if isinstance(name, str) and name.strip() else "某人"
        lines.append(who + "：" + said.strip())

    if not lines:
        return ""

    return "最近對話（舊到新）：\n" + "\n".join(lines[-CONTEXT_LIMIT:]) + "\n\n"


def _avoid_tuple(avoid):

    if not avoid:
        return ()

    if isinstance(avoid, str):
        return (avoid,)

    found = []

    for item in avoid:

        if isinstance(item, str) and item and item not in found:
            found.append(item)

    return tuple(found)


def _avoid_set(avoid):

    return set(_avoid_tuple(avoid))


def _cjk_bigrams(text):

    chars = [
        char for char in text
        if "\u4e00" <= char <= "\u9fff"
    ]

    return {
        chars[index] + chars[index + 1]
        for index in range(len(chars) - 1)
    }


def _fixed_line_fits(insult, line):

    # Lines 7–9 are generic. Honor them only when they share the
    # insult's words; otherwise the model parks on line 7 forever.
    if line not in COMEBACK_LINES[6:]:
        return True

    if not isinstance(insult, str) or not insult.strip():
        return True

    folded_insult = unicodedata.normalize("NFKC", insult).upper()
    folded_line = unicodedata.normalize("NFKC", line).upper()

    if "AI" in folded_insult and "AI" in folded_line:
        return True

    return bool(
        _cjk_bigrams(folded_insult) & _cjk_bigrams(folded_line)
    )


def pick_comeback(rng=None, avoid=(), insult=""):

    choose = random.choice if rng is None else rng.choice
    blocked = _avoid_set(avoid)
    pool = tuple(
        line for line in COMEBACK_LINES
        if line not in blocked and _fixed_line_fits(insult, line)
    )

    if not pool:
        pool = tuple(
            line for line in COMEBACK_LINES
            if line not in blocked
        )

    if not pool:
        pool = COMEBACK_LINES

    return choose(pool)


def _log_path(kind, line=""):

    if kind == "fixed-choice" and line in COMEBACK_LINES:

        COMEBACK_LOGGER.info(
            "comeback path=fixed-choice line=%s",
            COMEBACK_LINES.index(line) + 1,
        )

        return

    COMEBACK_LOGGER.info("comeback path=%s", kind)


def format_group_comeback(line):

    return COMEBACK_PREFIX + line


def format_training_comeback(line, display_name):

    if not isinstance(display_name, str):
        display_name = ""

    return "敬告 " + display_name + "：" + line


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


def get_cached_group_name(group_id, now=None):

    if not group_id:
        return None

    now = time.time() if now is None else now

    with _group_lock:

        entry = _group_names.get(group_id)

        if entry is None:
            return None

        cached_at, name = entry

        if now - cached_at > DISPLAY_NAME_TTL_SECONDS:
            _group_names.pop(group_id, None)
            return None

        return name


def cache_group_name(group_id, name, now=None):

    if not group_id:
        return

    now = time.time() if now is None else now

    if not isinstance(name, str):
        name = ""

    with _group_lock:
        _group_names[group_id] = (now, name)


def clear_group_name_cache():

    with _group_lock:
        _group_names.clear()


class GroupSummary:

    def __init__(self, group_id, group_name):

        self.groupId = group_id
        self.groupName = group_name


def get_group_summary(group_id, http_get, token, now=None):

    if not group_id:
        return GroupSummary("", "")

    cached = get_cached_group_name(group_id, now=now)

    if cached is not None:
        return GroupSummary(group_id, cached)

    url = (
        "https://api.line.me/v2/bot/group/"
        f"{group_id}/summary"
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
    summary_id = group_id

    if isinstance(payload, dict):

        raw_name = payload.get("groupName")

        if isinstance(raw_name, str):
            name = raw_name

        raw_id = payload.get("groupId")

        if isinstance(raw_id, str) and raw_id:
            summary_id = raw_id

    cache_group_name(group_id, name, now=now)

    return GroupSummary(summary_id, name)


def is_training_group(group_name):

    return group_name == TRAINING_GROUP_NAME


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


def classify_insult(openai_client, text, quoted=False, context=()):

    body = (text or "")[:CLASSIFY_MAX_CHARS]

    if quoted:
        body = "（這則是回覆訊息）\n" + body

    block = _context_block(context)

    if block:
        body = block + "這則訊息：\n" + body

    model = _classify_model()
    kwargs = {
        "model": model,
        "instructions": CLASSIFY_INSTRUCTIONS,
        "input": body,
        "max_output_tokens": 32,
        "timeout": CLASSIFY_TIMEOUT_SECONDS,
    }

    if "gpt-5" in model:
        kwargs["reasoning"] = {"effort": "minimal"}
        kwargs["max_output_tokens"] = 200

    try:

        response = openai_client.responses.create(**kwargs)

    except Exception as exc:

        COMEBACK_LOGGER.warning("comeback classify error: %s", exc)

        return False

    answer = getattr(response, "output_text", None) or ""

    return _answer_is_yes(answer)


def _reply_instructions():

    numbered = "\n".join(
        f"{index}. {line}"
        for index, line in enumerate(COMEBACK_LINES, start=1)
    )

    return REPLY_INSTRUCTIONS + numbered


def _reply_model():

    model = (os.environ.get("COMEBACK_REPLY_MODEL") or "").strip()

    return model or REPLY_MODEL_DEFAULT


def prepare_comeback_body(raw):

    if not isinstance(raw, str):
        return ""

    text = ""

    for line in raw.splitlines():

        line = line.strip()

        if line:
            text = line
            break

    if not text:
        return ""

    quotes = "\"'「」『』“”‘’"

    while (
        len(text) >= 2
        and text[0] in quotes
        and text[-1] in quotes
    ):
        text = text[1:-1].strip()

    if text.startswith("敬告"):

        for separator in ("：", ":"):

            if separator in text:
                text = text.split(separator, 1)[1].strip()
                break

    while (
        len(text) >= 2
        and text[0] in quotes
        and text[-1] in quotes
    ):
        text = text[1:-1].strip()

    return text.strip()


def comeback_body_forbidden(text):

    if not isinstance(text, str) or not text:
        return False

    lowered = text.lower()

    return any(
        word in text or word in lowered
        for word in REPLY_FORBIDDEN
    )


def truncate_comeback_body(text):

    if not isinstance(text, str):
        return ""

    if len(text) <= COMEBACK_MAX_BODY_CHARS:
        return text

    trimmed = text[:COMEBACK_MAX_BODY_CHARS].rstrip(
        " \t，,、。.!！?？~～…"
    )

    return trimmed


def _comeback_body_ok(text):

    return (
        isinstance(text, str)
        and bool(text)
        and len(text) <= COMEBACK_MAX_BODY_CHARS
        and not comeback_body_forbidden(text)
    )


def _finish_comeback_body(text):

    if not text or comeback_body_forbidden(text):
        return ""

    if len(text) <= COMEBACK_MAX_BODY_CHARS:
        return text

    trimmed = truncate_comeback_body(text)

    if _comeback_body_ok(trimmed):
        return trimmed

    return ""


def _reply_input(text, quoted, previous, avoid=(), context=()):

    body = (text or "").strip()[:CLASSIFY_MAX_CHARS]

    if quoted:
        body = "（這則是回覆訊息）\n" + body

    prompt = _context_block(context) + "對方剛說：\n" + body
    blocked = [
        item for item in avoid
        if isinstance(item, str) and item
    ]

    if blocked:

        shown = "\n".join(blocked[-RECENT_REPLY_LIMIT:])
        prompt += "\n\n最近用過、不要再用：\n" + shown

    if previous:

        prompt += (
            "\n\n上一句不合格或跟禁用句重複，不要再用："
            + previous[:40]
            + "\n請改成 50 個字以內的一句。"
        )

    return prompt


def _request_comeback(
    openai_client,
    text,
    quoted,
    previous,
    avoid=(),
    context=(),
    crude=False,
):

    model = _reply_model()
    kwargs = {
        "model": model,
        "instructions": (
            LEWD_REPLY_INSTRUCTIONS if crude else _reply_instructions()
        ),
        "input": _reply_input(text, quoted, previous, avoid, context),
        "max_output_tokens": 256,
        "timeout": CLASSIFY_TIMEOUT_SECONDS,
    }

    # gpt-5 rejects temperature; sending it fails the call and the
    # reply falls through to a fixed line.
    if "gpt-5" in model:
        kwargs["reasoning"] = {"effort": "minimal"}
        kwargs["max_output_tokens"] = 400
    else:
        kwargs["temperature"] = REPLY_TEMPERATURE

    response = openai_client.responses.create(**kwargs)

    return getattr(response, "output_text", None) or ""


def _choice_number(raw):

    if isinstance(raw, bool) or raw is None:
        return None

    if isinstance(raw, int):
        return raw

    if isinstance(raw, str):

        token = raw.strip()

        if token.lstrip("-").isdigit():
            return int(token)

    return None


def _parse_reply_payload(raw):

    if not isinstance(raw, str) or "{" not in raw:
        return None

    try:

        payload, _end = json.JSONDecoder().raw_decode(
            raw.strip()[raw.strip().find("{"):]
        )

    except json.JSONDecodeError:
        return None

    if not isinstance(payload, dict) or "choice" not in payload:
        return None

    return payload


def _plain_reply_text(raw):

    payload = _parse_reply_payload(raw)

    if payload is not None:

        text = payload.get("text")

        if not isinstance(text, str):
            return ""

        return prepare_comeback_body(text)

    if not isinstance(raw, str):
        return ""

    return prepare_comeback_body(raw)


def _is_model_refusal(text):

    if not isinstance(text, str) or not text:
        return False

    return any(marker in text for marker in REFUSAL_MARKERS)


def _is_soft_line(text):

    if not isinstance(text, str) or not text:
        return False

    return any(marker in text for marker in SOFT_MARKERS)


def _is_graphic(text):

    if not isinstance(text, str) or not text:
        return False

    return any(marker in text for marker in GRAPHIC_MARKERS)


def _lewd_body_ok(text):

    return (
        _comeback_body_ok(text)
        and text not in COMEBACK_LINES
        and not _is_model_refusal(text)
        and not _is_soft_line(text)
        and not _is_graphic(text)
    )


def pick_lewd_fallback(avoid=()):

    blocked = _avoid_set(avoid)

    for line in LEWD_FALLBACKS:

        if line not in blocked:
            return line

    return LEWD_FALLBACKS[0]


def _read_reply(raw, avoid=(), insult="", lewd=False):

    blocked = _avoid_set(avoid)
    payload = _parse_reply_payload(raw)
    text = _plain_reply_text(raw)

    if lewd:

        if _lewd_body_ok(text) and text not in blocked:
            return text, ""

        return "", text or "不合格"

    if payload is not None:

        number = _choice_number(payload.get("choice"))

        if (
            number is not None
            and 1 <= number <= len(COMEBACK_LINES)
        ):

            line = COMEBACK_LINES[number - 1]

            if line not in blocked and _fixed_line_fits(insult, line):
                return line, ""

            if _comeback_body_ok(text) and text not in blocked:
                return text, ""

            return "", line

    if _comeback_body_ok(text) and text not in blocked:
        return text, ""

    return "", text or "不合格"


def _accept_finished(text, avoid):

    finished = _finish_comeback_body(text)

    if finished and finished not in _avoid_set(avoid):
        return finished

    return ""


def generate_comeback(
    openai_client,
    text,
    quoted=False,
    avoid=(),
    context=(),
    crude=False,
):

    blocked = _avoid_tuple(avoid)
    history = tuple(context or ())

    try:

        first_raw = _request_comeback(
            openai_client,
            text,
            quoted,
            None,
            blocked,
            history,
            crude=crude,
        )

    except Exception as exc:

        COMEBACK_LOGGER.warning("comeback reply error: %s", exc)

        return ""

    chosen, hint = _read_reply(first_raw, blocked, text, lewd=crude)

    if chosen:
        return chosen

    try:

        second_raw = _request_comeback(
            openai_client,
            text,
            quoted,
            hint,
            blocked,
            history,
            crude=crude,
        )

    except Exception as exc:

        COMEBACK_LOGGER.warning("comeback reply error: %s", exc)

        if crude:
            return ""

        return _accept_finished(_plain_reply_text(first_raw), blocked)

    chosen, _hint = _read_reply(second_raw, blocked, text, lewd=crude)

    if chosen:
        return chosen

    if crude:
        return ""

    finished = _accept_finished(_plain_reply_text(second_raw), blocked)

    if finished:
        return finished

    return _accept_finished(_plain_reply_text(first_raw), blocked)


def _comeback_line(text, quoted, generate, group_id=""):

    if generate is not None:

        try:

            generated = generate(text, quoted=bool(quoted))

        except Exception as exc:

            COMEBACK_LOGGER.warning("comeback reply error: %s", exc)

            generated = ""

        if isinstance(generated, str):
            generated = generated.strip()
        else:
            generated = ""

        if _is_model_refusal(generated):
            generated = ""

        recent = _avoid_set(recent_replies(group_id))
        repeated = (
            generated in COMEBACK_LINES and generated in recent
        )
        unfit = (
            generated in COMEBACK_LINES
            and not _fixed_line_fits(text, generated)
        )

        if (
            not repeated
            and not unfit
            and (
                generated in COMEBACK_LINES
                or _comeback_body_ok(generated)
            )
        ):

            kind = (
                "fixed-choice"
                if generated in COMEBACK_LINES
                else "generated"
            )
            _log_path(kind, generated)

            return generated

    line = pick_comeback(
        avoid=recent_replies(group_id),
        insult=text,
    )
    _log_path("fallback", line)

    return line


def _lewd_comeback_line(text, quoted, generate, group_id=""):

    generated = ""

    if generate is not None:

        try:

            generated = generate(text, quoted=bool(quoted))

        except Exception as exc:

            COMEBACK_LOGGER.warning("comeback reply error: %s", exc)

            generated = ""

        if isinstance(generated, str):
            generated = generated.strip()
        else:
            generated = ""

    recent = _avoid_set(recent_replies(group_id))

    if _lewd_body_ok(generated) and generated not in recent:

        _log_path("lewd")

        return generated

    line = pick_lewd_fallback(recent)
    _log_path("lewd-fallback")

    return line


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
    quoted=False,
    training=False,
    generate=None,
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

    if is_status_command(stripped):

        if not _toggle_allowed(user_id, owner_user_id):
            return "status_ignored"

        if comeback_enabled():
            reply(STATUS_ON_REPLY)
            return "status_on"

        reply(STATUS_OFF_REPLY)
        return "status_off"

    if not comeback_enabled():
        return "disabled"

    strong = is_insult(stripped) or is_bare_insult(stripped)
    profane = is_profanity(stripped)
    lewd = is_lewd_harassment(stripped)
    taunt = is_direct_taunt(stripped)

    if (
        not profane
        and not strong
        and not lewd
        and not taunt
        and not should_classify(stripped)
    ):
        return "ignore"

    try:

        name = get_display_name(group_id, user_id)

    except Exception as exc:

        COMEBACK_LOGGER.warning("comeback profile error: %s", exc)

        # The training room counts every sender. A failed group-member
        # profile lookup must not drop them.
        if not training:
            return "lookup_error"

        name = ""

    if not isinstance(name, str):
        name = ""

    if training and not name.strip():
        name = TRAINING_NAME_FALLBACK

    remember_context(group_id, name, stripped)

    if not training and not is_target_sender(name):
        return "not_target"

    if profane:

        if training:
            sent = format_training_comeback(PROFANITY_REPLY, name)
        else:
            sent = format_group_comeback(PROFANITY_REPLY)

        reply(sent)
        remember_context(group_id, BOT_CONTEXT_NAME, sent)
        remember_reply(group_id, PROFANITY_REPLY)
        _log_path("profanity")

        return "profanity"

    if observe_copy_paste(group_id, user_id, stripped):

        if training:
            sent = format_training_comeback(COPY_PASTE_REPLY, name)
        else:
            sent = format_group_comeback(COPY_PASTE_REPLY)

        reply(sent)
        remember_context(group_id, BOT_CONTEXT_NAME, sent)
        remember_reply(group_id, COPY_PASTE_REPLY)
        _log_path("copy-paste")

        return "copy_paste"

    followup = (
        not lewd
        and not taunt
        and is_followup_taunt(stripped)
        and context_has_lewd(recent_context(group_id))
    )

    if lewd:

        line = _lewd_comeback_line(
            stripped,
            quoted,
            generate,
            group_id,
        )

        if training:
            sent = format_training_comeback(line, name)
        else:
            sent = format_group_comeback(line)

        reply(sent)
        remember_context(group_id, BOT_CONTEXT_NAME, sent)
        remember_reply(group_id, line)

        return "comeback"

    if not strong and not taunt and not followup:

        try:

            insulting = bool(
                classify(stripped, quoted=bool(quoted))
            )

        except Exception as exc:

            COMEBACK_LOGGER.warning(
                "comeback classify error: %s",
                exc,
            )

            return "classify_error"

        if not insulting:
            return "not_insult"

    line = _comeback_line(stripped, quoted, generate, group_id)

    if training:
        sent = format_training_comeback(line, name)
    else:
        sent = format_group_comeback(line)

    reply(sent)
    remember_context(group_id, BOT_CONTEXT_NAME, sent)
    remember_reply(group_id, line)

    return "comeback"

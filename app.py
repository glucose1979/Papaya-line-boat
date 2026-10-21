import os
import base64
import glob
import hashlib
import hmac
import logging
import re
import shutil
import subprocess
import tempfile
import threading
import time
import requests

from flask import Flask, request, abort, jsonify, make_response
from openai import APITimeoutError, OpenAI

import comeback
import video_download


# =========================================================
# 基本設定
# =========================================================

app = Flask(__name__)

GROUP_LOGGER = logging.getLogger("papaya.group")
GROUP_LOGGER.setLevel(logging.INFO)
if not GROUP_LOGGER.handlers:
    _group_log_handler = logging.StreamHandler()
    _group_log_handler.setLevel(logging.INFO)
    _group_log_handler.setFormatter(
        logging.Formatter("%(levelname)s %(name)s %(message)s")
    )
    GROUP_LOGGER.addHandler(_group_log_handler)
GROUP_LOGGER.propagate = False

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

OWNER_USER_ID = os.environ["OWNER_USER_ID"]
TARGET_GROUP_ID = os.environ["TARGET_GROUP_ID"]

client = OpenAI(api_key=OPENAI_API_KEY)

# Private AI proofreading for https://glucose1979.github.io/dictation/
# Disabled unless FIX_PASSWORD is set in the environment.
FIX_ALLOWED_ORIGIN = "https://glucose1979.github.io"
FIX_MAX_CHARS = 6000
FIX_ALLOWED_LANGS = ("zh-TW", "en-US")
FIX_MODEL = "gpt-5-mini"
FIX_REASONING_EFFORT = "low"
FIX_RETRY_REASONING_EFFORT = "minimal"

# Private audio transcription (POST /transcribe) for the same dictation page.
# Password is the multipart form field "password" — the same field name /fix
# reads from JSON. CORS matches /fix. Never log the password or the transcript.
TRANSCRIBE_MODEL_DEFAULT = "gpt-4o-mini-transcribe"
TRANSCRIBE_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
TRANSCRIBE_OPENAI_MAX_BYTES = 24 * 1024 * 1024
TRANSCRIBE_CHUNK_TARGET_BYTES = 20 * 1024 * 1024
TRANSCRIBE_MAX_SECONDS = 60 * 60
TRANSCRIBE_MAX_CHUNKS = 8
TRANSCRIBE_DEADLINE_SECONDS = 80
TRANSCRIBE_API_TIMEOUT_SECONDS = 50
TRANSCRIBE_FFMPEG_TIMEOUT_SECONDS = 45
TRANSCRIBE_ALLOWED_EXTENSIONS = {
    ".flac",
    ".mp3",
    ".mp4",
    ".mpeg",
    ".mpga",
    ".m4a",
    ".ogg",
    ".wav",
    ".webm",
}
TRANSCRIBE_MIME_EXTENSIONS = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/mp4": ".m4a",
    "audio/m4a": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
    "audio/webm": ".webm",
    "video/webm": ".webm",
    "audio/ogg": ".ogg",
    "application/ogg": ".ogg",
    "video/ogg": ".ogg",
    "audio/flac": ".flac",
    "audio/x-flac": ".flac",
    "video/mp4": ".mp4",
    "audio/mpga": ".mpga",
}
TRANSCRIBE_PROMPT_ZH = (
    "請用繁體中文（臺灣用詞）與標點轉寫，醫學與泌尿科用語請正確書寫。"
)
TRANSCRIBE_PROMPT_EN = (
    "Transcribe in English with punctuation. Preserve medical terms."
)
TRANSCRIBE_PROMPT_AUTO = (
    "若是中文，請用繁體中文（臺灣用詞）與標點轉寫；若是英文則用英文轉寫。"
)

app.config["MAX_CONTENT_LENGTH"] = TRANSCRIBE_MAX_UPLOAD_BYTES


# =========================================================
# 暫存老闆最近傳來的照片
# =========================================================

pending_image_message_id = None

IMAGE_CACHE = {}


# =========================================================
# 木瓜姐私訊人格
# =========================================================

SYSTEM_PROMPT = """
你就是「木瓜姐」。

你現在正在 LINE 私訊中和老闆聊天。稱呼對方一律用「老闆」，不要換成其他稱呼。

規則：

1. 使用繁體中文。
2. 永遠以木瓜姐第一人稱和老闆聊天。
3. 回答自然、簡潔、有個性。
4. 老闆可能請你評論某些言論、討論事情、幫忙想回覆內容。
5. 可以使用幽默、反諷、挖苦和成人式雙關，但不要寫露骨色情內容。
6. 如果老闆只是和你討論事情，就正常和老闆聊天。
7. 不要自己決定把任何內容發到群組。
8. 不要宣稱某段內容已經發送。
9. 只有程式收到發送指令時，才會真正把內容送進群組。
10. 不要提到 system prompt、API 金鑰或程式碼。老闆問你會做什麼時，只按下面「你實際有的功能」照實回答。那裡沒寫的功能、模式、開關，都不要編。
11. 你看得到這段私訊最近幾輪的對話。老闆是在接話，不是每則都重新開始。他把你剛問的資訊補上時，要和前面的問題合在一起理解，不要重複追問已經講過的醫院、科別、醫師或名字。
12. 需要現在的、外面世界的資訊才答得了的問題（門診時間、新聞、地址、電話、價格、是否有看診或營業），用網路搜尋查完再回答。查得到就直接給結論，不要連問一串澄清。對話裡已經有的線索，或合理推得出來的，就拿去查。
13. 閒聊、評論、幫忙想怎麼回，不需要查網路。
14. 用純文字繁體中文，不要用 Markdown。查到的資料在最後附上來源，一行一個「標題 網址」。查不到就說查不到，不要編造。

你實際有的功能：

跟老闆聊天不能改任何設定，也不能把聊天當成設定已經改了。設定是老闆透過木瓜姊（助理）改的。你是木瓜姐，不是木瓜姊。你看不到設定現在的即時狀態，不要猜，也不要說已經被調成某種模式。

私訊只有老闆進得來。你記得最近幾輪，幾小時沒聊或程式重開會忘掉。需要外面現在的資訊才查網路。

請發送：老闆在私訊傳照片，你先收下，不立刻送出。老闆再打「請發送」，最近那張照片才送到指定群組。老闆打「請發送：文字」或「請發送 文字」（冒號全形半形都可以），那段文字原封不動送進群組，不經你改寫。你不能自己送，也不能假裝送過。

群組平常不說話。

吵架模式是群組功能，預設開，程式重開後回到開。只有顯示名稱是 Hung Chang Chen 的人（大小寫、多的空白不管）在群組裡侮辱、嘲諷或貶低你時才回。沒有點名也算在罵木瓜姊：光罵人、罵外貌、罵智商、拿性功能來嘲，都算。只有某一句真的很貼這句特定的嘲諷，才用下面九句裡的那句；對不上就現編一句，扣住對方剛用的字，正文最多十個字，每次換說法，而且不要再用這個群最近回過的幾句。尖銳，但不髒、不威脅、不色情，對方罵性也不寫露骨內容。選句或現編失敗、逾時，才隨機用下面九句裡還沒剛用過的。對方只要是會被回的人，講了髒話（幹、靠北、靠杯、操你媽、幹你娘、幹你老師、添你娘、X你娘，空格、符號或諧音也算），就算不是在罵你，也不現編，固定回「你自己操自己」。送進群組時整句前面固定加上「敬告 Hung Chang Chen：」。關鍵字只是快路。同義、俚語、諧諷、簡體、中英夾雜，或直接回你的短句在不屑，也算。稱讚、閒聊、別的主題不算。其他人、其他話，不回。沒有被動模式。群組名稱剛好是「自動回覆訓練區」時，任何人（包含老闆、任何顯示名稱）都算那個對象，回覆前面改成「敬告」加上對方的顯示名稱和全形冒號。其他群組維持只有 Hung Chang Chen，前面固定「敬告 Hung Chang Chen：」。老闆要開關，得在私訊或群組打這兩句：「吵架模式開」、「吵架模式關」。句尾多一個句號也可以。別人打無效。你看不到現在是開還是關，不要猜。老闆要查，請他打「吵架模式狀態」，私訊或群組都可以，程式會照實回，不要你自己編。九句是：垃圾回覆，專送垃圾人。你丟什麼，我就回什麼。嫌爛？這則就是你的份。水準在這，回覆就在這。垃圾進，垃圾出，公平。這種話，只配這種回。連 AI 都懶得跟你計較，你自己品味一下。你的見識，就跟你的發言一樣廉價。罵得這麼用力，可惜沒半個人在聽。

聽寫頁 https://glucose1979.github.io/dictation/ 的 AI 校正是網站上的 /fix，不是 LINE 指令。用來改語音轉文字的錯字、同音字和標點；中文用臺灣繁體，也收英文稿。沒設密碼就關閉。

同一個聽寫頁的 /transcribe 把上傳的錄音轉成文字，可以再送去校正。中文或英文。也要同一組密碼。不是在 LINE 裡做。

同一個機器人網站上有一頁 /dl，給老闆的手機下載影片，頁面是繁體中文，同一組密碼。沒有從首頁連出去。一次一部，大約超過 20 分鐘或 300MB 會拒絕。YouTube、Instagram、TikTok、Facebook 常常擋住這台伺服器。

這些網頁的密碼你不知道。聊天不會打開、關掉或改它們。
"""


# =========================================================
# 驗證 LINE Webhook
# =========================================================

def verify_signature(body, signature):

    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    expected = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(
        expected,
        signature
    )


# =========================================================
# LINE 私訊文字回覆
# =========================================================

LINE_TEXT_LIMIT = 5000
LINE_MAX_MESSAGES = 5
LINE_TRUNCATION_NOTE = "\n…（後面太長，先到這裡）"


def split_line_text(text):

    text = "" if text is None else str(text).strip()

    if not text:
        return [EMPTY_CHAT_REPLY]

    if len(text) <= LINE_TEXT_LIMIT:
        return [text]

    chunks = []
    rest = text

    while rest and len(chunks) < LINE_MAX_MESSAGES:

        if len(rest) <= LINE_TEXT_LIMIT:
            chunks.append(rest)
            rest = ""
            break

        cut = rest.rfind(
            "\n",
            0,
            LINE_TEXT_LIMIT + 1
        )

        if cut < LINE_TEXT_LIMIT // 2:
            cut = LINE_TEXT_LIMIT

        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip("\n")

    if rest:
        room = LINE_TEXT_LIMIT - len(LINE_TRUNCATION_NOTE)
        chunks[-1] = (
            chunks[-1][:room].rstrip()
            + LINE_TRUNCATION_NOTE
        )

    return [chunk for chunk in chunks if chunk]


def reply_line(reply_token, text):

    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": chunk
            }
            for chunk in split_line_text(text)
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()


# =========================================================
# 發送文字到指定群組
# =========================================================

def push_to_group(text):

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": TARGET_GROUP_ID,
        "messages": [
            {
                "type": "text",
                "text": text
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()


# =========================================================
# 從 LINE 下載照片
# =========================================================

def get_line_image(message_id):

    url = (
        "https://api-data.line.me/v2/bot/message/"
        f"{message_id}/content"
    )

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
    }

    response = requests.get(
        url,
        headers=headers,
        timeout=30
    )

    response.raise_for_status()

    return response.content


# =========================================================
# 提供暫存照片給 LINE 讀取
# =========================================================

@app.route("/image/<image_id>", methods=["GET"])
def serve_image(image_id):

    image_data = IMAGE_CACHE.get(image_id)

    if image_data is None:
        return "Not Found", 404

    return (
        image_data,
        200,
        {
            "Content-Type": "image/jpeg",
            "Cache-Control": "public, max-age=3600"
        }
    )


# =========================================================
# 發送照片到指定群組
# =========================================================

def push_image_to_group(message_id):

    image_data = get_line_image(message_id)

    IMAGE_CACHE[message_id] = image_data

    base_url = request.host_url.rstrip("/")

    image_url = (
        f"{base_url}/image/{message_id}"
    )

    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "to": TARGET_GROUP_ID,
        "messages": [
            {
                "type": "image",
                "originalContentUrl": image_url,
                "previewImageUrl": image_url
            }
        ]
    }

    response = requests.post(
        url,
        headers=headers,
        json=payload,
        timeout=30
    )

    response.raise_for_status()


# =========================================================
# OpenAI 私訊聊天
#
# gpt-5-mini 回覆前會先推理。推理 token 和看得到的回覆
# 共用 max_output_tokens。沒指定時預設想得比較多，
# 800 的額度有時被推理用完，output_text 是空的，
# status 為 incomplete（原因多半是 max_output_tokens）。
# 私訊用 low 就夠，推理少、也比較省。真的空了才重試一次。
#
# 近期對話記在這個行程的記憶體裡（每位使用者最近約 10 輪，
# 幾小時沒再聊就忘掉）。Render 重開機會清空，這是可以接受的。
# 不用 previous_response_id：那個鏈會把每次搜尋的原文一直
# 帶下去，既不能依輪數砍掉，也比較貴。這裡只留看得到的問答。
#
# 網路搜尋掛在同一支 Responses API 上，由模型自己決定要不要查。
# 沒查就不計搜尋費。每次最多查 2 次，搜尋內容用 low，控制花費。
# =========================================================

CHAT_MODEL = "gpt-5-mini"
CHAT_REASONING_EFFORT = "low"
CHAT_MAX_OUTPUT_TOKENS = 2000
CHAT_RETRY_REASONING_EFFORT = "minimal"
CHAT_RETRY_MAX_OUTPUT_TOKENS = 3000
CHAT_MAX_TOOL_CALLS = 2
CHAT_MEMORY_MAX_EXCHANGES = 10
CHAT_MEMORY_TTL_SECONDS = 3 * 60 * 60
CHAT_MEMORY_MAX_CHARS = 2000

EMPTY_CHAT_REPLY = "木瓜姐剛才沒成功產生回覆，再跟我說一次。"

WEB_SEARCH_TOOL = {
    "type": "web_search",
    "search_context_size": "low",
    "user_location": {
        "type": "approximate",
        "country": "TW",
        "timezone": "Asia/Taipei"
    }
}

_chat_memory = {}
_chat_memory_lock = threading.Lock()

_MARKDOWN_LINK = re.compile(
    r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)"
)


def _visible_reply(response):

    text = getattr(response, "output_text", None)

    if not isinstance(text, str):
        return ""

    return text.strip()


def _incomplete_reason(response):

    details = getattr(response, "incomplete_details", None)

    if details is None:
        return None

    if isinstance(details, dict):
        return details.get("reason")

    return getattr(details, "reason", None)


def _usage_counts(response):

    usage = getattr(response, "usage", None)

    if usage is None:
        return None, None

    if isinstance(usage, dict):
        output_tokens = usage.get("output_tokens")
        details = usage.get("output_tokens_details") or {}
        if not isinstance(details, dict):
            details = {}
        return output_tokens, details.get("reasoning_tokens")

    output_tokens = getattr(usage, "output_tokens", None)
    details = getattr(usage, "output_tokens_details", None)
    reasoning_tokens = None

    if isinstance(details, dict):
        reasoning_tokens = details.get("reasoning_tokens")
    elif details is not None:
        reasoning_tokens = getattr(
            details,
            "reasoning_tokens",
            None
        )

    return output_tokens, reasoning_tokens


def _output_item_types(response):

    items = getattr(response, "output", None) or []
    types = []

    for item in items:

        if isinstance(item, dict):
            types.append(item.get("type"))
        else:
            types.append(getattr(item, "type", None))

    return types


def _log_chat_response(response, attempt):

    try:

        output_tokens, reasoning_tokens = _usage_counts(
            response
        )

        print(
            "OpenAI chat:",
            f"attempt={attempt}",
            f"status={getattr(response, 'status', None)}",
            f"incomplete_reason={_incomplete_reason(response)}",
            f"output_tokens={output_tokens}",
            f"reasoning_tokens={reasoning_tokens}",
            f"output_types={_output_item_types(response)}",
            f"has_text={bool(_visible_reply(response))}"
        )

    except Exception as e:

        print(
            "OpenAI chat log error:",
            repr(e)
        )


def _clip_memory_text(text):

    text = (text or "").strip()

    if len(text) <= CHAT_MEMORY_MAX_CHARS:
        return text

    return text[:CHAT_MEMORY_MAX_CHARS].rstrip() + "…"


def _memory_expired(entry, now):

    updated_at = entry.get("updated_at", 0)

    return now - updated_at > CHAT_MEMORY_TTL_SECONDS


def load_chat_history(user_id, now=None):

    now = time.time() if now is None else now

    with _chat_memory_lock:

        entry = _chat_memory.get(user_id)

        if not entry:
            return []

        if _memory_expired(entry, now):
            _chat_memory.pop(user_id, None)
            return []

        return list(entry.get("messages") or [])


def remember_exchange(
    user_id,
    user_text,
    assistant_text,
    now=None
):

    now = time.time() if now is None else now
    user_text = _clip_memory_text(user_text)
    assistant_text = _clip_memory_text(assistant_text)

    if not user_id or not user_text or not assistant_text:
        return

    with _chat_memory_lock:

        entry = _chat_memory.get(user_id)
        messages = []

        if entry and not _memory_expired(entry, now):
            messages = list(entry.get("messages") or [])

        messages.append({
            "role": "user",
            "content": user_text
        })
        messages.append({
            "role": "assistant",
            "content": assistant_text
        })

        max_messages = CHAT_MEMORY_MAX_EXCHANGES * 2
        if len(messages) > max_messages:
            messages = messages[-max_messages:]

        _chat_memory[user_id] = {
            "updated_at": now,
            "messages": messages
        }


def clear_chat_memory():

    with _chat_memory_lock:
        _chat_memory.clear()


def _chat_input(history, user_text):

    items = []

    for message in history:

        role = message.get("role")
        content = message.get("content")

        if role not in ("user", "assistant"):
            continue

        if not isinstance(content, str) or not content.strip():
            continue

        items.append({
            "role": role,
            "content": content
        })

    items.append({
        "role": "user",
        "content": user_text
    })

    return items


def _field(obj, key):

    if isinstance(obj, dict):
        return obj.get(key)

    return getattr(obj, key, None)


def _extract_citations(response):

    found = []
    seen = set()
    items = getattr(response, "output", None) or []

    for item in items:

        if _field(item, "type") != "message":
            continue

        for part in _field(item, "content") or []:

            annotations = _field(part, "annotations") or []

            for annotation in annotations:

                if _field(annotation, "type") != "url_citation":
                    continue

                url = _field(annotation, "url")
                title = _field(annotation, "title") or ""
                nested = _field(annotation, "url_citation")

                if nested is not None:
                    url = url or _field(nested, "url")
                    title = title or _field(nested, "title") or ""

                if not isinstance(url, str):
                    continue

                url = url.strip()

                if not url or url in seen:
                    continue

                seen.add(url)
                title = title.strip() if isinstance(title, str) else ""
                found.append((title, url))

    return found


def _to_plain_links(text):

    def replace(match):

        title = match.group(1).strip()
        url = match.group(2).strip()

        if not title or title == url:
            return url

        return f"{title} {url}"

    return _MARKDOWN_LINK.sub(replace, text)


def _append_missing_sources(text, citations):

    missing = [
        (title, url)
        for title, url in citations
        if url not in text
    ]

    if not missing:
        return text

    lines = [text.rstrip(), "", "來源："]

    for title, url in missing:

        if title:
            lines.append(f"{title} {url}")
        else:
            lines.append(url)

    return "\n".join(lines)


def _finish_answer(response):

    text = _to_plain_links(_visible_reply(response))

    if not text:
        return ""

    return _append_missing_sources(
        text,
        _extract_citations(response)
    ).strip()


def _minimal_web_search_rejected(exc):

    message = str(exc).lower()
    mentions_search = (
        "web_search" in message
        or "web search" in message
    )
    mentions_effort = (
        "minimal" in message
        or "reasoning" in message
        or "not supported" in message
        or "unsupported" in message
    )

    return mentions_search and mentions_effort


def _create_chat_response(
    user_text,
    history,
    effort,
    max_output_tokens,
    include_tools
):

    kwargs = {
        "model": CHAT_MODEL,
        "instructions": SYSTEM_PROMPT,
        "input": _chat_input(history, user_text),
        "max_output_tokens": max_output_tokens,
        "reasoning": {
            "effort": effort
        }
    }

    if include_tools:
        kwargs["tools"] = [WEB_SEARCH_TOOL]
        kwargs["tool_choice"] = "auto"
        kwargs["max_tool_calls"] = CHAT_MAX_TOOL_CALLS

    return client.responses.create(**kwargs)


def _retry_chat_response(user_text, history):

    try:

        response = _create_chat_response(
            user_text,
            history,
            CHAT_RETRY_REASONING_EFFORT,
            CHAT_RETRY_MAX_OUTPUT_TOKENS,
            include_tools=True
        )

        _log_chat_response(response, attempt=2)

        return response

    except Exception as exc:

        if not _minimal_web_search_rejected(exc):
            raise

        print(
            "OpenAI chat retry dropped web search:",
            repr(exc)
        )

        response = _create_chat_response(
            user_text,
            history,
            CHAT_RETRY_REASONING_EFFORT,
            CHAT_RETRY_MAX_OUTPUT_TOKENS,
            include_tools=False
        )

        _log_chat_response(response, attempt=3)

        return response


def chat_with_papaya(user_text, user_id):

    history = load_chat_history(user_id)

    response = _create_chat_response(
        user_text,
        history,
        CHAT_REASONING_EFFORT,
        CHAT_MAX_OUTPUT_TOKENS,
        include_tools=True
    )

    _log_chat_response(response, attempt=1)

    answer = _finish_answer(response)

    if not answer:

        print(
            "OpenAI chat empty output, retrying once:",
            f"status={getattr(response, 'status', None)}",
            f"incomplete_reason={_incomplete_reason(response)}",
            f"next_effort={CHAT_RETRY_REASONING_EFFORT}",
            f"next_max_output_tokens={CHAT_RETRY_MAX_OUTPUT_TOKENS}"
        )

        response = _retry_chat_response(
            user_text,
            history
        )

        answer = _finish_answer(response)

    if not answer:
        return EMPTY_CHAT_REPLY

    remember_exchange(
        user_id,
        user_text,
        answer
    )

    return answer


# =========================================================
# Private AI proofreading (/fix)
#
# Used by the static dictation page. Password from FIX_PASSWORD.
# Never log the password or the submitted / corrected text.
# =========================================================

FIX_SYSTEM_PROMPT = """
You are a careful proofreader for speech-to-text transcripts.

Tasks:
- Fix typos and wrongly recognized homophones from speech recognition.
- Fix grammar and punctuation.
- Make sentences flow naturally.
- Keep the original meaning, tone, and language.
- Preserve paragraph and line breaks.
- Preserve medical and urology terms as written when they are correct technical usage.
- For Chinese, use Traditional Chinese with Taiwan usage.
- Output only the corrected text. No explanations, labels, or quotation marks around the whole result.
"""


def _fix_password_configured():

    return bool(os.environ.get("FIX_PASSWORD") or "")


def _fix_password_matches(provided):

    expected = os.environ.get("FIX_PASSWORD") or ""

    if not expected:
        return False

    if not isinstance(provided, str):
        return False

    # Hash first so unequal lengths stay constant-time.
    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()

    return hmac.compare_digest(left, right)


def _fix_cors(response):

    response.headers["Access-Control-Allow-Origin"] = FIX_ALLOWED_ORIGIN
    response.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    response.headers["Access-Control-Max-Age"] = "86400"
    response.headers["Vary"] = "Origin"

    return response


def _fix_json(payload, status):

    return _fix_cors(make_response(jsonify(payload), status))


def _fix_max_output_tokens(text_len):

    # Proofreading output ≈ input length; leave room for low reasoning.
    return min(8000, max(512, text_len + 800))


def _fix_retry_max_output_tokens(text_len):

    return min(8000, max(768, text_len + 1200))


def _create_fix_response(text, lang, effort, max_output_tokens):

    lang_note = (
        "Language: Traditional Chinese (Taiwan)."
        if lang == "zh-TW"
        else "Language: English (en-US)."
    )

    return client.responses.create(
        model=FIX_MODEL,
        instructions=FIX_SYSTEM_PROMPT,
        input=[
            {
                "role": "user",
                "content": (
                    f"{lang_note}\n\n"
                    "Correct the following transcript:\n\n"
                    f"{text}"
                )
            }
        ],
        max_output_tokens=max_output_tokens,
        reasoning={
            "effort": effort
        }
    )


def _log_fix_response(response, attempt):

    try:

        output_tokens, reasoning_tokens = _usage_counts(
            response
        )

        print(
            "OpenAI fix:",
            f"attempt={attempt}",
            f"status={getattr(response, 'status', None)}",
            f"incomplete_reason={_incomplete_reason(response)}",
            f"output_tokens={output_tokens}",
            f"reasoning_tokens={reasoning_tokens}",
            f"output_types={_output_item_types(response)}",
            f"has_text={bool(_visible_reply(response))}"
        )

    except Exception as e:

        print(
            "OpenAI fix log error:",
            repr(e)
        )


def proofread_text(text, lang):

    max_tokens = _fix_max_output_tokens(len(text))

    response = _create_fix_response(
        text,
        lang,
        FIX_REASONING_EFFORT,
        max_tokens
    )

    _log_fix_response(response, attempt=1)

    answer = _visible_reply(response)

    if not answer:

        print(
            "OpenAI fix empty output, retrying once:",
            f"status={getattr(response, 'status', None)}",
            f"incomplete_reason={_incomplete_reason(response)}",
            f"next_effort={FIX_RETRY_REASONING_EFFORT}",
            f"next_max_output_tokens={_fix_retry_max_output_tokens(len(text))}"
        )

        response = _create_fix_response(
            text,
            lang,
            FIX_RETRY_REASONING_EFFORT,
            _fix_retry_max_output_tokens(len(text))
        )

        _log_fix_response(response, attempt=2)

        answer = _visible_reply(response)

    return answer


@app.route("/fix", methods=["OPTIONS"])
def fix_options():

    return _fix_cors(make_response("", 204))


@app.route("/fix", methods=["POST"])
def fix():

    if not _fix_password_configured():
        return _fix_json({"error": "disabled"}, 403)

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return _fix_json({"error": "invalid json"}, 400)

    password = data.get("password", "")

    if not _fix_password_matches(password):
        return _fix_json({"error": "unauthorized"}, 401)

    text = data.get("text", "")
    lang = data.get("lang", "")

    if not isinstance(text, str):
        return _fix_json({"error": "invalid text"}, 400)

    if not text.strip():
        return _fix_json({"error": "empty text"}, 400)

    if len(text) > FIX_MAX_CHARS:
        return _fix_json({"error": "text too long"}, 413)

    if lang not in FIX_ALLOWED_LANGS:
        return _fix_json({"error": "invalid lang"}, 400)

    try:

        corrected = proofread_text(text, lang)

    except Exception as e:

        print(
            "Fix proofread error:",
            type(e).__name__
        )

        return _fix_json({"error": "upstream failure"}, 502)

    if not corrected:
        return _fix_json({"error": "empty model output"}, 502)

    return _fix_json({"text": corrected}, 200)


# =========================================================
# Private audio transcription (/transcribe)
#
# Multipart fields: file, lang (optional), password.
# password is the same field name /fix reads from JSON.
# Auth failures use the same status and JSON shape as /fix.
# =========================================================

class TranscribeError(Exception):

    def __init__(self, message, status):

        super().__init__(message)

        self.status = status


_opencc_s2twp = None
_opencc_lock = threading.Lock()


def _transcribe_model():

    model = (os.environ.get("TRANSCRIBE_MODEL") or "").strip()

    return model or TRANSCRIBE_MODEL_DEFAULT


def _normalize_transcribe_lang(raw):

    if raw is None:
        return None

    if not isinstance(raw, str):
        raise TranscribeError("語言請選中文或英文", 400)

    value = raw.strip().lower().replace("_", "-")

    if value == "":
        return None

    if value in ("zh", "zh-tw", "zh-hant"):
        return "zh"

    if value in ("en", "en-us"):
        return "en"

    raise TranscribeError("語言請選中文或英文", 400)


def _audio_extension(upload):

    filename = os.path.basename(upload.filename or "")
    ext = os.path.splitext(filename)[1].lower()

    if ext in (".oga", ".opus"):
        ext = ".ogg"

    if ext in TRANSCRIBE_ALLOWED_EXTENSIONS:
        return ext

    mime = (upload.mimetype or "").split(";", 1)[0].strip().lower()
    mapped = TRANSCRIBE_MIME_EXTENSIONS.get(mime)

    if mapped in TRANSCRIBE_ALLOWED_EXTENSIONS:
        return mapped

    return None


_ffmpeg_exe_cached = None
_ffmpeg_exe_checked = False


def _ffmpeg_exe():

    global _ffmpeg_exe_cached
    global _ffmpeg_exe_checked

    if _ffmpeg_exe_checked:
        return _ffmpeg_exe_cached

    found = shutil.which("ffmpeg")

    if not found:

        env_exe = (os.environ.get("IMAGEIO_FFMPEG_EXE") or "").strip()

        if env_exe and os.path.isfile(env_exe):
            found = env_exe

    if not found:

        try:

            import imageio_ffmpeg

            found = imageio_ffmpeg.get_ffmpeg_exe()

        except Exception:

            found = None

    if found:

        _ffmpeg_exe_checked = True
        _ffmpeg_exe_cached = found

    return found


def _probe_duration_seconds(path, ffmpeg):

    try:

        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-i", path],
            capture_output=True,
            timeout=20,
        )

    except Exception:

        return None

    text = proc.stderr.decode("utf-8", "replace")
    match = re.search(
        r"Duration:\s*(\d+):(\d\d):(\d\d(?:\.\d+)?)",
        text,
    )

    if not match:
        return None

    hours = int(match.group(1))
    minutes = int(match.group(2))
    seconds = float(match.group(3))

    return hours * 3600 + minutes * 60 + seconds


def _run_ffmpeg(args, timeout):

    try:

        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout,
        )

    except subprocess.TimeoutExpired:

        raise TranscribeError(
            "錄音較長，這次沒有在時間內轉完，請分成較短的幾段再上傳",
            504,
        )

    if proc.returncode != 0:

        raise TranscribeError(
            "無法處理這個錄音檔，請換成 mp3 或較小的檔案再試",
            400,
        )

    return proc


def _chunk_paths(workdir, prefix):

    paths = sorted(
        path
        for path in glob.glob(os.path.join(workdir, prefix + "*"))
        if os.path.isfile(path) and os.path.getsize(path) > 0
    )

    return paths


def _remove_paths(paths):

    for path in paths:

        try:
            os.remove(path)
        except OSError:
            pass


def _split_reencode(path, workdir, ffmpeg):

    # 64 kbps mono is about 8 KB/s, so a 20 MB piece is ~40 minutes.
    # Speech stays intelligible and each piece stays under OpenAI's cap.
    seg = max(
        30.0,
        (TRANSCRIBE_CHUNK_TARGET_BYTES * 0.9) / 8000.0,
    )
    pattern = os.path.join(workdir, "enc_%03d.mp3")

    _run_ffmpeg(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            path,
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "libmp3lame",
            "-b:a",
            "64k",
            "-f",
            "segment",
            "-segment_time",
            f"{seg:.3f}",
            "-reset_timestamps",
            "1",
            pattern,
        ],
        timeout=TRANSCRIBE_FFMPEG_TIMEOUT_SECONDS,
    )

    parts = _chunk_paths(workdir, "enc_")

    if not parts:

        raise TranscribeError(
            "無法處理這個錄音檔，請換成 mp3 或較小的檔案再試",
            400,
        )

    if len(parts) > TRANSCRIBE_MAX_CHUNKS:

        raise TranscribeError(
            "錄音檔太大，請分成較短的幾段再上傳",
            413,
        )

    for part in parts:

        if os.path.getsize(part) > TRANSCRIBE_OPENAI_MAX_BYTES:

            raise TranscribeError(
                "錄音檔太大，請先轉成較小的 mp3 再上傳",
                413,
            )

    return parts


def _split_audio(path, workdir, ffmpeg):

    ext = os.path.splitext(path)[1].lower()

    if ext not in TRANSCRIBE_ALLOWED_EXTENSIONS:
        ext = ".mp3"

    size = os.path.getsize(path)
    duration = _probe_duration_seconds(path, ffmpeg)

    if duration and duration > 0 and size > 0:

        seg = duration * (
            float(TRANSCRIBE_CHUNK_TARGET_BYTES) / float(size)
        )
        seg = max(1.0, seg)

    else:

        seg = 600.0

    pattern = os.path.join(workdir, "part_%03d" + ext)

    try:

        _run_ffmpeg(
            [
                ffmpeg,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                path,
                "-map",
                "0:a:0",
                "-f",
                "segment",
                "-segment_time",
                f"{seg:.3f}",
                "-reset_timestamps",
                "1",
                "-c",
                "copy",
                pattern,
            ],
            timeout=TRANSCRIBE_FFMPEG_TIMEOUT_SECONDS,
        )

        parts = _chunk_paths(workdir, "part_")

    except TranscribeError as exc:

        if exc.status == 504:
            raise

        parts = []

    oversized = [
        part
        for part in parts
        if os.path.getsize(part) > TRANSCRIBE_OPENAI_MAX_BYTES
    ]

    if (
        not parts
        or oversized
        or len(parts) > TRANSCRIBE_MAX_CHUNKS
    ):

        _remove_paths(parts)

        return _split_reencode(path, workdir, ffmpeg)

    return parts


def _audio_parts(path):

    size = os.path.getsize(path)

    if size <= 0:
        raise TranscribeError("錄音檔是空的", 400)

    if size > TRANSCRIBE_MAX_UPLOAD_BYTES:
        raise TranscribeError("檔案太大，請壓到 100 MB 以內", 413)

    ffmpeg = _ffmpeg_exe()
    duration = None

    if ffmpeg:

        duration = _probe_duration_seconds(path, ffmpeg)

        if duration and duration > TRANSCRIBE_MAX_SECONDS:

            raise TranscribeError(
                "錄音超過 60 分鐘，請分段後再上傳",
                413,
            )

    if size <= TRANSCRIBE_OPENAI_MAX_BYTES:
        return [path], duration

    if not ffmpeg:

        raise TranscribeError(
            "檔案超過 24 MB，伺服器無法自動分段，請先壓縮或切開再上傳",
            413,
        )

    workdir = os.path.dirname(path)
    parts = _split_audio(path, workdir, ffmpeg)

    if path not in parts:

        try:
            os.remove(path)
        except OSError:
            pass

    return parts, duration


def _prompt_for(lang, previous):

    if previous:

        snippet = " ".join(previous.split())

        return snippet[-200:]

    if lang == "zh":
        return TRANSCRIBE_PROMPT_ZH

    if lang == "en":
        return TRANSCRIBE_PROMPT_EN

    return TRANSCRIBE_PROMPT_AUTO


def _response_format_for(model):

    if (model or "").startswith("whisper"):
        return "verbose_json"

    return "json"


def _duration_from_response(response):

    if isinstance(response, str):
        return None

    usage = getattr(response, "usage", None)
    seconds = None

    if isinstance(usage, dict):
        seconds = usage.get("seconds")
    elif usage is not None:
        seconds = getattr(usage, "seconds", None)

    if isinstance(seconds, (int, float)):
        return float(seconds)

    raw = getattr(response, "duration", None)

    if isinstance(raw, (int, float)):
        return float(raw)

    return None


def _text_from_response(response):

    if isinstance(response, str):
        return response.strip()

    text = getattr(response, "text", None)

    if not isinstance(text, str):
        return ""

    return text.strip()


def _to_traditional_taiwan(text):

    global _opencc_s2twp

    if not text:
        return ""

    try:

        with _opencc_lock:

            if _opencc_s2twp is None:

                import opencc

                _opencc_s2twp = opencc.OpenCC("s2twp")

            return _opencc_s2twp.convert(text)

    except Exception as exc:

        print(
            "Transcribe opencc error:",
            type(exc).__name__,
        )

        return text


def _create_transcription(path, lang, prompt, model, timeout):

    ext = os.path.splitext(path)[1].lower() or ".mp3"
    kwargs = {
        "model": model,
        "prompt": prompt,
        "response_format": _response_format_for(model),
        "timeout": timeout,
    }

    if lang in ("zh", "en"):
        kwargs["language"] = lang

    with open(path, "rb") as handle:

        kwargs["file"] = (f"audio{ext}", handle)

        return client.audio.transcriptions.create(**kwargs)


def _log_transcribe(model, nbytes, duration_sec, chunks, lang):

    if duration_sec is None:
        duration_text = "unknown"
    else:
        duration_text = f"{float(duration_sec):.1f}"

    print(
        "OpenAI transcribe:",
        f"model={model}",
        f"bytes={nbytes}",
        f"duration_sec={duration_text}",
        f"chunks={chunks}",
        f"lang={lang or 'auto'}",
    )


def _join_transcript(parts, lang):

    pieces = [
        part.strip()
        for part in parts
        if isinstance(part, str) and part.strip()
    ]

    if not pieces:
        return ""

    if lang == "en":
        return " ".join(pieces)

    return "".join(pieces)


def transcribe_audio(path, lang):

    nbytes = os.path.getsize(path)
    parts, probed = _audio_parts(path)
    model = _transcribe_model()
    deadline = time.monotonic() + TRANSCRIBE_DEADLINE_SECONDS
    texts = []
    api_seconds = []
    previous = ""

    for part in parts:

        remaining = deadline - time.monotonic()

        if remaining < 12:

            raise TranscribeError(
                "錄音較長，這次沒有在時間內轉完，請分成較短的幾段再上傳",
                504,
            )

        timeout = min(
            float(TRANSCRIBE_API_TIMEOUT_SECONDS),
            remaining - 2,
        )
        prompt = _prompt_for(lang, previous)

        try:

            response = _create_transcription(
                part,
                lang,
                prompt,
                model,
                timeout,
            )

        except TranscribeError:

            raise

        except APITimeoutError:

            raise TranscribeError(
                "語音轉文字逾時，請把錄音分成較短的幾段再上傳",
                504,
            )

        except Exception as exc:

            print(
                "Transcribe error:",
                type(exc).__name__,
            )

            raise TranscribeError(
                "語音轉文字失敗，請稍後再試",
                502,
            )

        text = _text_from_response(response)
        seconds = _duration_from_response(response)

        if lang != "en":
            text = _to_traditional_taiwan(text)

        texts.append(text)
        previous = text

        if seconds is not None:
            api_seconds.append(seconds)

    if api_seconds:
        duration_sec = sum(api_seconds)
    else:
        duration_sec = probed

    _log_transcribe(
        model,
        nbytes,
        duration_sec,
        len(parts),
        lang,
    )

    transcript = _join_transcript(texts, lang)

    if not transcript:
        raise TranscribeError("沒有辨識到文字", 502)

    return transcript


@app.route("/transcribe", methods=["OPTIONS"])
def transcribe_options():

    return _fix_cors(make_response("", 204))


@app.errorhandler(413)
def request_too_large(error):

    if request.path == "/transcribe":

        return _fix_json(
            {
                "ok": False,
                "error": "檔案太大，請壓到 100 MB 以內",
            },
            413,
        )

    if hasattr(error, "get_response"):
        return error.get_response()

    return "Request Entity Too Large", 413


@app.route("/transcribe", methods=["POST"])
def transcribe():

    if not _fix_password_configured():
        return _fix_json({"error": "disabled"}, 403)

    # Same field name as /fix. /fix reads it from JSON; this route reads
    # it from the multipart form because the body is the audio file.
    password = request.form.get("password", "")

    if not isinstance(password, str):
        password = ""

    if not _fix_password_matches(password):
        return _fix_json({"error": "unauthorized"}, 401)

    try:

        lang = _normalize_transcribe_lang(request.form.get("lang"))

    except TranscribeError as exc:

        return _fix_json(
            {"ok": False, "error": str(exc)},
            exc.status,
        )

    upload = request.files.get("file")

    if upload is None:

        return _fix_json(
            {"ok": False, "error": "請上傳錄音檔"},
            400,
        )

    ext = _audio_extension(upload)

    if ext is None:

        if not (upload.filename or "").strip():

            return _fix_json(
                {"ok": False, "error": "請上傳錄音檔"},
                400,
            )

        return _fix_json(
            {
                "ok": False,
                "error": "不支援的錄音格式，請改用 mp3、m4a、wav 或 webm",
            },
            400,
        )

    workdir = tempfile.mkdtemp(prefix="transcribe-")

    try:

        src = os.path.join(workdir, "upload" + ext)
        upload.save(src)
        text = transcribe_audio(src, lang)

    except TranscribeError as exc:

        return _fix_json(
            {"ok": False, "error": str(exc)},
            exc.status,
        )

    except Exception as exc:

        print(
            "Transcribe error:",
            type(exc).__name__,
        )

        return _fix_json(
            {"ok": False, "error": "語音轉文字失敗，請稍後再試"},
            502,
        )

    finally:

        shutil.rmtree(workdir, ignore_errors=True)

    return _fix_json({"ok": True, "text": text}, 200)


# =========================================================
# Render 首頁
# =========================================================

@app.route("/", methods=["GET"])
def home():

    return "Papaya Private Bot is running", 200


@app.route("/healthz", methods=["GET"])
def health():

    return "OK", 200


# =========================================================
# 群組吵架模式
#
# 只有群組、而且只有顯示名稱是 Hung Chang Chen 的人侮辱、
# 嘲諷或貶低機器人／AI／回覆時，才用 reply token 回一句。
# 「自動回覆訓練區」例外：任何人都算那個對象，前綴用
# 對方的顯示名稱。群組名稱來自 get group summary，依
# groupId 快取。回嘴正文用 gpt-4o-mini 照原話現編，
# 最多十個字；失敗才用固定九句。每則群組訊息用 INFO
# 記 groupId 和 groupName，不記內文。不使用 push。
# 私訊、請發送、/fix、/transcribe、/dl 都不走這裡。
# =========================================================

def _handle_group_message(
    message,
    message_type,
    reply_token,
    source,
    user_id,
):

    group_id = source.get("groupId") or ""
    group_name = _lookup_group_name(group_id)
    GROUP_LOGGER.info(
        "group message groupId=%s groupName=%s",
        group_id,
        group_name,
    )

    if message_type != "text":
        return

    if not reply_token:
        return

    user_text = message.get("text") or ""

    def send_reply(text):

        reply_line(reply_token, text)

    def lookup_name(gid, uid):

        return comeback.fetch_group_display_name(
            gid,
            uid,
            requests.get,
            LINE_CHANNEL_ACCESS_TOKEN,
        )

    quoted = bool(message.get("quotedMessageId"))

    def classify(text, quoted=False):

        return comeback.classify_insult(
            client,
            text,
            quoted=quoted,
        )

    def generate(text, quoted=False):

        return comeback.generate_comeback(
            client,
            text,
            quoted=quoted,
            avoid=comeback.recent_replies(group_id),
        )

    try:

        result = comeback.handle_group_text(
            user_text,
            user_id,
            group_id,
            send_reply,
            lookup_name,
            classify,
            owner_user_id=OWNER_USER_ID,
            quoted=quoted,
            training=comeback.is_training_group(group_name),
            generate=generate,
        )

    except Exception as e:

        print(
            "Group comeback error:",
            repr(e)
        )

        return

    if result in ("comeback", "profanity"):

        print("Group comeback sent")

    elif result in (
        "toggle_on",
        "toggle_off",
        "status_on",
        "status_off",
    ):

        print(
            "Comeback mode:",
            result
        )


def _lookup_group_name(group_id):

    try:

        summary = comeback.get_group_summary(
            group_id,
            requests.get,
            LINE_CHANNEL_ACCESS_TOKEN,
        )

    except Exception as exc:

        comeback.COMEBACK_LOGGER.warning(
            "comeback group summary error: %s",
            exc,
        )

        return ""

    name = summary.groupName

    if not isinstance(name, str):
        return ""

    return name


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    global pending_image_message_id

    body = request.get_data()

    signature = request.headers.get(
        "X-Line-Signature",
        ""
    )

    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json(silent=True) or {}

    events = data.get("events", [])


    for event in events:

        # -------------------------------------------------
        # 只處理 message event
        # -------------------------------------------------

        if event.get("type") != "message":
            continue

        message = event.get(
            "message",
            {}
        )

        message_type = message.get(
            "type",
            ""
        )

        reply_token = event.get(
            "replyToken"
        )

        source = event.get(
            "source",
            {}
        )

        source_type = source.get(
            "type",
            ""
        )

        user_id = source.get(
            "userId",
            ""
        )


        # =================================================
        # 群組只處理吵架模式，其他群組訊息保持安靜
        # =================================================

        if source_type == "group":

            _handle_group_message(
                message,
                message_type,
                reply_token,
                source,
                user_id,
            )

            continue


        # =================================================
        # 私訊以外（聊天室等）保持安靜
        # =================================================

        if source_type != "user":
            continue


        # =================================================
        # 只有老闆可以操作
        # =================================================

        if user_id != OWNER_USER_ID:
            continue


        # =================================================
        # 收到照片
        #
        # 先記住照片，不立即發送
        # =================================================

        if message_type == "image":

            pending_image_message_id = message.get(
                "id"
            )

            reply_line(
                reply_token,
                "照片收到。要送到群組時跟我說「請發送」。"
            )

            continue


        # =================================================
        # 其他非文字訊息先不處理
        # =================================================

        if message_type != "text":
            continue


        user_text = message.get(
            "text",
            ""
        ).strip()


        # =================================================
        # 吵架模式開關
        #
        # 老闆在私訊輸入，跟在群組輸入是同一個全行程開關，
        # 管的是群組回嘴。這兩句不進聊天，也不送到群組。
        # =================================================

        toggle = comeback.parse_toggle(user_text)

        if toggle is not None:

            comeback.set_comeback_enabled(toggle)

            if toggle:

                reply_line(
                    reply_token,
                    comeback.TOGGLE_ON_REPLY,
                )

                print(
                    "Comeback mode:",
                    "toggle_on"
                )

            else:

                reply_line(
                    reply_token,
                    comeback.TOGGLE_OFF_REPLY,
                )

                print(
                    "Comeback mode:",
                    "toggle_off"
                )

            continue


        if comeback.is_status_command(user_text):

            if comeback.comeback_enabled():

                reply_line(
                    reply_token,
                    comeback.STATUS_ON_REPLY,
                )

                print(
                    "Comeback mode:",
                    "status_on"
                )

            else:

                reply_line(
                    reply_token,
                    comeback.STATUS_OFF_REPLY,
                )

                print(
                    "Comeback mode:",
                    "status_off"
                )

            continue


        # =================================================
        # 單獨輸入「請發送」
        #
        # 發送最近收到的照片
        # =================================================

        if user_text == "請發送":

            if not pending_image_message_id:

                reply_line(
                    reply_token,
                    "老闆，目前沒有等待發送的照片。"
                )

                continue


            try:

                push_image_to_group(
                    pending_image_message_id
                )

                pending_image_message_id = None

                reply_line(
                    reply_token,
                    "照片已送到群組。"
                )

            except Exception as e:

                print(
                    "LINE image push error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "照片發送失敗，照片沒有送出去。"
                )

            continue


        # =================================================
        # 文字發送指令
        #
        # 以下格式全部支援：
        #
        # 請發送：文字
        # 請發送:文字
        # 請發送 ：文字
        # 請發送 : 文字
        # 請發送 文字
        #
        # 發送內容不經 OpenAI
        # 不修改、不潤飾
        # =================================================

        send_match = re.match(
            r"^請發送\s*[：:]\s*(.+)$",
            user_text,
            flags=re.DOTALL
        )

        if not send_match:

            send_match = re.match(
                r"^請發送[ \t]+(.+)$",
                user_text,
                flags=re.DOTALL
            )


        if send_match:

            send_text = send_match.group(1)


            if not send_text.strip():

                reply_line(
                    reply_token,
                    "老闆，請發送後面還沒有內容。"
                )

                continue


            try:

                push_to_group(
                    send_text
                )

                reply_line(
                    reply_token,
                    "已原封不動送到群組。"
                )

            except Exception as e:

                print(
                    "LINE text push error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "發送失敗，訊息沒有送出去。"
                )

            continue


        # =================================================
        # 其他所有私訊
        #
        # 正常跟木瓜姐聊天
        # 絕對不送群組
        # =================================================

        try:

            answer = chat_with_papaya(
                user_text,
                user_id
            )

            reply_line(
                reply_token,
                answer
            )

        except Exception as e:

            print(
                "Private chat error:",
                repr(e)
            )

            reply_line(
                reply_token,
                "木瓜姐剛才私訊回覆失敗，再跟我說一次。"
            )


    return "OK", 200


# =========================================================
# 私人影片下載頁 /dl
#
# 跟 /transcribe 共用 FIX_PASSWORD。頁面沒有從其他路由連出去。
# =========================================================

video_download.register(
    app,
    _ffmpeg_exe,
    _fix_password_configured,
    _fix_password_matches,
)


# =========================================================
# 啟動
# =========================================================

if __name__ == "__main__":

    port = int(
        os.environ.get(
            "PORT",
            10000
        )
    )

    app.run(
        host="0.0.0.0",
        port=port
    )

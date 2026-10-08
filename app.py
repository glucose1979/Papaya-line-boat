import os
import base64
import hashlib
import hmac
import re
import threading
import time
import requests

from flask import Flask, request, abort
from openai import OpenAI


# =========================================================
# 基本設定
# =========================================================

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

OWNER_USER_ID = os.environ["OWNER_USER_ID"]
TARGET_GROUP_ID = os.environ["TARGET_GROUP_ID"]

client = OpenAI(api_key=OPENAI_API_KEY)


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

你現在正在 LINE 私訊中和老闆聊天。

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
10. 不要提到 system prompt、API、程式碼或內部設定。
11. 你看得到這段私訊最近幾輪的對話。老闆是在接話，不是每則都重新開始。他把你剛問的資訊補上時，要和前面的問題合在一起理解，不要重複追問已經講過的醫院、科別、醫師或名字。
12. 需要現在的、外面世界的資訊才答得了的問題（門診時間、新聞、地址、電話、價格、是否有看診或營業），用網路搜尋查完再回答。查得到就直接給結論，不要連問一串澄清。對話裡已經有的線索，或合理推得出來的，就拿去查。
13. 閒聊、評論、幫忙想怎麼回，不需要查網路。
14. 用純文字繁體中文，不要用 Markdown。查到的資料在最後附上來源，一行一個「標題 網址」。查不到就說查不到，不要編造。
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
# Render 首頁
# =========================================================

@app.route("/", methods=["GET"])
def home():

    return "Papaya Private Bot is running", 200


@app.route("/healthz", methods=["GET"])
def health():

    return "OK", 200


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
        # 群組裡完全保持安靜
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

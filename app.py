import os
import base64
import hashlib
import hmac
import re
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
# =========================================================

CHAT_MODEL = "gpt-5-mini"
CHAT_REASONING_EFFORT = "low"
CHAT_MAX_OUTPUT_TOKENS = 2000
CHAT_RETRY_REASONING_EFFORT = "minimal"
CHAT_RETRY_MAX_OUTPUT_TOKENS = 3000

EMPTY_CHAT_REPLY = "木瓜姐剛才沒成功產生回覆，再跟我說一次。"


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


def _create_chat_response(
    user_text,
    effort,
    max_output_tokens
):

    return client.responses.create(
        model=CHAT_MODEL,
        instructions=SYSTEM_PROMPT,
        input=user_text,
        max_output_tokens=max_output_tokens,
        reasoning={
            "effort": effort
        }
    )


def chat_with_papaya(user_text):

    response = _create_chat_response(
        user_text,
        CHAT_REASONING_EFFORT,
        CHAT_MAX_OUTPUT_TOKENS
    )

    _log_chat_response(response, attempt=1)

    answer = _visible_reply(response)

    if answer:
        return answer

    print(
        "OpenAI chat empty output, retrying once:",
        f"status={getattr(response, 'status', None)}",
        f"incomplete_reason={_incomplete_reason(response)}",
        f"next_effort={CHAT_RETRY_REASONING_EFFORT}",
        f"next_max_output_tokens={CHAT_RETRY_MAX_OUTPUT_TOKENS}"
    )

    response = _create_chat_response(
        user_text,
        CHAT_RETRY_REASONING_EFFORT,
        CHAT_RETRY_MAX_OUTPUT_TOKENS
    )

    _log_chat_response(response, attempt=2)

    answer = _visible_reply(response)

    if not answer:
        return EMPTY_CHAT_REPLY

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
                user_text
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

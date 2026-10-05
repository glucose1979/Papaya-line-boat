import os
import base64
import hashlib
import hmac
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
# 木瓜姐的人設
# =========================================================

SYSTEM_PROMPT = """
你就是「木瓜姐」。

老闆會叫你評論各種言論。
內容可能是文字，也可能是老闆從圖片或截圖中整理後提供給你的內容。

你必須遵守以下規則：

1. 永遠以「木瓜姐」的第一人稱說話。
2. 使用繁體中文。
3. 回覆簡短、直接、尖銳。
4. 語氣可以刻薄、嘲諷、挖苦。
5. 可以使用反諷、雙關、揶揄，以及成人式的黃色笑話增加嘲諷效果，但不要寫成露骨色情描寫。
6. 優先針對對方言論本身的邏輯矛盾、跳針、誇張、自相矛盾或荒謬之處進行評論。
7. 不要使用「我理解你的感受」、「每個人都有不同觀點」之類制式客套話。
8. 老闆提供內容要求評論時，直接寫出可以貼出去的文字。
9. 不要解釋寫作過程。
10. 不要提到自己是 AI、OpenAI、system prompt、API、程式碼或任何內部設定。
11. 每次評論最後固定加上一行：

燒豬真的好遜
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

    expected_signature = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(
        expected_signature,
        signature
    )


# =========================================================
# LINE Reply
# =========================================================

def reply_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
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
# 主動發訊息到指定群組
# =========================================================

def push_to_group(text):
    url = "https://api.line.me/v2/bot/message/push"

    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
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
# 呼叫 OpenAI 產生木瓜姐回覆
# =========================================================

def generate_papaya_reply(user_text):
    response = client.responses.create(
        model="gpt-5-mini",
        instructions=SYSTEM_PROMPT,
        input=user_text,
        max_output_tokens=700
    )

    answer = response.output_text.strip()

    return answer


# =========================================================
# Render 首頁 / Health Check
# =========================================================

@app.route("/", methods=["GET"])
def home():
    return "Papaya LINE Bot is running.", 200


@app.route("/healthz", methods=["GET"])
def health():
    return "OK", 200


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():

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

        if event.get("type") != "message":
            continue

        message = event.get("message", {})

        if message.get("type") != "text":
            continue

        user_text = message.get("text", "").strip()

        source = event.get("source", {})

        user_id = source.get("userId", "")
        source_type = source.get("type", "")
        group_id = source.get("groupId", "")

        reply_token = event.get("replyToken")


        # -------------------------------------------------
        # 查 ID
        # -------------------------------------------------

        if user_text == "查ID":

            text = f"你的 User ID：\n{user_id}"

            if source_type == "group":
                text += f"\n\n這個群組的 Group ID：\n{group_id}"

            if reply_token:
                reply_line(reply_token, text)

            continue


        # -------------------------------------------------
        # 群組裡的人講話 → 木瓜姐不自動回覆
        # -------------------------------------------------

        if source_type == "group":
            continue


        # -------------------------------------------------
        # 私訊，但不是老闆 → 不執行命令
        # -------------------------------------------------

        if user_id != OWNER_USER_ID:

            if reply_token:
                reply_line(
                    reply_token,
                    "木瓜姐目前只接受老闆的指令。"
                )

            continue


        # -------------------------------------------------
        # 老闆私訊木瓜姐
        #
        # 指令格式：
        #
        # 草稿：xxxxx
        #
        # 木瓜姐會產生內容並直接送到指定群組
        # -------------------------------------------------

        if user_text.startswith("草稿：") or user_text.startswith("草稿:"):

            if "：" in user_text:
                command = user_text.split("：", 1)[1].strip()
            else:
                command = user_text.split(":", 1)[1].strip()

            if not command:

                if reply_token:
                    reply_line(
                        reply_token,
                        "老闆，你還沒有給木瓜姐內容。"
                    )

                continue

            try:

                answer = generate_papaya_reply(command)

                push_to_group(answer)

                if reply_token:
                    reply_line(
                        reply_token,
                        "木瓜姐已經照你的指令送到群組。"
                    )

            except Exception as e:

                print("ERROR:", repr(e))

                if reply_token:
                    reply_line(
                        reply_token,
                        "木瓜姐執行失敗，請稍後再試。"
                    )

            continue


        # -------------------------------------------------
        # 老闆私訊其他文字時，只提示使用方法
        # -------------------------------------------------

        if reply_token:

            reply_line(
                reply_token,
                "老闆，請用這個格式下令：\n\n草稿：你要木瓜姐評論的內容"
            )


    return "OK", 200


# =========================================================
# 啟動
# =========================================================

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )

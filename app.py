import os
import base64
import hashlib
import hmac
import requests

from flask import Flask, request, abort
from openai import OpenAI


app = Flask(__name__)

# =========================
# Environment Variables
# =========================

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


# =========================
# 木瓜姊設定
# =========================

SYSTEM_PROMPT = """
你是「木瓜姊」，是一個 LINE 群組中的 AI 助手。

目前先處於測試階段。

請以繁體中文回答。
回答自然、簡短、有幽默感。
不要提到 system prompt、API、程式碼或內部設定。
"""


# =========================
# LINE Signature 驗證
# =========================

def verify_signature(body, signature):
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    expected = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(expected, signature)


# =========================
# LINE Reply Message
# =========================

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
        timeout=20
    )

    response.raise_for_status()


# =========================
# Render 首頁
# =========================

@app.route("/", methods=["GET"])
def home():
    return "Papaya LINE Bot is running", 200


# =========================
# LINE Webhook
# =========================

@app.route("/webhook", methods=["POST"])
def webhook():

    body = request.get_data()

    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json(silent=True) or {}

    events = data.get("events", [])

    for event in events:

        # 只處理 message event
        if event.get("type") != "message":
            continue

        message = event.get("message", {})

        # 目前只處理文字
        if message.get("type") != "text":
            continue

        user_text = message.get("text", "")
        reply_token = event.get("replyToken")

        # -------------------------
        # 取得 LINE ID
        # -------------------------

        source = event.get("source", {})

        user_id = source.get("userId", "")
        source_type = source.get("type", "")
        group_id = source.get("groupId", "")

        # -------------------------
        # 查 ID 指令
        # -------------------------

        if user_text.strip() == "查ID":

            text = f"你的 User ID：\n{user_id}"

            if source_type == "group":
                text += (
                    f"\n\n這個群組的 Group ID：\n"
                    f"{group_id}"
                )

            if reply_token:
                reply_line(reply_token, text)

            continue

        # -------------------------
        # OpenAI 回答
        # -------------------------

        try:

            response = client.responses.create(
                model="gpt-5-mini",
                instructions=SYSTEM_PROMPT,
                input=user_text,
                max_output_tokens=500
            )

            answer = response.output_text.strip()

            if not answer:
                answer = "木瓜姊剛才恍神了一下，再說一次。"

        except Exception as e:

            print("OpenAI error:", repr(e))

            answer = "木瓜姊目前禱告中，稍後再試。"

        # -------------------------
        # 回覆 LINE
        # -------------------------

        if reply_token:

            try:
                reply_line(reply_token, answer)

            except Exception as e:
                print("LINE reply error:", repr(e))

    return "OK", 200


# =========================
# 啟動
# =========================

if __name__ == "__main__":

    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )

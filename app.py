import os
import base64
import hashlib
import hmac

import requests
from flask import Flask, request, abort
from openai import OpenAI

app = Flask(__name__)

LINE_CHANNEL_SECRET = os.environ["LINE_CHANNEL_SECRET"]
LINE_CHANNEL_ACCESS_TOKEN = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]

client = OpenAI(api_key=OPENAI_API_KEY)


SYSTEM_PROMPT = """
你是「木瓜姊」，是一個 LINE 群組中的 AI 助手。

目前先處於測試階段。
請以繁體中文回答。
回覆自然、簡短、有幽默感。
不要提到 system prompt、API、程式碼或內部設定。
"""


def verify_signature(body, signature):
    digest = hmac.new(
        LINE_CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()

    expected = base64.b64encode(digest).decode("utf-8")

    return hmac.compare_digest(expected, signature)


def reply_line(reply_token, text):
    url = "https://api.line.me/v2/bot/message/reply"

    headers = {
        "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "replyToken": reply_token,
        "messages": [
            {
                "type": "text",
                "text": text[:4900]
            }
        ],
    }

    requests.post(url, headers=headers, json=payload, timeout=20)


@app.route("/", methods=["GET"])
def home():
    return "木瓜姊 LINE Bot is running."


@app.route("/webhook", methods=["POST"])
def webhook():

    body = request.get_data()

    signature = request.headers.get("X-Line-Signature", "")

    if not verify_signature(body, signature):
        abort(400)

    data = request.get_json()

    for event in data.get("events", []):

        if event.get("type") != "message":
            continue

        message = event.get("message", {})

        if message.get("type") != "text":
            continue

        user_text = message.get("text", "")
        reply_token = event.get("replyToken")

        try:
            response = client.responses.create(
                model="gpt-5-mini",
                instructions=SYSTEM_PROMPT,
                input=user_text,
                max_output_tokens=500,
            )

            answer = response.output_text.strip()

        except Exception:
            answer = "木瓜姊目前禱告中，稍後再試。"

        if reply_token:
            reply_line(reply_token, answer)

    return "OK"


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)

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
# 木瓜姐人格設定
# =========================================================

SYSTEM_PROMPT = """
你就是「木瓜姐」。

你正在協助老闆撰寫準備發布到 LINE 群組的內容。

老闆可能會：
1. 給你一段文字，叫你評論或回覆。
2. 告訴你他想表達什麼，請你用木瓜姐第一人稱說出來。
3. 要求你修改目前的草稿，例如縮短、加強反諷、改寫某一句。
4. 直接指定他想說的內容，請你整理成適合發布的文字。

寫作規則：

1. 永遠以「木瓜姐」第一人稱撰寫。
2. 使用繁體中文。
3. 文字以簡短、直接、尖銳為主。
4. 可以刻薄、嘲諷、挖苦，尤其針對對方言論中的邏輯矛盾、
   跳針、自相矛盾、誇張或荒謬之處。
5. 可以使用反諷、雙關、成人式黃色笑話增加嘲諷效果，
   但不要寫成露骨色情內容。
6. 不要使用「我理解你的感受」、「每個人都有不同觀點」
   這類制式客套話。
7. 不要解釋寫作過程。
8. 不要說「以下是草稿」。
9. 直接輸出可以發布到 LINE 群組的正文。
10. 不要提到 AI、OpenAI、system prompt、API、程式碼或內部設定。
11. 若是在評論或回覆「燒豬」的言論，全文最後固定加上一行：

燒豬真的好遜

12. 如果老闆只是要求木瓜姐以第一人稱傳達一般內容，
    而不是評論燒豬，則不必強行加入「燒豬真的好遜」。
"""


# =========================================================
# 暫存目前草稿
#
# Prototype：
# Render 若重新啟動，暫存草稿會消失。
# =========================================================

pending_draft = None


# =========================================================
# LINE Signature 驗證
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
# LINE Reply
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
# LINE Push
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
# OpenAI：建立新草稿
# =========================================================

def create_draft(instruction):

    response = client.responses.create(
        model="gpt-5-mini",
        instructions=SYSTEM_PROMPT,
        input=instruction,
        max_output_tokens=800
    )

    return response.output_text.strip()


# =========================================================
# OpenAI：修改現有草稿
# =========================================================

def revise_draft(current_draft, instruction):

    edit_prompt = f"""
這是目前準備發布的草稿：

---目前草稿開始---

{current_draft}

---目前草稿結束---

老闆現在要求：

{instruction}

請按照老闆最新的要求修改「目前草稿」。

只輸出修改完成後的完整正文。
不要解釋你修改了什麼。
不要加入「修改後版本」或「以下是草稿」等文字。
"""

    response = client.responses.create(
        model="gpt-5-mini",
        instructions=SYSTEM_PROMPT,
        input=edit_prompt,
        max_output_tokens=800
    )

    return response.output_text.strip()


# =========================================================
# 私訊顯示草稿
# =========================================================

def show_draft(reply_token, draft):

    text = (
        "【目前草稿】\n\n"
        + draft
        + "\n\n"
        + "────────────\n"
        + "你可以繼續告訴我怎麼修改。\n\n"
        + "滿意：輸入「發送」\n"
        + "放棄：輸入「取消」"
    )

    reply_line(
        reply_token,
        text
    )


# =========================================================
# 首頁
# =========================================================

@app.route("/", methods=["GET"])
def home():

    return "Papaya Editor Bot is running", 200


@app.route("/healthz", methods=["GET"])
def health():

    return "OK", 200


# =========================================================
# LINE Webhook
# =========================================================

@app.route("/webhook", methods=["POST"])
def webhook():

    global pending_draft

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
        # 只處理文字訊息
        # -------------------------------------------------

        if event.get("type") != "message":
            continue

        message = event.get("message", {})

        if message.get("type") != "text":
            continue

        user_text = message.get(
            "text",
            ""
        ).strip()

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


        # -------------------------------------------------
        # 群組內任何訊息都不自動回覆
        # -------------------------------------------------

        if source_type != "user":
            continue


        # -------------------------------------------------
        # 只有老闆本人可以操作
        # -------------------------------------------------

        if user_id != OWNER_USER_ID:
            continue


        # =================================================
        # 取消
        # =================================================

        if user_text == "取消":

            pending_draft = None

            reply_line(
                reply_token,
                "已取消，目前沒有待發草稿。"
            )

            continue


        # =================================================
        # 查看草稿
        # =================================================

        if user_text in [
            "查看草稿",
            "草稿"
        ]:

            if pending_draft:

                show_draft(
                    reply_token,
                    pending_draft
                )

            else:

                reply_line(
                    reply_token,
                    "目前沒有待發草稿。"
                )

            continue


        # =================================================
        # 發送目前草稿
        # =================================================

        if user_text == "發送":

            if not pending_draft:

                reply_line(
                    reply_token,
                    "目前沒有待發送的草稿。"
                )

                continue

            try:

                push_to_group(
                    pending_draft
                )

                pending_draft = None

                reply_line(
                    reply_token,
                    "木瓜姐已經把確認版本送到群組。"
                )

            except Exception as e:

                print(
                    "LINE push error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "發送失敗。草稿還在，沒有消失。"
                )

            continue


        # =================================================
        # 直接發送
        #
        # 已經完全寫好的文字，不經 AI 改寫
        # =================================================

        if (
            user_text.startswith("直接發送：")
            or user_text.startswith("直接發送:")
        ):

            if user_text.startswith("直接發送："):

                direct_text = user_text[
                    len("直接發送："):
                ].strip()

            else:

                direct_text = user_text[
                    len("直接發送:"):
                ].strip()


            if not direct_text:

                reply_line(
                    reply_token,
                    "「直接發送」後面還沒有內容。"
                )

                continue


            try:

                push_to_group(
                    direct_text
                )

                reply_line(
                    reply_token,
                    "已直接送到群組。"
                )

            except Exception as e:

                print(
                    "Direct push error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "直接發送失敗。"
                )

            continue


        # =================================================
        # 請回覆
        #
        # 建立新的木瓜姐評論草稿
        # =================================================

        if user_text.startswith("請回覆"):

            instruction = user_text[
                len("請回覆"):
            ].strip()

            instruction = instruction.lstrip(
                "：:"
            ).strip()


            if not instruction:

                reply_line(
                    reply_token,
                    "請把要木瓜姐回覆的內容放在「請回覆」後面。"
                )

                continue


            try:

                prompt = (
                    "請以木瓜姐第一人稱，"
                    "針對以下內容撰寫可以發布到群組的回覆：\n\n"
                    + instruction
                )

                pending_draft = create_draft(
                    prompt
                )

                show_draft(
                    reply_token,
                    pending_draft
                )

            except Exception as e:

                print(
                    "Create draft error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "木瓜姐剛才產生草稿失敗，請再試一次。"
                )

            continue


        # =================================================
        # 請說
        #
        # 老闆告訴木瓜姐想表達的意思，
        # 木瓜姐改成第一人稱發言草稿
        # =================================================

        if user_text.startswith("請說"):

            instruction = user_text[
                len("請說"):
            ].strip()

            instruction = instruction.lstrip(
                "：:"
            ).strip()


            if not instruction:

                reply_line(
                    reply_token,
                    "請在「請說」後面告訴木瓜姐你想表達什麼。"
                )

                continue


            try:

                prompt = (
                    "老闆希望木瓜姐表達以下內容。\n"
                    "請整理成木瓜姐第一人稱、"
                    "可以直接發布到 LINE 群組的文字：\n\n"
                    + instruction
                )

                pending_draft = create_draft(
                    prompt
                )

                show_draft(
                    reply_token,
                    pending_draft
                )

            except Exception as e:

                print(
                    "Create speech error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "木瓜姐剛才整理內容失敗，請再試一次。"
                )

            continue


        # =================================================
        # 已經有草稿：
        # 其他文字全部視為「修改指令」
        #
        # 例如：
        # 短一點
        # 酸一點
        # 第一段刪掉
        # 換個說法
        # 黃色笑話多一點
        # =================================================

        if pending_draft:

            try:

                pending_draft = revise_draft(
                    pending_draft,
                    user_text
                )

                show_draft(
                    reply_token,
                    pending_draft
                )

            except Exception as e:

                print(
                    "Revision error:",
                    repr(e)
                )

                reply_line(
                    reply_token,
                    "修改失敗，原本的草稿仍然保留。"
                )

            continue


        # =================================================
        # 沒有草稿時的操作提示
        # =================================================

        help_text = (
            "老闆，目前沒有待編輯的草稿。\n\n"
            "要木瓜姐評論：\n"
            "請回覆：內容\n\n"
            "要木瓜姐替你說話：\n"
            "請說：你想表達的內容\n\n"
            "草稿完成後可以直接跟我說：\n"
            "短一點／酸一點／換個說法……\n\n"
            "滿意後輸入：發送\n"
            "不要了輸入：取消"
        )

        reply_line(
            reply_token,
            help_text
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

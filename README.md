# Papaya-line-boat

Line AI boat (木瓜姐).

## Group comeback mode (吵架模式)

In a group chat, when the sender’s LINE display name is `Hung Chang Chen` (case-insensitive; extra spaces ignored) and the message insults the bot, the AI, or its replies, the bot answers with **one** short comeback. The name is read from the [group member profile API](https://developers.line.biz/en/reference/messaging-api/#get-group-member-profile) and cached in memory for 12 hours. The reply uses the reply token, not push, so it does not spend the monthly push quota.

Other group messages stay silent. Other private messages, `請發送`, `/fix`, `/transcribe`, and `/dl` are unchanged.

This bot already sets `OWNER_USER_ID`, so only that user can toggle the mode. Typing either line in the 1:1 private chat or in a group flips the same global switch, which controls group comebacks:

- `吵架模式開`
- `吵架模式關`

A trailing `。` or `!` is ignored. Anyone else typing them is ignored. The bot replies in that same chat with `吵架模式開了。` or `吵架模式關了。` These two lines are not sent to the private-chat model and are not pushed to the group.

The switch defaults to **on** after deploy. `吵架模式開` / `吵架模式關` override that until the process restarts. A restart or a new deploy clears the in-memory override and the name cache, then starts again from `COMEBACK_MODE` (on when that variable is unset). Keep gunicorn at one worker, the same constraint `/dl` already documents, so the override is the one the group sees.

| Variable | Required | Default | Meaning |
| --- | --- | --- | --- |
| `COMEBACK_MODE` | no | on | Unset, empty, `on`, `1`, or `true` starts enabled. `off`, `0`, `false`, `no`, or `關` starts disabled. |
| `COMEBACK_CLASSIFY_MODEL` | no | `gpt-4o-mini` | Used only when that sender’s message looks disparaging but is not a keyword hit. `gpt-5-mini` is accepted. A failure or a `no` sends nothing. |

No new secret. The profile call uses `LINE_CHANNEL_ACCESS_TOKEN`. Classification uses the existing `OPENAI_API_KEY`.

Keyword hits (insult word next to AI／人工智慧／機器人／回覆／回复／木瓜姊／木瓜姐／木瓜妹, or the set phrase 人工智障) reply immediately. Examples: 垃圾AI, 垃圾回覆, 垃圾AI回覆, 廢物AI, 爛AI, AI很爛, 智障回覆, 垃圾機器人. Spaces and `AI` / `ai` / `ＡＩ` do not matter.

Insult words: 垃圾, 廢物, 廢柴, 智障, 腦殘, 笨蛋, 白痴, 白癡, 低能, 沒用, 狗屁, 狗屎, 扯淡, 靠北, 三小, 有病, 機車, 爛, 廢, 蠢, 傻, 笨, 鳥, 糟, 遜, 菜, 雷, 差, 爛透, 爛透了. English, next to AI: garbage, trash, stupid, dumb, useless, lame, worst, shitty, bad, sucks.

`垃圾分類` and `垃圾車` are not insults. A short leftover such as「這AI有點不行」is the only case sent to the classifier.

Comeback lines:

1. 垃圾回覆，專送垃圾人。
2. 你丟什麼，我就回什麼。
3. 嫌爛？這則就是你的份。
4. 水準在這，回覆就在這。
5. 垃圾進，垃圾出，公平。
6. 這種話，只配這種回。

## Private AI proofreading (`POST /fix`)

Used by the static dictation page at https://glucose1979.github.io/dictation/ .

Set `FIX_PASSWORD` in the Render environment to enable the endpoint. If unset or empty, `/fix` stays disabled and returns 403. Do not commit the password; keep it only in env vars (same as `OPENAI_API_KEY`).

## Audio transcription (`POST /transcribe`)

Same page, same password. The browser uploads a recording and the editor receives the transcript, which the user can then send through `/fix`.

### Request

`POST /transcribe` with `multipart/form-data` (and `OPTIONS` for the CORS preflight, same origin and headers as `/fix`).

| Field | Required | Meaning |
| --- | --- | --- |
| `file` | yes | Audio file. OpenAI accepts flac, mp3, mp4, mpeg, mpga, m4a, ogg, wav, webm. |
| `lang` | no | `zh` or `en`. `zh-TW` and `en-US` (the codes the dictation page already stores) are accepted and treated as `zh` and `en`. Omit to auto-detect. |
| `password` | yes | **Form field** named `password`. This is the same field name `/fix` reads from its JSON body. It is not a header. |

`/transcribe` is disabled unless `FIX_PASSWORD` is set (shared with `/fix`).

- Password unset: `403` `{"error": "disabled"}`
- Missing or wrong password: `401` `{"error": "unauthorized"}`

Those two responses match `/fix` exactly (status and JSON shape). Every other response uses `ok`:

- Success: `200` `{"ok": true, "text": "..."}`
- Failure: `{"ok": false, "error": "<繁體中文，可直接顯示給使用者>"}` with a 4xx/5xx status

### Limits

- Upload cap: **100 MB** (larger bodies are rejected with 413 before the handler runs).
- OpenAI accepts about **25 MB per request**. Files up to **24 MB** are sent as one request. Larger files are split with ffmpeg (stream copy, re-encode only if a piece is still too big) into at most **8** pieces under 24 MB.
- Duration cap when ffmpeg can read it: **60 minutes**.
- The handler stops around **80 seconds** so it can still return JSON inside Render's request limit.

`imageio-ffmpeg` ships a static ffmpeg binary, so splitting works on Render's free instance (512 MB RAM, no system ffmpeg). Files over 24 MB are rejected with a short Chinese message if that binary cannot be started.

### Model and cost

Default model is `gpt-4o-mini-transcribe` (override with env `TRANSCRIBE_MODEL`, for example `whisper-1` or `gpt-4o-mini-transcribe-2025-12-15`). The key is the existing `OPENAI_API_KEY`. Logs include model, byte size, duration, chunk count, and language — not the transcript and not the password.

### Render

Gunicorn's default worker timeout is 30 seconds, which is too short for a transcription. Add `--timeout 90` to the existing start command. If it is still the default `gunicorn app:app`, use:

```
gunicorn --bind 0.0.0.0:$PORT --timeout 90 app:app
```

A start command set in the Render dashboard overrides a Procfile, so change it there if one is already set. Render's proxy still cuts the request off at about 100 seconds; this app aims to finish or return an error before that. No new secret is required beyond the existing `OPENAI_API_KEY` and `FIX_PASSWORD`. Optional: `TRANSCRIBE_MODEL`.

## Private video download (`GET /dl`)

Unlisted page for the owner’s phone. It is not linked from `/` or any other route. The HTML response sends `X-Robots-Tag: noindex` and a `noindex` robots meta tag.

The page is Traditional Chinese, with large type and one red **下載** button. The password is stored in the browser’s `localStorage` on that phone. Downloading calls same-origin JSON endpoints that reuse `FIX_PASSWORD` (the same value as `/transcribe` and `/fix`):

| Request | Password | Result |
| --- | --- | --- |
| `POST /dl/start` | JSON field `password`, plus `url` | `401` `{"error":"unauthorized"}` if wrong. `403` `{"error":"disabled"}` if `FIX_PASSWORD` is unset. Otherwise `{ok, job_id, token}`. |
| `POST /dl/status` | JSON field `password`, plus `job_id` | Progress, or a Traditional Chinese `error` the page can show as-is. |
| `GET /dl/file/<job_id>?token=` | token from `start` (not the password) | The mp4, `Content-Disposition` attachment, UTF-8 filename. |

No new environment variable. Leave `FIX_PASSWORD` set. Do not add a link to `/dl` on the public site.

The browser returns immediately. yt-dlp runs in a background thread in the same process, and the page polls until the file is ready. That keeps each HTTP call inside Render’s request limit. The phone then saves the file with the browser’s normal download.

### Limits

- One download at a time.
- Video longer than **20 minutes** is rejected.
- File larger than **300 MB** is rejected.
- The server-side download gives up after about **4 minutes**.
- The finished file is kept for about **10 minutes** so the green **存到手機** button still works, then the temp directory is deleted. Partial files are deleted as soon as a download fails.
- Jobs live in this process’s memory. Keep gunicorn at **one worker** (the default for `gunicorn --timeout 90 app:app`). Extra workers would not see the job.

### Formats and ffmpeg

Preferred output is an Android-friendly mp4: H.264 (`avc1`) video plus `m4a` audio, merged to mp4 when ffmpeg is available, otherwise the best single-file mp4.

ffmpeg is the same binary `/transcribe` already uses: `ffmpeg` on `PATH` if present, otherwise the static build from `imageio-ffmpeg` (already in `requirements.txt`). Render’s Python image does **not** include a system ffmpeg or ffprobe. The static ffmpeg binary is enough to merge H.264 + AAC into mp4. ffprobe is not shipped with `imageio-ffmpeg`; some HLS container fixups are skipped. No apt package is required for the merge.

`yt-dlp` is pinned to **2026.8.19**, with `yt-dlp-ejs==0.8.0` (YouTube’s JavaScript challenge scripts). A JavaScript runtime is **not** installed on Render. YouTube often fails closed without one, and datacenter IPs are often blocked even when the runtime exists.

### Render checklist

- Start command stays `gunicorn --timeout 90 app:app` (or the longer form already documented for `/transcribe`). Do not raise `--workers`.
- `FIX_PASSWORD` must be set or `/dl` returns 403. No additional secret.
- Do not expect a system ffmpeg package. `imageio-ffmpeg` covers merging.
- Optional and easy to get wrong: installing Deno or Node so yt-dlp can solve YouTube’s player challenge. That still does not fix IP blocks. This sample does not install a JS runtime.
- The last hop that saves the file onto the phone is one HTTP response. Render’s proxy cuts requests off at about 100 seconds, so a large file on a slow mobile connection can fail after the server already has it. Wi-Fi is the reliable case. The green button retries until the 10-minute file expires.

### What often fails from Render

YouTube, Instagram, TikTok, and Facebook frequently refuse datacenter addresses (“sign in to confirm you’re not a bot”, HTTP 403, login walls). The page shows a short Traditional Chinese message and does not try cookies, proxies, or other ways around the block. A direct public mp4, or a site that still allows the host, can succeed. Login-required, private, age-restricted, live, and geo-blocked videos are refused with a Chinese explanation.

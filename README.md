# Papaya-line-boat

Line AI boat (木瓜姐).

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

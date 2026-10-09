# Papaya-line-boat

Line AI boat (木瓜姐).

## Private AI proofreading (`POST /fix`)

Used by the static dictation page at https://glucose1979.github.io/dictation/ .

Set `FIX_PASSWORD` in the Render environment to enable the endpoint. If unset or empty, `/fix` stays disabled and returns 403. Do not commit the password; keep it only in env vars (same as `OPENAI_API_KEY`).

"""Private phone page for downloading one public video.

Same FIX_PASSWORD as /transcribe. The page is not linked from anywhere
else. yt-dlp runs in a background thread so each HTTP call stays short
enough for Render's request limit.
"""

import hashlib
import hmac
import ipaddress
import os
import re
import secrets
import shutil
import socket
import tempfile
import threading
import time
from urllib.parse import quote, urlparse

from flask import jsonify, make_response, request, send_file


DL_MAX_MB = 300
DL_MAX_MINUTES = 20
DL_MAX_BYTES = DL_MAX_MB * 1024 * 1024
DL_MAX_DURATION_SECONDS = DL_MAX_MINUTES * 60
DL_JOB_DEADLINE_SECONDS = 4 * 60
DL_JOB_TTL_SECONDS = 10 * 60
DL_SOCKET_TIMEOUT_SECONDS = 20

MSG_UNSUPPORTED = (
    "這個連結不支援。請換一個 YouTube、Instagram、"
    "TikTok、Facebook 或一般影片網址。"
)
MSG_LOGIN = "這個影片需要登入才能看，這裡沒辦法下載。"
MSG_BLOCKED = "網站擋住了這台伺服器，請稍後再試，或換一個連結。"
MSG_UNAVAILABLE = "這個影片不存在、已下架，或是私人影片。"
MSG_AGE = "這個影片有年齡限制，這裡沒辦法下載。"
MSG_GEO = "這個影片在伺服器所在的地區看不了。"
MSG_LIVE = "這是直播或預告，沒辦法下載。"
MSG_TOO_LONG = f"影片超過 {DL_MAX_MINUTES} 分鐘，請換較短的影片。"
MSG_TOO_BIG = f"檔案超過 {DL_MAX_MB} MB，請換較短的影片。"
MSG_NO_MP4 = "找不到可以在手機上播放的 MP4，請換一個連結。"
MSG_TIMEOUT = "下載時間太久或連線逾時，請改下載較短的影片。"
MSG_PLAYLIST = "請貼上單一支影片的連結，不要貼整份清單。"
MSG_GENERIC = "下載失敗，請檢查連結後再試一次。"
MSG_EMPTY_URL = "請貼上影片連結。"
MSG_BAD_URL = "請貼上以 http 或 https 開頭的影片連結。"
MSG_BAD_HOST = "這個連結不能用。"
MSG_NOT_FOUND_HOST = "找不到這個網址，請再檢查一次。"
MSG_LONG_URL = "連結太長，請換一個影片連結。"
MSG_BUSY = "正在下載另一部影片，請等它完成再按。"
MSG_EXPIRED = "這次下載已經過期，請再按一次下載。"
MSG_NOT_READY = "影片還沒準備好，請稍候再按。"

# With ffmpeg: H.264 in mp4 + m4a, then any single mp4.
# Without ffmpeg: a single mp4 only. Separate streams cannot be merged.
_FORMAT_WITH_FFMPEG = (
    "bv*[ext=mp4][vcodec^=avc1][height<=720]+ba[ext=m4a]"
    "/bv*[ext=mp4][vcodec^=avc1]+ba[ext=m4a]"
    "/b[ext=mp4][vcodec^=avc1]"
    "/b[ext=mp4]"
)
_FORMAT_SINGLE_MP4 = "b[ext=mp4][vcodec^=avc1]/b[ext=mp4]"
_FORMAT_SORT = ["res:720", "vcodec:h264", "acodec:aac", "ext:mp4:m4a"]

_URL_IN_TEXT = re.compile(r"https?://\S+")
_BAD_FILENAME = re.compile(r'[\x00-\x1f\\/:*?"<>|]+')
_BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
    "metadata.internal",
}

_jobs = {}
_lock = threading.Lock()
_ffmpeg_getter = None
_password_configured = None
_password_matches = None


class DlError(Exception):

    def __init__(self, message, status=422):

        super().__init__(message)
        self.status = status


class _QuietLog:

    def __init__(self):

        self.lines = []

    def _keep(self, message):

        if message:
            self.lines.append(str(message))

    debug = _keep
    info = _keep
    warning = _keep
    error = _keep


def reset_state():

    with _lock:
        workdirs = [
            job.get("workdir")
            for job in _jobs.values()
        ]
        _jobs.clear()

    for workdir in workdirs:
        if workdir:
            shutil.rmtree(workdir, ignore_errors=True)


def _spawn(target, args):

    thread = threading.Thread(
        target=target,
        args=args,
        daemon=True,
    )
    thread.start()
    return thread


def _ip_is_public(ip):

    return bool(ip.is_global)


def _blocked_hostname(hostname):

    host = (hostname or "").strip().rstrip(".").lower()

    if not host or host in _BLOCKED_HOSTS:
        return True

    if host.endswith(
        (".localhost", ".local", ".internal", ".localdomain")
    ):
        return True

    return False


def validate_url(raw, resolve=None):

    if not isinstance(raw, str):
        raise DlError(MSG_EMPTY_URL, 400)

    url = raw.strip()

    if not url:
        raise DlError(MSG_EMPTY_URL, 400)

    if len(url) > 2000:
        raise DlError(MSG_LONG_URL, 400)

    parsed = urlparse(url)

    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise DlError(MSG_BAD_URL, 400)

    if parsed.username or parsed.password:
        raise DlError(MSG_BAD_HOST, 400)

    hostname = parsed.hostname

    if _blocked_hostname(hostname):
        raise DlError(MSG_BAD_HOST, 400)

    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        literal = None

    if literal is not None:
        if not _ip_is_public(literal):
            raise DlError(MSG_BAD_HOST, 400)
        return url

    if resolve is None:
        resolve = socket.getaddrinfo

    try:
        infos = resolve(hostname, None)
    except OSError:
        raise DlError(MSG_NOT_FOUND_HOST, 400)

    addresses = []

    for info in infos:
        try:
            addresses.append(ipaddress.ip_address(info[4][0]))
        except (ValueError, IndexError):
            continue

    if not addresses or any(not _ip_is_public(ip) for ip in addresses):
        raise DlError(MSG_BAD_HOST, 400)

    return url


def download_filename(title):

    text = _BAD_FILENAME.sub(" ", title or "")
    text = re.sub(r"\s+", " ", text).strip(" .")
    text = text[:60].rstrip(" .")

    if not text:
        text = "影片"

    return text + ".mp4"


def map_download_error(text):

    lowered = _URL_IN_TEXT.sub(" ", text or "").lower()

    if (
        "max-filesize" in lowered
        or "larger than max" in lowered
        or "file is larger" in lowered
    ):
        return MSG_TOO_BIG

    if (
        "requested format is not available" in lowered
        or "no video formats found" in lowered
    ):
        return MSG_NO_MP4

    if (
        "unsupported url" in lowered
        or "no suitable extractor" in lowered
        or "unsupported url scheme" in lowered
    ):
        return MSG_UNSUPPORTED

    if (
        "age-restricted" in lowered
        or "age restricted" in lowered
        or "confirm your age" in lowered
        or "inappropriate for some users" in lowered
    ):
        return MSG_AGE

    if (
        "not a bot" in lowered
        or "captcha" in lowered
        or "http error 403" in lowered
        or "http error 429" in lowered
        or "403: forbidden" in lowered
        or "forbidden" in lowered
        or "access denied" in lowered
    ):
        return MSG_BLOCKED

    if (
        "login required" in lowered
        or "please log in" in lowered
        or "please sign in" in lowered
        or "sign in to" in lowered
        or "log in to" in lowered
        or "private video" in lowered
        or "video is private" in lowered
        or "members-only" in lowered
        or "members only" in lowered
        or "join this channel" in lowered
    ):
        return MSG_LOGIN

    if (
        "not available in your country" in lowered
        or "geographic" in lowered
        or "geo restricted" in lowered
        or "geo-restricted" in lowered
    ):
        return MSG_GEO

    if (
        "is live" in lowered
        or "live event" in lowered
        or "currently live" in lowered
        or "is_live" in lowered
    ):
        return MSG_LIVE

    if (
        "timed out" in lowered
        or "timeout" in lowered
        or "urlopen error" in lowered
        or "network is unreachable" in lowered
        or "temporary failure in name resolution" in lowered
    ):
        return MSG_TIMEOUT

    if "ffmpeg is not installed" in lowered:
        return MSG_NO_MP4

    if "playlist" in lowered:
        return MSG_PLAYLIST

    if (
        "http error 404" in lowered
        or "404: not found" in lowered
        or "unavailable" in lowered
        or "has been removed" in lowered
        or "does not exist" in lowered
        or "video is private" in lowered
    ):
        return MSG_UNAVAILABLE

    return MSG_GENERIC


def _metadata_incomplete(incomplete):

    # yt-dlp passes True, or a set of format fields that are not the
    # video's duration. Duration is reliable unless incomplete is True
    # or the set explicitly includes it.
    if incomplete is True:
        return True

    if isinstance(incomplete, (set, frozenset)):
        return "duration" in incomplete

    return bool(incomplete)


def precheck_message(info, incomplete=False):

    if not isinstance(info, dict):
        return None

    if info.get("_type") in ("playlist", "multi_video"):
        return MSG_PLAYLIST

    live_status = info.get("live_status")

    if info.get("is_live") or live_status in ("is_live", "is_upcoming"):
        return MSG_LIVE

    if _metadata_incomplete(incomplete):
        return None

    duration = info.get("duration")

    if isinstance(duration, (int, float)) and not isinstance(duration, bool):
        if duration > DL_MAX_DURATION_SECONDS:
            return MSG_TOO_LONG

    return None


def build_ydl_opts(workdir, ffmpeg_location, on_progress, deadline, logger):

    from yt_dlp.utils import DownloadCancelled

    def match_filter(info, *, incomplete):

        # Returning a string makes yt-dlp skip the download. Raising
        # here is turned into a generic "download was cancelled".
        return precheck_message(info, incomplete=incomplete)

    def hook(status):

        if time.monotonic() > deadline:
            raise DownloadCancelled(MSG_TIMEOUT)

        state = status.get("status")

        if state == "downloading":
            total = (
                status.get("total_bytes")
                or status.get("total_bytes_estimate")
                or 0
            )
            done = status.get("downloaded_bytes") or 0

            if total:
                percent = int(done * 100 / total)
                percent = max(0, min(99, percent))
                on_progress(percent, f"正在下載… {percent}%")
            else:
                on_progress(None, "正在下載，請稍候…")

        elif state == "finished":
            on_progress(99, "正在整理影片，請稍候…")

    can_merge = bool(ffmpeg_location)
    opts = {
        "format": _FORMAT_WITH_FFMPEG if can_merge else _FORMAT_SINGLE_MP4,
        "format_sort": list(_FORMAT_SORT),
        "outtmpl": "media.%(ext)s",
        "paths": {
            "home": workdir,
            "temp": workdir,
        },
        "noplaylist": True,
        "max_filesize": DL_MAX_BYTES,
        "max_downloads": 1,
        "socket_timeout": DL_SOCKET_TIMEOUT_SECONDS,
        "retries": 1,
        "fragment_retries": 1,
        "extractor_retries": 0,
        "file_access_retries": 1,
        "concurrent_fragment_downloads": 1,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "cachedir": False,
        "restrictfilenames": False,
        "windowsfilenames": True,
        "overwrites": True,
        "ignoreerrors": False,
        "geo_bypass": False,
        "progress_hooks": [hook],
        "match_filter": match_filter,
        "logger": logger,
    }

    if can_merge:
        opts["merge_output_format"] = "mp4"
        opts["ffmpeg_location"] = ffmpeg_location

    return opts


def _exception_text(exc):

    parts = []
    seen = set()
    current = exc

    while current is not None and id(current) not in seen:
        seen.add(id(current))
        parts.append(str(current))
        current = current.__cause__ or current.__context__

    return "\n".join(parts)


def _find_output(workdir, info):

    candidates = []

    def add(path):

        if not path or not os.path.isfile(path):
            return

        if path.endswith(".part") or path.endswith(".ytdl"):
            return

        candidates.append(path)

    if isinstance(info, dict):
        for item in info.get("requested_downloads") or []:
            if isinstance(item, dict):
                add(item.get("filepath"))
                add(item.get("filename"))
                add(item.get("_filename"))

        add(info.get("filepath"))
        add(info.get("_filename"))

    if os.path.isdir(workdir):
        for name in os.listdir(workdir):
            add(os.path.join(workdir, name))

    mp4s = [
        path for path in candidates
        if path.lower().endswith(".mp4")
    ]
    pool = mp4s or []

    if not pool:
        return None

    return max(pool, key=os.path.getsize)


def _keep_only(workdir, path):

    keep = os.path.abspath(path)

    for name in os.listdir(workdir):
        candidate = os.path.join(workdir, name)

        if os.path.abspath(candidate) == keep:
            continue

        if os.path.isdir(candidate):
            shutil.rmtree(candidate, ignore_errors=True)
        else:
            try:
                os.remove(candidate)
            except OSError:
                pass


def _accepted_download(info, workdir, logger):

    logged = "\n".join(logger.lines)

    if isinstance(info, dict):
        early = precheck_message(info, incomplete=False)

        if early:
            status = 413 if early == MSG_TOO_LONG else 422
            raise DlError(early, status)

    path = _find_output(workdir, info if isinstance(info, dict) else None)

    if path is None:
        if "max-filesize" in logged.lower() or "larger than max" in logged.lower():
            raise DlError(MSG_TOO_BIG, 413)

        raise DlError(map_download_error(logged) if logged else MSG_NO_MP4, 422)

    size = os.path.getsize(path)

    if size <= 0:
        raise DlError(MSG_NO_MP4, 422)

    if size > DL_MAX_BYTES:
        raise DlError(MSG_TOO_BIG, 413)

    _keep_only(workdir, path)
    title = ""

    if isinstance(info, dict) and isinstance(info.get("title"), str):
        title = info["title"]

    return path, download_filename(title)


def download_video(url, workdir, on_progress, ffmpeg_location, deadline):

    import yt_dlp
    from yt_dlp.utils import DownloadCancelled, MaxDownloadsReached

    logger = _QuietLog()
    opts = build_ydl_opts(
        workdir,
        ffmpeg_location,
        on_progress,
        deadline,
        logger,
    )
    info = None

    try:

        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

    except MaxDownloadsReached:
        # One video is the cap. yt-dlp raises after that file is saved.
        return _accepted_download(info, workdir, logger)

    except DownloadCancelled as exc:
        message = str(exc).strip() or MSG_TIMEOUT

        if message not in (
            MSG_TOO_LONG,
            MSG_TOO_BIG,
            MSG_LIVE,
            MSG_PLAYLIST,
            MSG_TIMEOUT,
        ):
            message = map_download_error(
                message + "\n" + "\n".join(logger.lines)
            )

        status = 413 if message in (MSG_TOO_LONG, MSG_TOO_BIG) else 422

        if message == MSG_TIMEOUT:
            status = 504

        raise DlError(message, status) from exc

    except DlError:
        raise

    except Exception as exc:
        message = map_download_error(
            _exception_text(exc) + "\n" + "\n".join(logger.lines)
        )
        status = 413 if message == MSG_TOO_BIG else 422

        if message == MSG_TIMEOUT:
            status = 504

        raise DlError(message, status) from exc

    return _accepted_download(info, workdir, logger)


def _expire_unlocked(now):

    delete_dirs = []

    for job_id, job in list(_jobs.items()):
        age = now - job["created_at"]
        status = job["status"]

        if status in ("queued", "downloading") and age > DL_JOB_DEADLINE_SECONDS + 20:
            job["status"] = "error"
            job["error"] = MSG_TIMEOUT
            job["message"] = MSG_TIMEOUT
            workdir = job.get("workdir")
            job["workdir"] = None
            job["path"] = None

            if workdir:
                delete_dirs.append(workdir)

        elif status in ("ready", "error", "finished") and age > DL_JOB_TTL_SECONDS:
            workdir = job.get("workdir")
            _jobs.pop(job_id, None)

            if workdir:
                delete_dirs.append(workdir)

    return delete_dirs


def _sweep(now=None):

    now = time.time() if now is None else now

    with _lock:
        delete_dirs = _expire_unlocked(now)

    for workdir in delete_dirs:
        shutil.rmtree(workdir, ignore_errors=True)


def _active_unlocked():

    return any(
        job["status"] in ("queued", "downloading")
        for job in _jobs.values()
    )


def _update(job_id, **fields):

    with _lock:
        job = _jobs.get(job_id)

        if not job or job["status"] in ("error", "ready", "finished"):
            return False

        job.update(fields)
        return True


def _fail(job_id, message):

    workdir = None

    with _lock:
        job = _jobs.get(job_id)

        if not job or job["status"] in ("ready", "error", "finished"):
            return

        workdir = job.get("workdir")
        job["status"] = "error"
        job["error"] = message
        job["message"] = message
        job["workdir"] = None
        job["path"] = None

    if workdir:
        shutil.rmtree(workdir, ignore_errors=True)

    print("dl:", "error")


def _mark_ready(job_id, path, filename):

    workdir = os.path.dirname(path)

    if os.path.isdir(workdir):
        _keep_only(workdir, path)

    size = os.path.getsize(path)

    with _lock:
        job = _jobs.get(job_id)

        if not job or job["status"] == "error":
            workdir = os.path.dirname(path)
            shutil.rmtree(workdir, ignore_errors=True)
            return

        job["status"] = "ready"
        job["path"] = path
        job["filename"] = filename
        job["progress"] = 100
        job["bytes"] = size
        job["message"] = "下載完成，正在存到手機…"
        job["error"] = None

    print("dl:", f"ready bytes={size}")


def _run_job(job_id, url):

    workdir = tempfile.mkdtemp(prefix="dl-job-")
    updated = _update(
        job_id,
        workdir=workdir,
        status="downloading",
        message="正在下載，請稍候…",
    )

    if not updated:
        shutil.rmtree(workdir, ignore_errors=True)
        return

    try:
        ffmpeg = _ffmpeg_getter() if _ffmpeg_getter else None
        path, filename = download_video(
            url,
            workdir,
            lambda percent, message: _update(
                job_id,
                progress=percent,
                message=message,
            ),
            ffmpeg,
            time.monotonic() + DL_JOB_DEADLINE_SECONDS,
        )
        _mark_ready(job_id, path, filename)

    except DlError as exc:
        _fail(job_id, str(exc))

    except Exception as exc:
        print("dl:", type(exc).__name__)
        _fail(job_id, MSG_GENERIC)


def _begin_job(url):

    now = time.time()
    job_id = secrets.token_urlsafe(16)
    token = secrets.token_urlsafe(24)

    with _lock:
        delete_dirs = _expire_unlocked(now)

        if _active_unlocked():
            busy = True
        else:
            busy = False
            _jobs[job_id] = {
                "token": token,
                "status": "queued",
                "progress": None,
                "message": "正在準備下載，請稍候…",
                "error": None,
                "path": None,
                "filename": None,
                "workdir": None,
                "created_at": now,
                "bytes": None,
            }

    for workdir in delete_dirs:
        shutil.rmtree(workdir, ignore_errors=True)

    if busy:
        raise DlError(MSG_BUSY, 429)

    host = urlparse(url).hostname or ""
    print("dl:", f"start host={host}")
    _spawn(_run_job, (job_id, url))

    return job_id, token


def _token_matches(expected, provided):

    if not isinstance(expected, str) or not isinstance(provided, str):
        return False

    if not expected or not provided:
        return False

    left = hashlib.sha256(provided.encode("utf-8")).digest()
    right = hashlib.sha256(expected.encode("utf-8")).digest()

    return hmac.compare_digest(left, right)


def _job_view(job_id):

    _sweep()

    with _lock:
        job = _jobs.get(job_id)

        if not job:
            return None

        return {
            "status": job["status"],
            "progress": job["progress"],
            "message": job["message"],
            "error": job["error"],
            "filename": job.get("filename") if job["status"] == "ready" else None,
        }


def _job_file(job_id, token):

    with _lock:
        job = _jobs.get(job_id)

        if not job:
            return None

        if not _token_matches(job.get("token"), token):
            return False

        if job["status"] != "ready" or not job.get("path"):
            return "not-ready"

        path = job["path"]
        filename = job.get("filename") or "影片.mp4"

    if not os.path.isfile(path):
        return None

    return path, filename


def _noindex(response):

    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Content-Type-Options"] = "nosniff"

    return response


def _json(payload, status):

    return _noindex(make_response(jsonify(payload), status))


def _authorize(password):

    if _password_configured is None or not _password_configured():
        return _json({"error": "disabled"}, 403)

    if not isinstance(password, str):
        password = ""

    if _password_matches is None or not _password_matches(password):
        return _json({"error": "unauthorized"}, 401)

    return None


def _page_html():

    return """<!DOCTYPE html>
<html lang="zh-Hant">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="googlebot" content="noindex, nofollow">
<meta name="referrer" content="no-referrer">
<title>下載影片</title>
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  html { font-size: 22px; }
  body {
    margin: 0 auto;
    padding: 20px 16px 48px;
    max-width: 480px;
    font-family: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
    color: #1a1a1a;
    background: #fff;
    line-height: 1.45;
  }
  h1 {
    font-size: 2rem;
    line-height: 1.2;
    margin: 0 0 12px;
  }
  .lead, .hint {
    font-size: 1.15rem;
    margin: 0 0 12px;
  }
  label {
    display: block;
    font-size: 1.2rem;
    font-weight: 700;
    margin: 18px 0 8px;
  }
  input[type="url"], input[type="password"], input[type="text"] {
    width: 100%;
    font-size: 1.15rem;
    padding: 16px;
    border: 3px solid #222;
    border-radius: 14px;
    background: #fff;
  }
  .check {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-top: 12px;
    font-size: 1.15rem;
    font-weight: 700;
  }
  .check input { width: 32px; height: 32px; }
  button.download {
    margin-top: 22px;
    width: 100%;
    min-height: 84px;
    background: #c40000;
    color: #fff;
    font-size: 1.8rem;
    font-weight: 800;
    border: 0;
    border-radius: 16px;
  }
  button.download:disabled { background: #777; }
  button.download:active { background: #8e0000; }
  .status {
    margin-top: 18px;
    font-size: 1.25rem;
    font-weight: 700;
    min-height: 1.4em;
  }
  .status.error {
    background: #ffe8e8;
    color: #8f0000;
    padding: 16px;
    border-radius: 14px;
    border: 3px solid #c40000;
  }
  .status.ok { color: #0b7a32; }
  .bar {
    height: 22px;
    background: #eee;
    border-radius: 11px;
    overflow: hidden;
    margin-top: 12px;
  }
  .bar[hidden] { display: none; }
  .bar > span {
    display: block;
    height: 100%;
    width: 0;
    background: #c40000;
  }
  .bar.unknown > span {
    width: 40%;
    animation: slide 1.1s ease-in-out infinite;
  }
  @keyframes slide {
    0% { transform: translateX(0); }
    50% { transform: translateX(140%); }
    100% { transform: translateX(0); }
  }
  a.save {
    display: block;
    margin-top: 16px;
    background: #0b7a32;
    color: #fff;
    text-align: center;
    text-decoration: none;
    font-size: 1.6rem;
    font-weight: 800;
    padding: 22px 16px;
    border-radius: 16px;
    min-height: 84px;
  }
  a.save[hidden] { display: none; }
  noscript p {
    font-size: 1.2rem;
    font-weight: 700;
  }
</style>
</head>
<body>
  <h1>下載影片</h1>
  <p class="lead">貼上影片連結，輸入密碼，再按紅色按鈕。影片會存到這支手機。</p>
  <p class="hint">YouTube、Instagram、TikTok、Facebook 都可以。最長約 20 分鐘，檔案不要超過 300 MB。</p>
  <form id="form" autocomplete="on">
    <label for="link">影片連結</label>
    <input id="link" name="link" type="url" inputmode="url" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" placeholder="貼上連結">
    <label for="password">密碼</label>
    <input id="password" name="password" type="password" autocomplete="current-password" placeholder="輸入密碼">
    <label class="check"><input id="showpass" type="checkbox"> 顯示密碼</label>
    <button class="download" id="go" type="submit">下載</button>
  </form>
  <div id="bar" class="bar" hidden><span id="barfill"></span></div>
  <div id="status" class="status" role="status" aria-live="polite"></div>
  <a id="save" class="save" hidden rel="noopener">存到手機</a>
  <noscript><p>請用手機的 Chrome 開啟這個頁面。</p></noscript>
  <script>
  (function () {
    var form = document.getElementById("form");
    var link = document.getElementById("link");
    var password = document.getElementById("password");
    var button = document.getElementById("go");
    var status = document.getElementById("status");
    var bar = document.getElementById("bar");
    var barFill = document.getElementById("barfill");
    var save = document.getElementById("save");
    var KEY = "dl-password";
    var timer = null;
    var polling = false;

    try {
      var saved = localStorage.getItem(KEY);
      if (saved) password.value = saved;
    } catch (err) {}

    function remember() {
      try { localStorage.setItem(KEY, password.value); } catch (err) {}
    }

    password.addEventListener("input", remember);
    password.addEventListener("change", remember);

    document.getElementById("showpass").addEventListener("change", function () {
      password.type = this.checked ? "text" : "password";
    });

    function setProgress(pct) {
      if (pct === null || pct === undefined || pct === "") {
        bar.hidden = false;
        bar.className = "bar unknown";
        barFill.style.width = "40%";
        return;
      }
      var n = Number(pct);
      if (isNaN(n)) {
        bar.hidden = true;
        return;
      }
      bar.hidden = false;
      bar.className = "bar";
      if (n < 0) n = 0;
      if (n > 100) n = 100;
      barFill.style.width = n + "%";
    }

    function showStatus(text, kind) {
      status.className = "status" + (kind ? " " + kind : "");
      status.textContent = text || "";
    }

    function setBusy(busy) {
      button.disabled = busy;
      button.textContent = busy ? "請稍候" : "下載";
    }

    function stopPoll() {
      polling = false;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
    }

    function authMessage(res, data) {
      if (res.status === 401 || (data && data.error === "unauthorized")) {
        return "密碼不對，請再試一次。";
      }
      if (res.status === 403 || (data && data.error === "disabled")) {
        return "下載功能尚未開啟。";
      }
      if (data && data.error && data.error !== "unauthorized" && data.error !== "disabled") {
        return data.error;
      }
      return "下載失敗，請稍後再試。";
    }

    function readJson(res) {
      return res.json().catch(function () { return null; });
    }

    function fail(text) {
      showStatus(text, "error");
      setBusy(false);
      bar.hidden = true;
      stopPoll();
    }

    function poll(jobId, token, started) {
      if (!polling) return;
      fetch("/dl/status", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId, password: password.value })
      }).then(function (res) {
        return readJson(res).then(function (data) {
          return { res: res, data: data };
        });
      }).then(function (result) {
        if (!polling) return;
        var res = result.res;
        var data = result.data;
        if (!res.ok) {
          if (res.status === 401 || res.status === 403) {
            fail(authMessage(res, data));
          } else if (data && data.error) {
            fail(data.error);
          } else {
            fail("這次下載已經過期，請再按一次下載。");
          }
          return;
        }
        if (!data || data.ok === false || data.status === "error") {
          fail((data && (data.error || data.message)) || "下載失敗，請稍後再試。");
          return;
        }
        if (data.status === "ready") {
          showStatus("下載完成。正在存到手機。如果沒有開始，請按綠色按鈕。", "ok");
          setProgress(100);
          var filename = data.filename || "影片.mp4";
          var href = "/dl/file/" + encodeURIComponent(jobId) + "?token=" + encodeURIComponent(token);
          save.hidden = false;
          save.href = href;
          save.setAttribute("download", filename);
          setBusy(false);
          stopPoll();
          var anchor = document.createElement("a");
          anchor.href = href;
          anchor.setAttribute("download", filename);
          anchor.rel = "noopener";
          document.body.appendChild(anchor);
          anchor.click();
          anchor.remove();
          return;
        }
        showStatus(data.message || "正在下載，請稍候…", "");
        if (data.progress === null || data.progress === undefined) {
          setProgress(null);
        } else {
          setProgress(data.progress);
        }
        if (Date.now() - started > 5 * 60 * 1000) {
          fail("等太久了，請改下載較短的影片。");
          return;
        }
        timer = setTimeout(function () {
          poll(jobId, token, started);
        }, 1500);
      }).catch(function () {
        fail("網路不通，請檢查連線後再按一次。");
      });
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      stopPoll();
      save.hidden = true;
      save.removeAttribute("href");
      remember();
      var url = (link.value || "").trim();
      if (!url) {
        showStatus("請貼上影片連結。", "error");
        bar.hidden = true;
        return;
      }
      if (!password.value) {
        showStatus("請輸入密碼。", "error");
        bar.hidden = true;
        return;
      }
      setBusy(true);
      showStatus("正在下載，請稍候…", "");
      setProgress(null);
      fetch("/dl/start", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url, password: password.value })
      }).then(function (res) {
        return readJson(res).then(function (data) {
          return { res: res, data: data };
        });
      }).then(function (result) {
        var res = result.res;
        var data = result.data;
        if (!res.ok || !data || !data.ok) {
          fail(authMessage(res, data));
          return;
        }
        polling = true;
        poll(data.job_id, data.token, Date.now());
      }).catch(function () {
        fail("網路不通，請檢查連線後再按一次。");
      });
    });
  })();
  </script>
</body>
</html>
"""


def register(app, ffmpeg_getter, password_configured, password_matches):

    global _ffmpeg_getter, _password_configured, _password_matches

    _ffmpeg_getter = ffmpeg_getter
    _password_configured = password_configured
    _password_matches = password_matches

    @app.get("/dl")
    def download_page():

        response = make_response(_page_html(), 200)
        response.headers["Content-Type"] = "text/html; charset=utf-8"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "script-src 'unsafe-inline'; "
            "style-src 'unsafe-inline'; "
            "connect-src 'self'; "
            "form-action 'self'; "
            "base-uri 'none'; "
            "frame-ancestors 'none'"
        )
        return _noindex(response)

    @app.post("/dl/start")
    def download_start():

        data = request.get_json(silent=True)
        password = ""

        if isinstance(data, dict):
            password = data.get("password", "")

        denied = _authorize(password)

        if denied is not None:
            return denied

        if not isinstance(data, dict):
            return _json({"ok": False, "error": MSG_GENERIC}, 400)

        try:
            url = validate_url(data.get("url", ""))
            job_id, token = _begin_job(url)
        except DlError as exc:
            return _json({"ok": False, "error": str(exc)}, exc.status)

        return _json(
            {"ok": True, "job_id": job_id, "token": token},
            200,
        )

    @app.post("/dl/status")
    def download_status():

        data = request.get_json(silent=True)
        password = ""

        if isinstance(data, dict):
            password = data.get("password", "")

        denied = _authorize(password)

        if denied is not None:
            return denied

        if not isinstance(data, dict):
            return _json({"ok": False, "error": MSG_EXPIRED}, 400)

        job_id = data.get("job_id", "")

        if not isinstance(job_id, str) or not job_id:
            return _json({"ok": False, "error": MSG_EXPIRED}, 404)

        view = _job_view(job_id)

        if view is None:
            return _json({"ok": False, "error": MSG_EXPIRED}, 404)

        if view["status"] == "error":
            return _json(
                {
                    "ok": False,
                    "status": "error",
                    "error": view["error"] or MSG_GENERIC,
                },
                200,
            )

        return _json(
            {
                "ok": True,
                "status": view["status"],
                "progress": view["progress"],
                "message": view["message"],
                "filename": view["filename"],
            },
            200,
        )

    @app.get("/dl/file/<job_id>")
    def download_file(job_id):

        found = _job_file(job_id, request.args.get("token", ""))

        if found is None:
            return _json({"ok": False, "error": MSG_EXPIRED}, 404)

        if found is False:
            return _json({"error": "unauthorized"}, 401)

        if found == "not-ready":
            return _json({"ok": False, "error": MSG_NOT_READY}, 409)

        path, filename = found
        response = send_file(
            path,
            mimetype="video/mp4",
            as_attachment=True,
            download_name=filename,
            conditional=True,
            max_age=0,
        )
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"

        # Quote again so a non-ASCII title is present even if the
        # framework only emits an ASCII fallback.
        encoded = quote(filename, safe="")
        disposition = response.headers.get("Content-Disposition", "")

        if "filename*=" not in disposition.lower():
            response.headers["Content-Disposition"] = (
                f"attachment; filename=\"video.mp4\"; filename*=UTF-8''{encoded}"
            )

        return response

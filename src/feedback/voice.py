"""Voice feedback, transcribed: a parent may say it instead of typing it.

The host downloads the voice message (Telegram: the bot's getFile; VK: the audio link the
Worker passes on), sends it to Whisper on Cloudflare Workers AI and keeps ONLY the text,
stored as "[голосовое] <text>" in the feedback table. The audio lives in memory for the
call and is never written anywhere. From there it is ordinary written feedback: the
insights model reads it as usual.

Anything that goes wrong (no token, a long message, a download or model error, an empty
transcript) leaves today's behaviour: the "[голосовое]" label, and the family is asked
once for a few words. Off unless GAME_VOICE=1 and CF_AI_TOKEN is set (a Cloudflare token
with Workers AI permission; CF_ACCOUNT_ID names the account).
"""

import base64
import json
import os
import urllib.parse
import urllib.request

LABEL = "[голосовое]"
MAX_SECONDS = 120
MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 1500
DEFAULT_MODEL = "@cf/openai/whisper-large-v3-turbo"
# Whisper was trained on subtitles: on silence or noise it "hears" their credits
# (production check 2026-11-15: one second of silence became «Продолжение следует...»).
# A transcript that is nothing but such a phrase is no transcript at all.
HALLUCINATIONS = (
    "продолжение следует", "субтитры", "спасибо за просмотр", "подписывайтесь на канал",
    "редактор субтитров", "корректор", "dimatorzok", "amara.org", "игорь негода",
)
# VK serves message audio from its own CDN; never fetch anything else on a sender's word.
# Production check 2026-11-15: VK served a voice message from psv4.vkuserphoto.ru.
VK_AUDIO_HOSTS = (".userapi.com", ".vk.com", ".vkuseraudio.net", ".vkuseraudio.ru",
                  ".vkuserphoto.ru", ".vk-cdn.net")


def enabled():
    return (os.environ.get("GAME_VOICE") == "1" and bool(os.environ.get("CF_AI_TOKEN"))
            and bool(os.environ.get("CF_ACCOUNT_ID")))


def _get(url, timeout=30):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("audio too large")
    return data


def transcribe(audio, post=None):
    """The text Whisper hears in `audio` (bytes), or '' when there is none."""
    url = (f"https://api.cloudflare.com/client/v4/accounts/{os.environ['CF_ACCOUNT_ID']}"
           f"/ai/run/{os.environ.get('VOICE_MODEL', DEFAULT_MODEL)}")
    body = json.dumps({"audio": base64.b64encode(audio).decode("ascii"), "language": "ru",
                       "task": "transcribe"}).encode("utf-8")
    if post is None:
        request = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {os.environ['CF_AI_TOKEN']}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    else:
        data = post(url, body)
    if not data.get("success", True):
        raise RuntimeError(f"Workers AI error: {json.dumps(data.get('errors'))[:200]}")
    text = " ".join(((data.get("result") or {}).get("text") or "").split())[:MAX_CHARS]
    return "" if hallucination(text) else text


def hallucination(text):
    """True when the transcript is only a subtitle credit Whisper makes up on silence."""
    plain = "".join(ch for ch in text.casefold() if ch.isalnum() or ch.isspace() or ch == ".").strip(" .")
    if not plain:
        return True
    return len(plain) <= 80 and any(phrase in plain for phrase in HALLUCINATIONS)


def labelled(transcript):
    return f"{LABEL} {transcript}" if transcript else LABEL


def _safely(fetch, transcribe_fn):
    try:
        heard = transcribe_fn(fetch())
        print(f"voice transcribed: {len(heard)} chars")
        return labelled(heard)
    except Exception as exc:  # noqa: BLE001 - a voice message must never break feedback
        print(f"voice transcription skipped: {type(exc).__name__}: {str(exc)[:200]}")
        return LABEL


def from_telegram(message, token, get=_get, transcribe_fn=transcribe):
    """'[голосовое] <text>' for a Telegram voice message, or the bare label."""
    voice = message.get("voice") or message.get("audio") or {}
    if not enabled() or not voice.get("file_id") or (voice.get("duration") or 0) > MAX_SECONDS:
        return LABEL

    def fetch():
        query = urllib.parse.urlencode({"file_id": voice["file_id"]})
        info = json.loads(get(f"https://api.telegram.org/bot{token}/getFile?{query}").decode("utf-8"))
        return get(f"https://api.telegram.org/file/bot{token}/{info['result']['file_path']}")

    return _safely(fetch, transcribe_fn)


def vk_url_allowed(url):
    parsed = urllib.parse.urlparse(url or "")
    host = parsed.hostname or ""
    return parsed.scheme == "https" and any(host.endswith(suffix) for suffix in VK_AUDIO_HOSTS)


def from_vk(url, seconds, get=_get, transcribe_fn=transcribe):
    """'[голосовое] <text>' for a VK audio message link, or the bare label."""
    if not enabled() or not vk_url_allowed(url) or (seconds or 0) > MAX_SECONDS:
        print(f"voice skipped: enabled={enabled()} host={urllib.parse.urlparse(url or '').hostname} "
              f"seconds={seconds}")
        return LABEL
    return _safely(lambda: get(url), transcribe_fn)

"""A picture above the game text: one illustration per skill (assets/cards).

The card is the game text plus a picture, sent as ONE message, so a delivery stays one
send with one identity:

- Telegram: the text with a large link preview of the picture shown above it. A photo
  caption is capped at 1024 characters and games are longer, so the picture comes as the
  preview of its public URL (the repository is public; CARD_BASE_URL overrides it).
- VK: the text with the picture attached, uploaded for that dialog right before the send.

A picture is decoration, never a reason to miss a day: no file for the skill, a failed
upload or GAME_CARDS unset all fall back to the plain text message. Off unless
GAME_CARDS=1.
"""

import json
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[2] / "assets" / "cards"
DEFAULT_BASE_URL = "https://raw.githubusercontent.com/LukshinVI/game-of-the-day/main/assets/cards"
VK_API_VERSION = "5.199"


def enabled():
    return os.environ.get("GAME_CARDS") == "1"


def picture(skill, occasion=None):
    """The local file for the card, or None when there is none (or cards are off).

    A day with an occasion (a birthday, a holiday) shows its greeting picture when there
    is one; otherwise, and on ordinary days, the picture of the game's skill.
    """
    if not enabled():
        return None
    candidates = ([f"greet_{occasion}.jpg"] if occasion else []) + ([f"skill_{skill}.jpg"] if skill else [])
    for name in candidates:
        path = ASSETS / name
        if path.is_file():
            return path
    return None


def telegram_preview(skill, occasion=None):
    """link_preview_options for sendMessage: the picture above the text, or no preview."""
    path = picture(skill, occasion)
    if path is None:
        return {"is_disabled": True}
    base = os.environ.get("CARD_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    return {"url": f"{base}/{path.name}", "prefer_large_media": True, "show_above_text": True}


def _vk(method, token, **params):
    params.update(access_token=token, v=VK_API_VERSION)
    request = urllib.request.Request(
        f"https://api.vk.com/method/{method}",
        data=urllib.parse.urlencode(params).encode("utf-8"),
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        data = json.loads(response.read().decode("utf-8"))
    if "error" in data:
        raise RuntimeError(f"VK {method} error {data['error'].get('error_code')}: {data['error'].get('error_msg')}")
    return data["response"]


def _upload(url, path):
    boundary = uuid.uuid4().hex
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"photo\"; filename=\"{path.name}\"\r\n"
        "Content-Type: image/jpeg\r\n\r\n"
    ).encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": f"multipart/form-data; boundary={boundary}"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def vk_attachment(skill, peer_id, token, vk=_vk, upload=_upload, occasion=None):
    """'photo<owner>_<id>[_<key>]' for this dialog, or None (no picture or the upload failed)."""
    path = picture(skill, occasion)
    if path is None or not peer_id or not token:
        return None
    try:
        server = vk("photos.getMessagesUploadServer", token, peer_id=peer_id)
        uploaded = upload(server["upload_url"], path)
        saved = vk("photos.saveMessagesPhoto", token, photo=uploaded["photo"],
                   server=uploaded["server"], hash=uploaded["hash"])[0]
    except Exception as exc:  # noqa: BLE001 - a picture must never block the game
        print(f"card upload skipped: {str(exc)[:200]}")
        return None
    key = f"_{saved['access_key']}" if saved.get("access_key") else ""
    return f"photo{saved['owner_id']}_{saved['id']}{key}"

"""
appearance_media.py — soundtracks and video openings you add in Settings › Customize Appearance.

Chimera keeps its own copy of each file in ~/Chimera/data/looks (never in the project folder, so nothing you add
ends up in the repo). The list (name, file, kind, how a video hands over to the splash) is stored in the
"look_custom" setting. The screen plays a file straight from disk when it can, and otherwise reads it in pieces
(media_chunk) and plays it from memory.
"""
import base64
import json
import os
import pathlib
import shutil
import time
import uuid

import automation_store as AS
import db

TYPES = {
    "sound": {".mp3": "audio/mpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac"},
    "video": {".mp4": "video/mp4", ".webm": "video/webm", ".m4v": "video/mp4"},
}
MAX_BYTES = {"sound": 25 * 1024 * 1024, "video": 200 * 1024 * 1024}
TRANSITIONS = ("displacement", "glitch", "fade", "cut")
CHUNK = 1_500_000                 # bytes per piece when the screen reads a file through Chimera


def media_dir():
    d = os.path.join(db.APP_DATA_DIR, "looks")
    os.makedirs(d, exist_ok=True)
    return d


def items():
    """What you added whose file is still there."""
    return [x for x in AS.look_custom() if os.path.isfile(os.path.join(media_dir(), x.get("file", "")))]


def _store(lst):
    AS.save_automation_settings({"look_custom": json.dumps(lst)})


def _find(mid):
    return next((x for x in AS.look_custom() if x.get("id") == mid), None)


def file_types(kind):
    exts = ";".join("*" + e for e in TYPES[kind])
    label = "Sound files" if kind == "sound" else "Video files"
    return (f"{label} ({exts})",)


def add(kind, path):
    """Copies a sound or video file into Chimera and lists it. Returns (ok, message, item)."""
    if kind not in TYPES:
        return False, "Unknown kind.", None
    if not path or not os.path.isfile(path):
        return False, "That file can't be found.", None
    ext = os.path.splitext(path)[1].lower()
    if ext not in TYPES[kind]:
        return False, f"Chimera can play {', '.join(TYPES[kind])} files here — not {ext or 'this type'}.", None
    size = os.path.getsize(path)
    if size == 0:
        return False, "That file is empty.", None
    if size > MAX_BYTES[kind]:
        return False, f"That file is {size / 1048576:.0f} MB — the limit is {MAX_BYTES[kind] // 1048576} MB.", None
    mid = ("s_" if kind == "sound" else "v_") + uuid.uuid4().hex[:8]
    dest = mid + ext
    shutil.copyfile(path, os.path.join(media_dir(), dest))
    name = " ".join(pathlib.Path(path).stem.replace("_", " ").split())[:40] or ("My soundtrack" if kind == "sound" else "My video")
    item = {"id": mid, "kind": kind, "name": name, "file": dest, "size": size, "added": time.strftime("%Y-%m-%d %H:%M")}
    if kind == "video":
        item["transition"] = "displacement"
    _store(AS.look_custom() + [item])
    return True, f"Added “{name}”.", item


def update(mid, name=None, transition=None):
    lst = AS.look_custom()
    item = next((x for x in lst if x.get("id") == mid), None)
    if not item:
        return False, "That one isn't in the list any more."
    if name is not None:
        name = " ".join(str(name).split())[:40]
        if not name:
            return False, "Give it a name."
        item["name"] = name
    if transition is not None:
        if item.get("kind") != "video" or transition not in TRANSITIONS:
            return False, "Choose displacement, glitch, fade or cut."
        item["transition"] = transition
    _store(lst)
    return True, "Saved."


def delete(mid):
    """Removes it from the list and deletes Chimera's copy. If it was chosen, the default comes back."""
    lst = AS.look_custom()
    item = next((x for x in lst if x.get("id") == mid), None)
    if not item:
        return False, "That one isn't in the list any more."
    _store([x for x in lst if x.get("id") != mid])
    try:
        os.remove(os.path.join(media_dir(), item.get("file", "")))
    except OSError:
        pass
    s = AS.get_automation_settings()
    reset = {}
    if s.get("look_sound") == mid:
        reset["look_sound"] = AS.DEFAULT_AUTOMATION["look_sound"]
    if s.get("look_splash") == mid:
        reset["look_splash"] = AS.DEFAULT_AUTOMATION["look_splash"]
    if reset:
        AS.save_automation_settings(reset)
    return True, f"Removed “{item.get('name', '')}”."


def _path(mid):
    item = _find(mid)
    if not item:
        return None, None
    p = os.path.join(media_dir(), os.path.basename(item.get("file", "")))
    return (p, item) if os.path.isfile(p) else (None, None)


def media_info(mid):
    """Where the screen can play it from: a file:// address, plus how many pieces to read if that doesn't work."""
    p, item = _path(mid)
    if not p:
        return {"ok": False, "message": "That file is missing."}
    size = os.path.getsize(p)
    ext = os.path.splitext(p)[1].lower()
    return {"ok": True, "url": pathlib.Path(p).as_uri(), "mime": TYPES[item["kind"]].get(ext, "application/octet-stream"),
            "size": size, "chunks": max(1, -(-size // CHUNK))}


def media_chunk(mid, index):
    p, _ = _path(mid)
    if not p:
        return {"ok": False, "message": "That file is missing."}
    with open(p, "rb") as f:
        f.seek(int(index) * CHUNK)
        data = f.read(CHUNK)
    return {"ok": True, "data": base64.b64encode(data).decode("ascii")}

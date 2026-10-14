"""Look at each photo with Claude's vision and pick the album it belongs in."""

import base64
import io
import json
import os
import shutil
import subprocess
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
from PIL import Image, ImageOps

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm", ".mts"}
MAX_SIDE = 1024  # long edge sent to the model; plenty to recognise a scene

PROMPT = """You are organising a personal photo library into albums.
Look at the photo and choose the ONE album it fits best.

Albums:
{albums}

Also write a short, specific caption (max 15 words) that would help someone
search for this photo later, e.g. "Birthday cake with candles at family dinner".
{hint}"""


def load_albums(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _video_frame(path):
    """Grab one frame ~1s in, so videos can be classified like photos."""
    if not shutil.which("ffmpeg"):
        return None
    fd, out = tempfile.mkstemp(suffix=".jpg")
    os.close(fd)
    for seek in ("1", "0"):
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", seek, "-i", path,
                            "-frames:v", "1", out], capture_output=True)
        if r.returncode == 0 and os.path.getsize(out) > 0:
            return out
    os.remove(out)
    return None


def _encode(path):
    """Return base64 JPEG (EXIF-rotated, downscaled) or None if unreadable."""
    frame = None
    if os.path.splitext(path)[1].lower() in VIDEO_EXT:
        frame = _video_frame(path)
        if not frame:
            return None
        path = frame
    try:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im).convert("RGB")
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
        return base64.standard_b64encode(buf.getvalue()).decode()
    except Exception:
        return None
    finally:
        if frame:
            os.remove(frame)


class Classifier:
    def __init__(self, albums, model, cache_path, workers=8):
        self.albums = albums
        self.names = list(albums)
        self.model = model
        self.cache_path = cache_path
        self.workers = workers
        self.client = anthropic.Anthropic()
        self.lock = threading.Lock()
        self.cache = {}
        if os.path.exists(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                self.cache = json.load(f)
        self.album_text = "\n".join(f"- {n}: {d}" for n, d in albums.items())
        self.schema = {
            "type": "object",
            "properties": {
                "album": {"type": "string", "enum": self.names},
                "caption": {"type": "string"},
            },
            "required": ["album", "caption"],
            "additionalProperties": False,
        }

    def _save(self):
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=1)
        os.replace(tmp, self.cache_path)

    def _ask(self, item):
        data = _encode(item.path)
        if data is None:
            return {"album": None, "caption": "", "error": "could not read image"}
        hint = ""
        if os.path.splitext(item.name)[1].lower() in VIDEO_EXT:
            hint = "\nThis is a single frame from a video."
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=1024,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": self.schema}},
            messages=[{"role": "user", "content": [
                {"type": "image",
                 "source": {"type": "base64", "media_type": "image/jpeg", "data": data}},
                {"type": "text", "text": PROMPT.format(albums=self.album_text, hint=hint)},
            ]}],
        )
        if response.stop_reason == "refusal":
            return {"album": None, "caption": "", "error": "model declined"}
        text = "".join(b.text for b in response.content if b.type == "text")
        result = json.loads(text)
        if result.get("album") not in self.albums:
            return {"album": None, "caption": "", "error": "unexpected album"}
        return {"album": result["album"], "caption": result.get("caption", "")[:200]}

    def classify(self, items, progress=print):
        """Classify items (each has .sha, .path, .name). Results cached by file hash,
        so re-running after an interruption only processes what's left."""
        todo = [it for it in items if it.sha not in self.cache]
        progress(f"{len(items) - len(todo)} already classified, {len(todo)} to go")
        done = 0
        with ThreadPoolExecutor(self.workers) as pool:
            futures = {pool.submit(self._ask, it): it for it in todo}
            for fut in as_completed(futures):
                it = futures[fut]
                try:
                    result = fut.result()
                except anthropic.AuthenticationError:
                    pool.shutdown(cancel_futures=True)
                    raise
                except anthropic.APIError as e:  # rate limits etc. -- retry on next run
                    progress(f"  skipped {it.name}: {e}")
                    continue
                with self.lock:
                    self.cache[it.sha] = result
                    done += 1
                    if done % 25 == 0:
                        self._save()
                        progress(f"  {done}/{len(todo)} classified")
        self._save()
        return {it.sha: self.cache.get(it.sha) for it in items}

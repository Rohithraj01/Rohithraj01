#!/usr/bin/env python3
"""Find duplicate photos and videos in Google Photos Takeout exports.

Usage:
    python find_duplicates.py ACCOUNT_A [ACCOUNT_B] [--out report]

ACCOUNT_A  extracted Takeout folder of the account you keep (the main account)
ACCOUNT_B  extracted Takeout folder of a second account (optional)

It looks at the pictures themselves, not only at file names and dates, so it also
finds copies that Google re-compressed ("Storage saver"), HEIC photos saved again
as JPG, WhatsApp copies with another name and date, photos whose JSON file is in
another zip of the export, and photos whose date is off by a time zone. It finds
duplicates between the two accounts and inside each account.

Every duplicate gets one of three labels:

  identical   the very same file
  same        the same photo saved again: smaller, re-compressed, other format or
              other name. Compared pixel by pixel, so it is safe to delete
              without looking.
  check       very probably the same photo, but edited, turned, or the copy that
              would be deleted is the bigger one. The report shows the two side
              by side; tick it if you agree.

Burst shots and two phones photographing the same moment look alike but are
different photos. They are not reported.

Writes report.html (thumbnails side by side, tick boxes, a link into Google Photos
for every copy, and a button to download the ticked ones as a list) and
report.csv. Needs: pip install pillow pillow-heif numpy
"""

import argparse
import csv
import hashlib
import html
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.parse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import numpy as np
    from PIL import Image, ImageFilter, ImageOps
    Image.MAX_IMAGE_PIXELS = None  # big panoramas are photos, not decompression bombs
except ImportError:
    np = Image = ImageFilter = ImageOps = None
try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF = True
except ImportError:
    HEIF = False

IMAGE_EXT = {".jpg", ".jpeg", ".jpe", ".png", ".gif", ".heic", ".heif", ".avif", ".webp",
             ".bmp", ".tif", ".tiff"}
RAW_EXT = {".dng", ".raw", ".cr2", ".cr3", ".nef", ".arw", ".orf", ".rw2", ".raf"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm", ".mts", ".m2ts",
             ".wmv", ".mpg", ".mpeg"}
MEDIA_EXT = IMAGE_EXT | RAW_EXT | VIDEO_EXT

DUP_SUFFIX = re.compile(r"^(.*?)\s*(\(\d+\))$")  # "IMG_1234(1)" -> ("IMG_1234", "(1)")
EDITED = re.compile(r"[-_ ](edited|bearbeitet|modifié|modificato|editado|bewerkt|redigerad|"
                    r"muokattu|edytowane|upravené|szerkesztett)$", re.I)
MESSAGING = re.compile(r"-wa\d{3,}$|^(received|signal|telegram)", re.I)
# File names cameras give: two different names of the same kind are two different shots,
# however alike they look (IMG_1234 and IMG_1235 are a burst, not a copy).
CAMERA_NAMES = (
    re.compile(r"^(?:img|dsc|dscn|dscf|imgp|pict|sam|gopr|dji|cimg|hpim|imag|p)_?(\d{3,6})$"),
    re.compile(r"^(?:img|vid|pxl|mvimg|photo|burst)?_?(\d{8}_\d{6,9}(?:_\d{1,3})?)"
               r"(?:\.mp|\.night|\.portrait|_hdr)?$"),
)

VERSION = 1           # bump when fingerprints change, so cached ones are not reused
THUMB = 32            # side of the tiny picture every photo is compared by
FINE = 384            # long side of the picture used for the pixel check
HASH_BITS = 12        # pictures whose hashes differ in more bits are not compared
LOOK_CORR = 0.80      # tiny pictures this alike get a pixel check
# Pixel check, after evening out brightness and contrast. The same photo saved again
# (smaller, re-compressed, HEIC/JPG) differs a little everywhere: at most ~0.55 standard
# deviations in its most different 0.5% of pixels ("diff"), and ~0.8 in its most
# different spot ("spot"). A burst shot, or a screenshot one message or one switch
# further on, differs strongly in one place: spot 1.5 or more.
SAME_CORR = 0.97
SAME_DIFF = 0.85
SAME_SPOT = 1.15
CHECK_DIFF = 1.10     # up to these: shown for a quick look
CHECK_SPOT = 1.40
STRICT_DIFF = 0.15    # taken seconds apart: only this close counts as the same photo
STRICT_SPOT = 0.30
FLAT = 4.0            # contrast (grey levels) below which a picture is nearly blank
TZ_SPAN = 14 * 3600 + 1800
LEVELS = {"identical": 0, "same": 1, "check": 2}

# Videos are matched by sampling a handful of frames and comparing them. A re-encoded or
# re-shared copy keeps almost the same length and every sampled frame matches closely
# (worst frame >= 0.95); two different clips of the same length score well under 0.90 even
# when the scene is similar, so the gap is clear.
VFRAMES = 7           # frames sampled across a video (fewer for a very short one)
VSIDE = 48            # side of each frame's small grey thumbnail, compared frame by frame
VSAME_MIN = 0.95      # same video: worst sampled frame at least this alike ...
VSAME_MED = 0.975     # ... and the middle frame at least this
VCHECK_MIN = 0.90     # probably the same (shown for a look): worst frame at least this ...
VCHECK_MED = 0.95     # ... and the middle frame at least this


def name_key(title):
    """'IMG_1234(1)-edited.HEIC' -> 'img_1234': copies keep the name but not the extension."""
    stem = EDITED.sub("", os.path.splitext(os.path.basename(title))[0].strip().lower())
    m = DUP_SUFFIX.match(stem)  # both "IMG(1)-edited" and "IMG-edited(1)" occur
    return EDITED.sub("", m.group(1) if m else stem)


def camera_id(title):
    key = name_key(title)
    for kind, rx in enumerate(CAMERA_NAMES):
        m = rx.match(key)
        if m:
            return kind, m.group(1)
    return None


def different_shots(t1, t2):
    a, b = camera_id(t1), camera_id(t2)
    return a is not None and b is not None and a[0] == b[0] and a[1] != b[1]


def time_relation(t1, t2):
    """'same' moment (also when the clocks differ by a time zone), 'near' or 'far'."""
    if t1 is None or t2 is None:
        return None
    d = abs(t1 - t2)
    if d == 0 or (d <= TZ_SPAN and d % 900 == 0):
        return "same"
    return "near" if d < 600 else "far"


class Item:
    __slots__ = ("path", "account", "name", "title", "folder", "size", "mtime", "sha", "taken",
                 "url", "video", "w", "h", "ph", "dh", "thumb", "contrast", "copies", "edited",
                 "dur", "vframes")

    def __init__(self, path, account, st):
        self.path = path
        self.account = account
        self.name = self.title = os.path.basename(path)
        self.folder = os.path.basename(os.path.dirname(path))
        self.size, self.mtime = st.st_size, st.st_mtime_ns
        self.sha = self.taken = self.url = self.edited = None
        self.video = os.path.splitext(path)[1].lower() in VIDEO_EXT
        self.w = self.h = self.ph = self.dh = self.thumb = None
        self.contrast = 0.0
        self.copies = 1
        self.dur = None       # video length in seconds
        self.vframes = None   # list of small grey frames (numpy uint8), for video compare

    @property
    def pixels(self):
        return (self.w or 0) * (self.h or 0)


class Sidecars:
    """Takeout's JSON files, found by folder name and the photo's original file name.

    Their file names are messy (".json", ".supplemental-metadata.json", cut short,
    "(1)" moved after the extension), so the "title" inside is used. A big export
    comes as several zips: extracted side by side, a photo's JSON can sit in another
    zip's copy of its folder, so folders are matched by name, not by full path.
    """

    def __init__(self, root):
        self.by_key = defaultdict(list)    # (folder name, title) -> [(n, meta)]
        self.by_title = defaultdict(list)  # title -> [(n, meta)]
        self.titles = defaultdict(list)    # folder name -> titles, for cut-short names
        for dirpath, _, files in os.walk(root):
            folder = os.path.basename(dirpath)
            for fn in files:
                if not fn.lower().endswith(".json"):
                    continue
                try:
                    with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                if not isinstance(data, dict) or not isinstance(data.get("title"), str) or (
                        "photoTakenTime" not in data and "url" not in data):
                    continue  # album metadata.json and the like
                ts = str((data.get("photoTakenTime") or {}).get("timestamp", ""))
                meta = (int(ts) if ts.isdigit() and int(ts) > 0 else None,
                        data.get("url") or None, data["title"])
                m = re.search(r"\((\d+)\)\.json$", fn)
                entry = (m.group(1) if m else "", meta)
                if (folder, data["title"]) not in self.by_key:
                    self.titles[folder].append(data["title"])
                self.by_key[(folder, data["title"])].append(entry)
                self.by_title[data["title"]].append(entry)

    @staticmethod
    def _pick(entries, n):
        if not entries:
            return None
        for num, meta in entries:
            if num == n:
                return meta
        metas = {meta for _, meta in entries}
        return metas.pop() if len(metas) == 1 else None

    def find(self, folder, name):
        stem, ext = os.path.splitext(name)
        plain, n = EDITED.sub("", stem), ""  # "IMG-edited.jpg" shares the original's JSON
        m = DUP_SUFFIX.match(plain)
        if m:  # "IMG(1).jpg" -> title "IMG.jpg", sidecar "IMG.jpg(1).json"
            plain, n = EDITED.sub("", m.group(1)), m.group(2)[1:-1]
        titles = dict.fromkeys((plain + ext, name, stem + ext))
        for title in titles:
            meta = self._pick(self.by_key.get((folder, title)), n)
            if meta:
                return meta
        if len(name) >= 40:  # Takeout cuts long file names short; the title has them in full
            hits = [t for t in self.titles.get(folder, ()) if t.startswith(plain) and t != plain]
            if len(hits) == 1:
                return self._pick(self.by_key[(folder, hits[0])], n)
        for title in titles:  # JSON in an album folder only: same photo, same data
            meta = self._pick(self.by_title.get(title), n)
            if meta:
                return meta
        return None


def scan(root, account="A"):
    """Every photo and video under root, with the date and link from its JSON file."""
    if not os.path.isdir(root):
        sys.exit(f"Folder not found: {root}")
    sidecars = Sidecars(root)
    items = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        folder = os.path.basename(dirpath)
        for fn in sorted(filenames):
            if os.path.splitext(fn)[1].lower() not in MEDIA_EXT:
                continue
            path = os.path.join(dirpath, fn)
            try:
                st = os.stat(path)
            except OSError:
                continue
            if not st.st_size:
                continue
            it = Item(path, account, st)
            meta = sidecars.find(folder, fn)
            if meta:
                it.taken, it.url, it.title = meta
            items.append(it)
    # "IMG-edited.jpg" next to "IMG.jpg" is the same Google Photos item, not a duplicate
    def key(it, stem):
        return os.path.dirname(it.path), stem.lower(), it.video

    originals = {key(it, os.path.splitext(it.name)[0]): it for it in items}
    kept = []
    for it in items:
        stem = os.path.splitext(it.name)[0]
        m = DUP_SUFFIX.match(stem)
        if m and EDITED.search(m.group(1)):  # "IMG-edited(1)" belongs to "IMG(1)"
            original = EDITED.sub("", m.group(1)) + m.group(2)
        else:
            original = EDITED.sub("", stem)
        orig = originals.get(key(it, original)) if original != stem else None
        if orig is not None and orig is not it and orig.edited is None:
            orig.edited = it.path
        else:
            kept.append(it)
    return kept


# ---- fingerprints -------------------------------------------------------------------

def _dct8():
    k, x = np.arange(8)[:, None], np.arange(THUMB)[None, :]
    m = np.cos(np.pi * (2 * x + 1) * k / (2 * THUMB)) * np.sqrt(2 / THUMB)
    m[0] /= np.sqrt(2)
    return m


def _bits(flags):
    return int.from_bytes(np.packbits(np.asarray(flags, bool)).tobytes(), "big")


def _flatten(im):
    """Pictures with see-through parts are shown on white."""
    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, "white")
        bg.paste(im, mask=im.getchannel("A"))
        return bg
    return im


def _exif_time(exif):
    """Capture time from EXIF, in seconds. Without a time zone it is local time read as UTC,
    which is fine: dates are compared allowing for time zones."""
    try:
        sub = exif.get_ifd(0x8769)
        s = sub.get(36867) or sub.get(36868) or exif.get(306)
        dt = datetime.strptime(str(s).strip()[:19], "%Y:%m:%d %H:%M:%S")
        m = re.match(r"^([+-])(\d\d):?(\d\d)$", str(sub.get(36881) or sub.get(36880) or "").strip())
        if m:
            off = timedelta(hours=int(m.group(2)), minutes=int(m.group(3)))
            dt = dt - off if m.group(1) == "+" else dt + off
        return int(dt.replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return None


def _look(src):
    with Image.open(src) as im:
        exif = im.getexif()
        w, h = im.size
        if exif.get(0x0112, 1) in (5, 6, 7, 8):
            w, h = h, w
        im.draft("L", (256, 256))  # JPEG: decode at 1/2 to 1/8 size, much faster
        g = _flatten(ImageOps.exif_transpose(im)).convert("L")
    t = np.asarray(g.resize((THUMB, THUMB), Image.Resampling.BOX), dtype=np.float64)
    d = _dct8()
    c = (d @ t @ d.T).ravel()[1:]  # lowest frequencies, without the overall brightness
    s = np.asarray(g.resize((9, 8), Image.Resampling.BOX), dtype=np.int16)
    return {"w": w, "h": h, "ph": _bits(c > np.median(c)), "dh": _bits(s[:, 1:] > s[:, :-1]),
            "thumb": np.rint(t).astype(np.uint8).tobytes(), "exif": _exif_time(exif)}


def _probe_duration(src):
    """A video's length in seconds and its frame size, with ffprobe."""
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-of", "json",
                            "-show_entries", "format=duration:stream=duration,width,height", src],
                           capture_output=True, text=True, timeout=120)
        info = json.loads(r.stdout or "{}")
    except (OSError, ValueError, subprocess.SubprocessError):
        return None, None, None
    stream = (info.get("streams") or [{}])[0]
    dur = (info.get("format") or {}).get("duration") or stream.get("duration")
    try:
        dur = float(dur)
    except (TypeError, ValueError):
        dur = None
    return dur, (int(stream.get("width") or 0) or None), (int(stream.get("height") or 0) or None)


def _video_frame(src, t):
    """One frame at time t (seconds) as a small grey VSIDE x VSIDE thumbnail, or None."""
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", src, "-frames:v", "1",
                            "-vf", "scale=256:-2", "-f", "mjpeg", "-"], capture_output=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode or not r.stdout:
        return None
    try:
        with Image.open(io.BytesIO(r.stdout)) as im:
            g = _flatten(ImageOps.exif_transpose(im)).convert("L")
        return np.asarray(g.resize((VSIDE, VSIDE), Image.Resampling.BOX), np.uint8).tobytes()
    except Exception:
        return None


def _video_look(src):
    """Sample a few frames across a video so copies can be matched by what they show."""
    if not shutil.which("ffmpeg"):
        return {"vbad": 1}
    dur, w, h = _probe_duration(src)
    if not dur or dur <= 0:
        return {"vbad": 1}
    n = VFRAMES if dur >= 2 else 3
    frames = []
    for k in range(n):
        f = _video_frame(src, dur * (k + 0.5) / n)
        if f is None:
            return {"vbad": 1}
        frames.append(f.hex())
    return {"dur": round(dur, 3), "w": w, "h": h, "vframes": frames}


def fingerprint(path):
    """Read a file once: its SHA-256, and for pictures/videos what they look like."""
    ext = os.path.splitext(path)[1].lower()
    visual = Image is not None and ext in IMAGE_EXT
    h = hashlib.sha256()
    data = bytearray() if visual else None
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
                if data is not None:
                    data += chunk
                    if len(data) > (300 << 20):
                        data = None  # huge file: decode it from disk instead
    except OSError:
        return None
    out = {"sha": h.hexdigest()}
    if visual:
        try:
            out.update(_look(io.BytesIO(data) if data is not None else path))
        except Exception:
            out["bad"] = 1  # can't be opened: compared by name and date only
    elif Image is not None and ext in VIDEO_EXT:
        try:
            out.update(_video_look(path))
        except Exception:
            out["vbad"] = 1  # unreadable: compared by name and date only
    return out


class Cache:
    """Fingerprints from earlier runs, so a re-run doesn't read every file again."""

    def __init__(self, path):
        self.db = None
        if not path:
            return
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.db = sqlite3.connect(path)
            self.db.execute(f"CREATE TABLE IF NOT EXISTS fp{VERSION} (path TEXT PRIMARY KEY,"
                            " size INTEGER, mtime INTEGER, data TEXT)")
        except sqlite3.Error:
            self.db = None

    def get(self, it):
        if self.db is None:
            return None
        row = self.db.execute(f"SELECT data FROM fp{VERSION} WHERE path=? AND size=? AND mtime=?",
                              (os.path.abspath(it.path), it.size, it.mtime)).fetchone()
        return json.loads(row[0]) if row else None

    def put(self, pairs):
        if self.db is None or not pairs:
            return
        rows = []
        for it, fp in pairs:
            fp = dict(fp)
            if "thumb" in fp:
                fp["thumb"] = fp["thumb"].hex()
            rows.append((os.path.abspath(it.path), it.size, it.mtime, json.dumps(fp)))
        try:
            self.db.executemany(f"INSERT OR REPLACE INTO fp{VERSION} VALUES (?,?,?,?)", rows)
            self.db.commit()
        except sqlite3.Error:
            pass


def _apply(it, fp):
    it.sha = fp["sha"]
    if "ph" in fp:
        it.w, it.h, it.ph, it.dh = fp["w"], fp["h"], fp["ph"], fp["dh"]
        thumb = fp["thumb"]
        it.thumb = bytes.fromhex(thumb) if isinstance(thumb, str) else thumb
        it.contrast = float(np.frombuffer(it.thumb, np.uint8).std())
    if "vframes" in fp:
        it.dur = fp.get("dur")
        it.vframes = [np.frombuffer(bytes.fromhex(s), np.uint8).reshape(VSIDE, VSIDE)
                      for s in fp["vframes"]]
        if not it.w and fp.get("w"):
            it.w, it.h = fp["w"], fp["h"]
    if it.taken is None and fp.get("exif"):
        it.taken = fp["exif"]


def _needs_video(it, fp, have_ffmpeg):
    """A cached video from before this feature (no frames yet) is read again, once."""
    return have_ffmpeg and it.video and "vframes" not in fp and "vbad" not in fp


def default_workers():
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _map(fn, args, workers):
    """fn over args, in several processes when it's worth it (results in order)."""
    if workers > 1 and len(args) > 16:
        try:
            pool = ProcessPoolExecutor(min(workers, 61))
        except (OSError, NotImplementedError, ImportError):
            pool = None
        if pool is not None:
            with pool:
                yield from pool.map(fn, args, chunksize=4)
            return
    for a in args:
        yield fn(a)


def load_account(root, account, cache=None, workers=1, progress=print):
    """Scan one Takeout export and fingerprint every file in it."""
    progress(f"Scanning account {account}: {root}")
    items = scan(root, account)
    cache = cache or Cache(None)
    have_ffmpeg = shutil.which("ffmpeg") is not None
    todo = []
    for it in items:
        fp = cache.get(it)
        if fp and not _needs_video(it, fp, have_ffmpeg):
            _apply(it, fp)
        else:
            todo.append(it)
    if todo:
        progress(f"  reading {len(todo):,} files" + (
            f" ({len(items) - len(todo):,} remembered from the last run)" if len(todo) < len(items) else ""))
    batch, start = [], time.time()
    for k, (it, fp) in enumerate(zip(todo, _map(fingerprint, [it.path for it in todo], workers)), 1):
        if fp:
            _apply(it, fp)
            batch.append((it, fp))
        if k % 500 == 0:
            cache.put(batch)
            batch = []
            progress(f"  {k:,} / {len(todo):,} files read ({time.time() - start:.0f} s)")
    cache.put(batch)
    bad = [it for it in items if it.sha and it.thumb is None and not it.video
           and os.path.splitext(it.path)[1].lower() in IMAGE_EXT]
    heic = sum(os.path.splitext(it.path)[1].lower() in (".heic", ".heif") for it in bad)
    if heic and not HEIF:
        progress(f"  {heic} HEIC photos can't be opened: run  pip install pillow-heif  and try again")
    elif bad:
        progress(f"  {len(bad)} pictures couldn't be opened; they are compared by name and date only")
    videos = sum(it.video for it in items)
    if videos and not have_ffmpeg:
        progress(f"  {videos} videos: install ffmpeg to match re-encoded copies by content "
                 "(without it, videos match only by identical file or the same name and date)")
    return collapse([it for it in items if it.sha])


def collapse(items):
    """Takeout puts the same photo in 'Photos from YYYY' and in every album it is in.
    Those copies are one photo in Google Photos, so count them once."""
    year = re.compile(r"(19|20)\d\d")
    items = sorted(items, key=lambda it: (not year.search(it.folder), it.path))
    seen = {}
    for it in items:
        first = seen.setdefault(it.sha, it)
        if first is not it:
            first.copies += 1
            first.url = first.url or it.url
            first.taken = first.taken if first.taken is not None else it.taken
            first.edited = first.edited or it.edited
    return list(seen.values())


# ---- comparing ----------------------------------------------------------------------

def _popcount(x):
    if hasattr(np, "bitwise_count"):
        return np.bitwise_count(x)
    table = np.array([bin(i).count("1") for i in range(256)], np.uint8)
    return table[x.view(np.uint8)].reshape(x.shape + (8,)).sum(-1)


def candidate_pairs(items):
    """Pairs worth a closer look: alike pictures, same name and moment, same file."""
    pairs = set()
    pics = [i for i, it in enumerate(items) if it.ph is not None]
    if pics:
        ph = np.array([items[i].ph for i in pics], np.uint64)
        dh = np.array([items[i].dh for i in pics], np.uint64)
        n = len(pics)
        step = max(1, min(1024, 2_000_000 // n))
        for s in range(0, n, step):
            e = min(n, s + step)
            close = ((_popcount(ph[s:e, None] ^ ph[None, s:]) <= HASH_BITS)
                     | (_popcount(dh[s:e, None] ^ dh[None, s:]) <= HASH_BITS))
            for i, j in zip(*np.nonzero(close)):  # row i is picture s+i, column j is s+j
                gi, gj = int(i) + s, int(j) + s
                if gi < gj:
                    pairs.add((pics[gi], pics[gj]))
    buckets = defaultdict(list)
    for i, it in enumerate(items):
        buckets[("sha", it.sha)].append(i)
        if it.taken is not None:  # same name, same moment (time zones are multiples of 15 min)
            buckets[(name_key(it.title), it.taken % 900, it.video)].append(i)
    for idx in buckets.values():
        if 1 < len(idx) <= 50:
            for x in range(len(idx)):
                for y in range(x + 1, len(idx)):
                    pairs.add((idx[x], idx[y]))
    # videos: compare only ones of nearly the same length (a re-encode keeps the length)
    vids = sorted((i for i, it in enumerate(items) if it.video and it.vframes),
                  key=lambda i: items[i].dur or 0.0)
    for x in range(len(vids)):
        da = items[vids[x]].dur or 0.0
        for y in range(x + 1, len(vids)):
            db = items[vids[y]].dur or 0.0
            if db - da > max(1.0, 0.05 * da):  # sorted by length: stop once too far apart
                break
            i, j = sorted((vids[x], vids[y]))
            pairs.add((i, j))
    return pairs


def _z(thumb, rot=0):
    t = np.frombuffer(thumb, np.uint8).astype(np.float32).reshape(THUMB, THUMB)
    if rot:
        t = np.rot90(t, rot)
    t = t - t.mean()
    n = float(np.sqrt((t * t).sum()))
    return t / n if n else t


def _gray(path, rot=0):
    with Image.open(path) as im:
        im.draft("L", (FINE * 2, FINE * 2))
        g = _flatten(ImageOps.exif_transpose(im)).convert("L")
    return g.rotate(90 * rot, expand=True) if rot else g


def _spot(d, r=2):
    """Largest average of d over any (2r+1) x (2r+1) square."""
    k = 2 * r + 1
    c = np.pad(np.pad(d, r, mode="edge").cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return float(((c[k:, k:] - c[:-k, k:] - c[k:, :-k] + c[:-k, :-k]) / (k * k)).max())


def compare_pixels(job):
    """How alike two pictures are, pixel by pixel: (correlation, difference of the most
    different pixels, difference in the most different spot)."""
    pa, pb, rot = job
    try:
        a, b = _gray(pa), _gray(pb, rot)
    except Exception:
        return None
    w, h = a.size
    s = FINE / max(w, h)
    size = (max(16, round(w * s)), max(16, round(h * s)))

    def norm(g):  # same size, slightly blurred (hides re-compression), same brightness
        x = np.asarray(g.resize(size, Image.Resampling.BOX).filter(ImageFilter.BoxBlur(1)),
                       np.float32)
        return (x - x.mean()) / (x.std() + 1e-3)

    A, B = norm(a), norm(b)
    d = np.abs(A - B)
    return float((A * B).mean()), float(np.percentile(d, 99.5)), _spot(d)


def _aspect_ok(a, b, rot):
    if not (a.w and a.h and b.w and b.h):
        return False
    rb = b.h / b.w if rot % 2 else b.w / b.h
    return abs(a.w / a.h - rb) <= 0.03 * a.w / a.h


def _why(a, b, when):
    parts = []
    ea, eb = (os.path.splitext(x.name)[1].lstrip(".").upper() for x in (a, b))
    ea, eb = ("JPG" if e == "JPEG" else e for e in (ea, eb))
    if ea != eb:
        parts.append(f"{ea} and {eb}")
    if a.pixels and b.pixels and max(a.pixels, b.pixels) > 1.1 * min(a.pixels, b.pixels):
        parts.append("one is a smaller copy")
    elif max(a.size, b.size) > 1.05 * min(a.size, b.size):
        parts.append("re-compressed")
    if name_key(a.title) != name_key(b.title):
        parts.append("different names")
    if when == "same" and a.taken != b.taken:
        parts.append("dates differ by a time zone")
    return "same photo, " + (", ".join(parts) if parts else "saved again")


def decide(a, b, coarse, rot, px):
    """('same' | 'check', reason) for two different files, or None."""
    when = time_relation(a.taken, b.taken)
    named = name_key(a.title) == name_key(b.title) and when == "same"
    if px is None:
        return ("check", "same name and date, but one of them can't be opened") if named else None
    corr, diff, spot = px
    flat = min(a.contrast, b.contrast) < FLAT
    aspect = _aspect_ok(a, b, rot)
    if corr >= SAME_CORR and diff <= SAME_DIFF and spot <= SAME_SPOT and aspect:
        if rot:
            return "check", "same photo, but turned"
        if when == "near" and not named and not (diff <= STRICT_DIFF and spot <= STRICT_SPOT):
            return "check", "taken seconds apart: maybe two shots of the same scene"
        if flat and not named:
            return "check", "nearly blank pictures that look the same"
        return "same", _why(a, b, when)
    if named:
        return "check", "same name and date, but edited (colours or framing differ)"
    if corr >= SAME_CORR and diff <= CHECK_DIFF and spot <= CHECK_SPOT and aspect and not flat:
        return "check", "looks the same, with small differences"
    return None


def _kind(it):
    ext = os.path.splitext(it.path)[1].lower()
    return "video" if it.video else "raw" if ext in RAW_EXT else "picture"


def _vz(frame):
    t = frame.astype(np.float32)
    t -= t.mean()
    n = float(np.sqrt((t * t).sum()))
    return t / n if n else t


def compare_video(fa, fb):
    """(worst, middle) frame correlation between two videos' sampled frames, or None.
    Each frame is matched to the nearest of its neighbours, to allow a tiny offset."""
    if not fa or not fb:
        return None
    za, zb = [_vz(f) for f in fa], [_vz(f) for f in fb]
    best = []
    for i in range(len(za)):
        cs = [float((za[i] * zb[j]).sum()) for j in (i - 1, i, i + 1) if 0 <= j < len(zb)]
        if cs:
            best.append(max(cs))
    if not best:
        return None
    best.sort()
    return best[0], best[len(best) // 2]


def decide_video(a, b):
    """('same' | 'check', reason) for two different video files, or None."""
    if not (a.dur and b.dur):
        return None
    dd = abs(a.dur - b.dur)
    v = compare_video(a.vframes, b.vframes)
    if v is None:
        return None
    worst, mid = v
    ea, eb = (os.path.splitext(x.name)[1].lstrip(".").upper() for x in (a, b))
    note = f", {ea} and {eb}" if ea != eb else ""
    if dd <= max(0.5, 0.02 * min(a.dur, b.dur)) and worst >= VSAME_MIN and mid >= VSAME_MED:
        smaller = " (one is a smaller copy)" if a.pixels and b.pixels and max(
            a.pixels, b.pixels) > 1.2 * min(a.pixels, b.pixels) else ""
        return "same", f"same video, re-encoded or re-saved{note}{smaller}"
    if dd <= max(1.0, 0.05 * min(a.dur, b.dur)) and worst >= VCHECK_MIN and mid >= VCHECK_MED:
        return "check", f"looks like the same video{note}"
    return None


def compare(items, pairs, workers=1):
    """Judge each candidate pair: [(i, j, level, reason, score)]."""
    found, looks = [], []
    zs = {}
    for i, j in sorted(pairs):
        a, b = items[i], items[j]
        if a.sha == b.sha:
            found.append((i, j, "identical", "identical file", 2.0))
            continue
        if different_shots(a.title, b.title):  # IMG_1234 and IMG_1235 are two recordings
            continue
        named = name_key(a.title) == name_key(b.title) and time_relation(a.taken, b.taken) == "same"
        if a.video and b.video and a.vframes and b.vframes:  # match videos by their frames
            v = decide_video(a, b)
            if v:
                found.append((i, j, v[0], v[1], 1.5 if v[0] == "same" else 0.6))
            continue
        if a.thumb is None or b.thumb is None:
            # videos without frames, RAW files and pictures that can't be opened: name/date.
            # A RAW file and its JPG, or a Live Photo's picture and video, are kept apart.
            if named and _kind(a) == _kind(b):
                if a.thumb is None and b.thumb is None:
                    what = "video" if a.video else "file"
                    found.append((i, j, "same", f"same {what} name and date", 1.0))
                else:
                    found.append((i, j, "check", "same name and date, but one of them can't be "
                                  "opened to compare the pictures", 0.5))
            continue
        for x, it in ((i, a), (j, b)):
            if x not in zs:
                zs[x] = _z(it.thumb)
        best, rot = -1.0, 0
        for k in ((0, 1, 2, 3) if named else (0,)):  # same name and date: maybe turned
            c = float((zs[i] * (zs[j] if k == 0 else _z(b.thumb, k))).sum())
            if c > best:
                best, rot = c, k
        if best >= LOOK_CORR or named:
            looks.append((i, j, best, rot))
    jobs = [(items[i].path, items[j].path, rot) for i, j, _, rot in looks]
    for (i, j, coarse, rot), px in zip(looks, _map(compare_pixels, jobs, workers)):
        v = decide(items[i], items[j], coarse, rot, px)
        if v:
            found.append((i, j, v[0], v[1], px[0] if px else 0.0))
    return found


# ---- grouping -----------------------------------------------------------------------

def _keeper_rank(it):
    return (it.account != "A", bool(MESSAGING.search(name_key(it.title))), -it.pixels, -it.size,
            it.path)


class Result:
    """rows: dicts with group, section, action ('keep'/'delete'), level, reason, item."""

    def __init__(self, items, found):
        self.items = items
        n = len(items)
        parent = list(range(n))

        def root(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        edge = {}
        for i, j, level, reason, score in found:
            edge[(i, j)] = edge[(j, i)] = (level, reason)
            if level != "check":
                parent[root(i)] = root(j)
        members = defaultdict(list)
        for i in range(n):
            members[root(i)].append(i)

        self.rows, self.keeper = [], {}
        groups = sorted((m for m in members.values() if len(m) > 1),
                        key=lambda m: min(items[i].taken or 0 for i in m))
        going = set()
        for g, m in enumerate(groups, 1):
            keep = min(m, key=lambda i: _keeper_rank(items[i]))
            rows = []
            for i in sorted(m, key=lambda i: _keeper_rank(items[i])):
                if i == keep:
                    continue
                level, reason = edge.get((i, keep)) or ("same", "same photo (matched through another copy)")
                it, kept = items[i], items[keep]
                if it.account != kept.account and it.pixels > 1.4 * kept.pixels:
                    level = "check"
                    reason += f"; this copy is bigger than the one in account {kept.account}"
                rows.append((i, level, reason))
                going.add(i)
            self._group(g, keep, rows)

        # possible duplicates: best partner only, so one photo isn't listed many times,
        # and not for a copy that is already going
        best = {}
        for i, j, level, reason, score in sorted(found, key=lambda f: -f[4]):
            if (level == "check" and root(i) != root(j) and not best.keys() & {i, j}
                    and not going & {i, j}):
                best[i] = best[j] = (i, j, reason)
        g = len(groups)
        for i, j, reason in dict.fromkeys(best.values()):
            g += 1
            drop, keep = sorted((i, j), key=lambda x: _keeper_rank(items[x]), reverse=True)
            self._group(g, keep, [(drop, "check", reason)])

    def _group(self, g, keep, rows):
        kept = self.items[keep]
        self.keeper[g] = kept
        first = len(self.rows)
        self.rows.append({"group": g, "action": "keep", "level": "", "reason": "", "item": kept,
                          "section": ""})
        for i, level, reason in rows:
            it = self.items[i]
            section = ("check" if level == "check" else
                       "B in A" if (it.account, kept.account) == ("B", "A") else f"twice in {it.account}")
            self.rows.append({"group": g, "action": "delete", "level": level, "reason": reason,
                              "item": it, "section": section})
        # the group is shown in the section of its first copy to go, the keeper with it
        self.rows[first]["section"] = self.rows[first + 1]["section"]

    def removable(self):
        """Copies that are certainly duplicates of something kept."""
        return [r["item"] for r in self.rows if r["action"] == "delete" and r["level"] != "check"]

    def counts(self):
        c = defaultdict(int)
        for r in self.rows:
            if r["action"] == "delete":
                c[r["section"]] += 1
        return c


def find_all(items, workers=1, progress=print):
    progress(f"Comparing {len(items):,} photos and videos ...")
    pairs = candidate_pairs(items)
    found = compare(items, pairs, workers)
    return Result(items, found)


# ---- report -------------------------------------------------------------------------

def make_thumb(job):
    src, dest = job
    try:
        if os.path.splitext(src)[1].lower() in VIDEO_EXT:
            if not shutil.which("ffmpeg"):
                return False
            for seek in ("1", "0"):
                r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", seek, "-i", src,
                                    "-frames:v", "1", "-vf", "scale=320:-2", dest],
                                   capture_output=True, timeout=60)
                if r.returncode == 0 and os.path.exists(dest) and os.path.getsize(dest):
                    return True
            return False
        with Image.open(src) as im:
            im.draft("RGB", (640, 640))
            im = _flatten(ImageOps.exif_transpose(im)).convert("RGB")
            im.thumbnail((320, 320))
            im.save(dest, "JPEG", quality=80)
        return True
    except Exception:
        return False


def _href(path, base):
    try:
        rel = os.path.relpath(path, base)
        return urllib.parse.quote(rel.replace(os.sep, "/"))
    except ValueError:  # another drive on Windows
        return Path(os.path.abspath(path)).as_uri()


def _when(ts):
    if ts is None:
        return "no date"
    try:
        return datetime.fromtimestamp(ts).strftime("%d %b %Y %H:%M")
    except (OverflowError, OSError, ValueError):
        return "no date"


SECTIONS = [
    ("check", "Please have a look",
     "Very probably the same photo, but edited, turned, or the copy that would go is the "
     "bigger one. Tick the ones you want to delete."),
    ("B in A", "In account B and already in account A",
     "Checked pixel by pixel (photos) or frame by frame (videos): safe to delete from account "
     "B without looking. If you move everything from B into A and then empty B, you don't need "
     "to delete these one by one."),
    ("twice in A", "Twice in account A", "The same photo twice; the bigger copy is kept."),
    ("twice in B", "Twice in account B", "The same photo twice; the bigger copy is kept."),
]
LABEL = {"identical": "Identical file", "same": "Same photo", "check": "Same photo?"}


def write_report(result, out, workers=1, progress=print):
    """out: path without extension. Writes out.csv, out.html and out_files/ (thumbnails)."""
    base = os.path.dirname(os.path.abspath(out))
    files = out + "_files"
    os.makedirs(files, exist_ok=True)
    rows = result.rows
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")

    with open(out + ".csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "section", "action", "level", "reason", "account", "file",
                    "google_photos_link", "taken", "width", "height", "size_mb", "path"])
        for r in rows:
            it = r["item"]
            w.writerow([r["group"], r["section"], r["action"], r["level"], r["reason"], it.account,
                        it.name, it.url or "", _when(it.taken), it.w or "", it.h or "",
                        f"{it.size / 1e6:.2f}", os.path.abspath(it.path)])

    thumbs = {}
    for r in rows:
        it = r["item"]
        thumbs.setdefault(it.path, os.path.join(files, f"{it.sha[:20]}.jpg"))
    jobs = [(p, d) for p, d in thumbs.items() if not os.path.exists(d)]
    if jobs:
        progress(f"Making {len(jobs):,} thumbnails for the report ...")
    ok = dict(zip([p for p, _ in jobs], _map(make_thumb, jobs, workers)))

    def figure(r, data_id):
        it = r["item"]
        thumb = thumbs[it.path]
        img = (f'<img loading="lazy" src="{html.escape(_href(thumb, base))}" alt="">'
               if ok.get(it.path, os.path.exists(thumb)) else '<div class="noimg">no preview</div>')
        dims = f" · {it.w}×{it.h}" if it.w else ""
        link = (f'<a href="{html.escape(it.url)}" target="_blank" rel="noopener">Open in Google Photos</a>'
                if it.url else "<span class=muted>no Google Photos link: search for the name</span>")
        if r["action"] == "keep":
            head = "<b class=keep>Keep</b>"
            cls = "keep"
        else:
            checked = "" if r["level"] == "check" else " checked"
            label = LABEL[r["level"]]
            if it.video and r["level"] in ("same", "check"):
                label = "Same video" if r["level"] == "same" else "Same video?"
            head = (f'<label><input type="checkbox" data-id="{data_id}"{checked}> '
                    f'<b>Delete</b></label> <span class="lvl {r["level"]}">{label}</span>')
            cls = "del"
        why = f'<div class="why">{html.escape(r["reason"])}</div>' if r["reason"] else ""
        return (f'<figure class="{cls}"><a href="{html.escape(_href(it.path, base))}" target="_blank">'
                f'{img}</a><figcaption><div>{head} · account {it.account}</div>'
                f'<div class=name>{html.escape(it.name)}</div>'
                f'<div>{_when(it.taken)}{dims} · {it.size / 1e6:.1f} MB</div>{why}<div>{link}</div>'
                f'</figcaption></figure>')

    data, by_section = [], defaultdict(list)
    groups = defaultdict(list)
    for r in rows:
        groups[r["group"]].append(r)
    for g, rs in groups.items():
        figs = []
        for r in rs:
            data_id = ""
            if r["action"] == "delete":
                data_id = str(len(data))
                it = r["item"]
                data.append({"account": it.account, "file": it.name, "link": it.url or "",
                             "level": r["level"], "reason": r["reason"], "taken": _when(it.taken),
                             "path": os.path.abspath(it.path), "default": r["level"] != "check"})
            figs.append(figure(r, data_id))
        section = next(r["section"] for r in rs if r["action"] == "delete")
        by_section[section].append(f'<div class="card">{"".join(figs)}</div>')

    counts = result.counts()
    total = sum(counts.values())
    a_n = sum(1 for it in result.items if it.account == "A")
    b_n = len(result.items) - a_n
    parts = []
    for key, title, note in SECTIONS:
        cards = by_section.get(key)
        if not cards:
            continue
        opened = " open" if key == "check" or len(cards) <= 60 else ""
        parts.append(f'<details{opened}><summary><h2>{title} <small>({counts[key]:,})</small></h2>'
                     f'</summary><p class=note>{note}</p>{"".join(cards)}</details>')
    accounts = f"{a_n:,} photos and videos in account A" + (f", {b_n:,} in account B" if b_n else "")
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Duplicate photos</title>
<style>
:root{{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#667085;--line:#e3e6eb;--keep:#16794c;
--del:#b42318;--check:#b54708;--chip:#eef1f5}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#111418;--card:#1b1f25;
--text:#e6e8eb;--muted:#9aa3ae;--line:#2c323a;--keep:#47c58a;--del:#f97066;--check:#fdb022;--chip:#262c34}}}}
:root[data-theme=dark]{{--bg:#111418;--card:#1b1f25;--text:#e6e8eb;--muted:#9aa3ae;--line:#2c323a;
--keep:#47c58a;--del:#f97066;--check:#fdb022;--chip:#262c34}}
body{{font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;margin:0 auto;max-width:1200px;
padding:16px;background:var(--bg);color:var(--text)}}
h1{{font-size:22px;margin:4px 0 8px}} h2{{display:inline;font-size:17px}} small{{color:var(--muted);font-weight:400}}
summary{{cursor:pointer;padding:10px 0}} .note,.muted{{color:var(--muted)}}
.bar{{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);
display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:1}}
button{{font:inherit;padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--card);
color:var(--text);cursor:pointer}}
.card{{display:flex;flex-wrap:wrap;gap:10px;background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:10px;margin:10px 0}}
figure{{margin:0;flex:1 1 220px;max-width:340px}}
figure img,.noimg{{width:100%;height:220px;object-fit:contain;background:var(--chip);border-radius:6px;display:block}}
.noimg{{display:flex;align-items:center;justify-content:center;color:var(--muted)}}
figure.keep img{{outline:3px solid var(--keep)}} figure.del img{{outline:3px solid var(--del)}}
figcaption{{padding-top:6px;overflow-wrap:anywhere}} b.keep{{color:var(--keep)}} .name{{font-weight:600}}
.lvl{{font-size:12px;padding:1px 6px;border-radius:9px;background:var(--chip)}} .lvl.check{{color:var(--check)}}
.why{{color:var(--muted)}} a{{color:inherit}}
</style></head><body>
<h1>Duplicate photos</h1>
<p>{accounts}. Found <b>{total:,}</b> copies that can go
({counts.get('B in A', 0):,} in account B that are already in A, {counts.get('twice in A', 0):,} twice in A,
{counts.get('twice in B', 0):,} twice in B) and <b>{counts.get('check', 0):,}</b> to have a quick look at.
Green = kept, red = the copy to delete. Click a picture to open the file.</p>
<div class="bar"><button id="dl">Download the ticked photos as a list (CSV)</button>
<span id="count" class="muted"></span></div>
{''.join(parts) or '<p>No duplicates found.</p>'}
<script>
const rows = {json.dumps(data, ensure_ascii=False).replace("</", "<\\/")};
const key = "duplicates-{stamp}";
let saved = {{}};
try {{ saved = JSON.parse(localStorage.getItem(key) || "{{}}"); }} catch (e) {{}}
const boxes = [...document.querySelectorAll("input[data-id]")];
function count() {{
  const n = boxes.filter(b => b.checked).length;
  document.getElementById("count").textContent = n + " ticked";
}}
for (const b of boxes) {{
  if (b.dataset.id in saved) b.checked = saved[b.dataset.id];
  b.addEventListener("change", () => {{
    saved[b.dataset.id] = b.checked;
    try {{ localStorage.setItem(key, JSON.stringify(saved)); }} catch (e) {{}}
    count();
  }});
}}
count();
document.getElementById("dl").addEventListener("click", () => {{
  const q = v => '"' + String(v).replace(/"/g, '""') + '"';
  const lines = [["account", "file", "google_photos_link", "taken", "level", "reason", "path"].join(",")];
  for (const b of boxes) if (b.checked) {{
    const r = rows[+b.dataset.id];
    lines.push([r.account, r.file, r.link, r.taken, r.level, r.reason, r.path].map(q).join(","));
  }}
  const blob = new Blob(["\\ufeff" + lines.join("\\r\\n")], {{type: "text/csv"}});
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "photos-to-delete.csv";
  a.click();
}});
</script></body></html>"""
    with open(out + ".html", "w", encoding="utf-8") as f:
        f.write(page)
    return counts


def need_pictures():
    if Image is None:
        sys.exit("This needs Pillow and NumPy to look at the pictures:\n"
                 "    pip install pillow pillow-heif numpy")


def print_summary(result, out, progress=print):
    c = result.counts()
    dels = [r["item"] for r in result.rows if r["action"] == "delete" and r["level"] != "check"]
    size = sum(it.size for it in dels)
    vids = sum(it.video for it in dels)
    progress("")
    progress(f"{sum(1 for it in result.items if it.account == 'A'):,} photos and videos in account A"
             + (f", {sum(1 for it in result.items if it.account == 'B'):,} in account B"
                if any(it.account == "B" for it in result.items) else ""))
    progress(f"  {c.get('B in A', 0):6,}  in account B and already in A")
    progress(f"  {c.get('twice in A', 0):6,}  twice in account A")
    progress(f"  {c.get('twice in B', 0):6,}  twice in account B")
    progress(f"  {c.get('check', 0):6,}  to have a quick look at")
    progress(f"Deleting the certain ones frees about {size / 1e9:.2f} GB"
             + (f" ({vids:,} of them videos)." if vids else "."))
    progress(f"Report: {out}.html (open it in a browser) and {out}.csv")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("account_a", help="Takeout folder of the account you keep")
    ap.add_argument("account_b", nargs="?", help="Takeout folder of a second account")
    ap.add_argument("--out", default="report", help="report path without extension")
    ap.add_argument("--workers", type=int, default=default_workers(),
                    help="processes reading files at the same time")
    ap.add_argument("--open", action="store_true", dest="open_report",
                    help="open the report in your browser when it is ready")
    ap.add_argument("--similar", action="store_true", help=argparse.SUPPRESS)  # always on now
    a = ap.parse_args()
    need_pictures()
    cache = Cache(a.out + "_files/fingerprints.sqlite")
    items = load_account(a.account_a, "A", cache, a.workers)
    if a.account_b:
        items += load_account(a.account_b, "B", cache, a.workers)
    result = find_all(items, a.workers)
    write_report(result, a.out, a.workers)
    print_summary(result, a.out)
    if a.open_report:
        import webbrowser
        webbrowser.open(Path(os.path.abspath(a.out + ".html")).as_uri())


if __name__ == "__main__":
    main()

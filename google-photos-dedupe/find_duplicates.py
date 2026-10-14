#!/usr/bin/env python3
"""Find photos/videos that exist in BOTH of two Google Photos Takeout exports.

Usage:
    python find_duplicates.py KEEP_DIR CLEAN_DIR [--out report] [--similar]

KEEP_DIR   extracted Takeout folder of the account you want to keep as the master copy
CLEAN_DIR  extracted Takeout folder of the account you want to remove duplicates from

Every item in CLEAN_DIR that also exists in KEEP_DIR is listed in report.html and
report.csv, with a link straight to that photo in Google Photos (taken from the
Takeout JSON sidecar) so you can open it and delete it.

Matching, strongest first:
  exact    identical file bytes (SHA-256)
  meta     same capture time (to the second) and same file name -- catches copies
           that Google re-compressed ("Storage saver"), so bytes differ
  similar  (only with --similar, needs `pip install pillow imagehash`) visually
           near-identical images taken within 24 hours of each other
"""

import argparse
import csv
import hashlib
import html
import json
import os
import re
import sys
from collections import defaultdict

MEDIA_EXT = {
    ".jpg", ".jpeg", ".png", ".gif", ".heic", ".heif", ".webp", ".bmp", ".tif", ".tiff",
    ".dng", ".raw", ".cr2", ".nef", ".arw",
    ".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm", ".mts",
}
DUP_SUFFIX = re.compile(r"^(.*?)(\(\d+\))$")  # "IMG_1234(1)" -> ("IMG_1234", "(1)")


class Item:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        self.size = os.path.getsize(path)
        self.sha = None
        self.taken = None  # unix seconds
        self.url = None
        self.phash = None


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_sidecars(dirpath, filenames):
    """Map media file name -> parsed JSON sidecar for one directory.

    Takeout sidecar names are messy (".json", ".supplemental-metadata.json",
    truncated to ~51 chars, "(1)" moved after the extension), so match on the
    "title" field inside the JSON instead of on the sidecar's file name.
    """
    by_title = defaultdict(list)
    for fn in filenames:
        if not fn.lower().endswith(".json"):
            continue
        try:
            with open(os.path.join(dirpath, fn), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("title"):
            m = re.search(r"\((\d+)\)\.json$", fn)
            by_title[data["title"]].append((m.group(1) if m else "", data))
    return by_title


def sidecar_for(name, by_title):
    stem, ext = os.path.splitext(name)
    m = DUP_SUFFIX.match(stem)
    n = ""
    if m:  # "IMG(1).jpg" -> title "IMG.jpg", sidecar "IMG.jpg(1).json"
        stem, n = m.group(1), m.group(2)[1:-1]
    candidates = by_title.get(stem + ext) or by_title.get(name) or []
    for num, data in candidates:
        if num == n:
            return data
    return candidates[0][1] if len(candidates) == 1 else None


def scan(root):
    items = []
    for dirpath, _, filenames in os.walk(root):
        sidecars = load_sidecars(dirpath, filenames)
        for fn in filenames:
            if os.path.splitext(fn)[1].lower() not in MEDIA_EXT:
                continue
            it = Item(os.path.join(dirpath, fn))
            meta = sidecar_for(fn, sidecars)
            if meta:
                ts = (meta.get("photoTakenTime") or {}).get("timestamp")
                it.taken = int(ts) if ts and str(ts).isdigit() else None
                it.url = meta.get("url")
            items.append(it)
    return items


def dedupe_within(items):
    """Takeout puts the same photo in 'Photos from YYYY' and every album folder.
    Collapse those copies so each photo is counted once."""
    seen = {}
    for it in items:
        it.sha = sha256(it.path)
        seen.setdefault(it.sha, it)
    return list(seen.values())


def norm_name(name):
    stem, ext = os.path.splitext(name.lower())
    m = DUP_SUFFIX.match(stem)
    stem = m.group(1) if m else stem
    stem = re.sub(r"[-_ ]?(edited|bearbeitet|modifié)$", "", stem)
    return stem + {".jpeg": ".jpg"}.get(ext, ext)


def add_phashes(items):
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        sys.exit("--similar needs: pip install pillow imagehash (pillow-heif for HEIC)")
    try:
        from pillow_heif import register_heif_opener
        register_heif_opener()
    except ImportError:
        pass
    for it in items:
        try:
            with Image.open(it.path) as im:
                it.phash = imagehash.phash(im)
        except Exception:
            pass


def find_matches(keep, clean, similar):
    by_sha = {it.sha: it for it in keep}
    by_meta = {(it.taken, norm_name(it.name)): it for it in keep if it.taken}
    matches = []
    for it in clean:
        if it.sha in by_sha:
            matches.append((it, by_sha[it.sha], "exact"))
        elif it.taken and (it.taken, norm_name(it.name)) in by_meta:
            matches.append((it, by_meta[(it.taken, norm_name(it.name))], "meta"))
        else:
            matches.append((it, None, None))

    if similar:
        pending = [m for m in matches if m[1] is None]
        add_phashes([m[0] for m in pending] + keep)
        hashed = [k for k in keep if k.phash is not None]
        for i, (it, kept, _) in enumerate(matches):
            if kept is not None or it.phash is None:
                continue
            for k in hashed:
                if it.taken and k.taken and abs(it.taken - k.taken) > 86400:
                    continue
                if it.phash - k.phash <= 4:
                    matches[i] = (it, k, "similar")
                    break
    return [m for m in matches if m[1] is not None]


def write_reports(matches, keep_total, clean_total, out):
    with open(out + ".csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["match", "delete_from_clean_account", "google_photos_link",
                    "size_bytes", "kept_copy_in_keep_account"])
        for it, k, how in matches:
            w.writerow([how, it.path, it.url or "", it.size, k.path])

    wasted = sum(it.size for it, _, _ in matches)
    rows = []
    for it, k, how in matches:
        link = (f'<a href="{html.escape(it.url)}" target="_blank">open &amp; delete</a>'
                if it.url else "<em>no link - search by name</em>")
        rows.append(
            f"<tr><td>{how}</td><td>{html.escape(it.name)}</td><td>{link}</td>"
            f"<td>{it.size / 1e6:.1f} MB</td><td>{html.escape(k.name)}</td></tr>")
    with open(out + ".html", "w", encoding="utf-8") as f:
        f.write(f"""<!doctype html><meta charset="utf-8"><title>Photo duplicates</title>
<style>body{{font:14px system-ui;margin:2em}}table{{border-collapse:collapse}}
td,th{{border:1px solid #ccc;padding:4px 8px;text-align:left}}</style>
<h1>Duplicates to delete from the second account</h1>
<p>{len(matches)} of {clean_total} items in the second account already exist in the
first account ({keep_total} items). Deleting them frees about {wasted / 1e9:.2f} GB.</p>
<p><b>exact</b> = identical file &middot; <b>meta</b> = same name and capture time
&middot; <b>similar</b> = looks the same (check before deleting)</p>
<table><tr><th>match</th><th>file</th><th>link</th><th>size</th><th>kept copy</th></tr>
{''.join(rows)}</table>""")
    return wasted


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("keep_dir")
    ap.add_argument("clean_dir")
    ap.add_argument("--out", default="report", help="report path without extension")
    ap.add_argument("--similar", action="store_true", help="also find look-alike images")
    a = ap.parse_args()

    print("Scanning", a.keep_dir)
    keep = dedupe_within(scan(a.keep_dir))
    print("Scanning", a.clean_dir)
    clean = dedupe_within(scan(a.clean_dir))
    matches = find_matches(keep, clean, a.similar)
    wasted = write_reports(matches, len(keep), len(clean), a.out)

    counts = defaultdict(int)
    for _, _, how in matches:
        counts[how] += 1
    print(f"\n{len(keep)} items in keep account, {len(clean)} in clean account")
    print(f"{len(matches)} duplicates ({dict(counts)}), {wasted / 1e9:.2f} GB")
    print(f"Report: {a.out}.html and {a.out}.csv")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Photo Organizer: merge two Google Photos accounts, remove duplicates, and sort
every photo into albums using AI image understanding.

  python photo_app.py organize TAKEOUT_A TAKEOUT_B --out organized
      Finds duplicates between the two accounts, classifies every unique photo
      with Claude's vision, and builds organized/Albums/<album>/... plus an
      HTML gallery and a duplicates report. Read-only on your exports.

  python photo_app.py upload organized --credentials client_secret.json
      Creates the albums in a Google Photos account and uploads each photo into
      its album, with the AI caption as the description (searchable in Photos).
"""

import argparse
import html
import json
import os
import shutil
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import find_duplicates as fd  # noqa: E402

UNSORTED = "Unsorted"
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm", ".mts"}


def place(src, dest_dir, used):
    """Hard-link (no extra disk space) or copy src into dest_dir, avoiding name clashes."""
    os.makedirs(dest_dir, exist_ok=True)
    stem, ext = os.path.splitext(os.path.basename(src))
    name, n = stem + ext, 1
    while (dest_dir, name.lower()) in used:
        name, n = f"{stem}_{n}{ext}", n + 1
    used.add((dest_dir, name.lower()))
    dest = os.path.join(dest_dir, name)
    if not os.path.exists(dest):
        try:
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
    return dest


def write_gallery(entries, out_dir):
    by_album = defaultdict(list)
    for e in entries:
        by_album[e["album"]].append(e)
    sections = []
    for album in sorted(by_album, key=lambda a: (a == UNSORTED, a)):
        cards = []
        for e in by_album[album]:
            rel = html.escape(os.path.relpath(e["organized_path"], out_dir))
            cap = html.escape(e["caption"] or e["name"])
            if os.path.splitext(rel)[1].lower() in VIDEO_EXT:
                media = f'<video preload="none" controls src="{rel}"></video>'
            else:
                media = f'<img loading="lazy" src="{rel}" alt="{cap}">'
            cards.append(f'<figure>{media}<figcaption>{cap}</figcaption></figure>')
        sections.append(f'<h2>{html.escape(album)} <small>({len(cards)})</small></h2>'
                        f'<div class="grid">{"".join(cards)}</div>')
    with open(os.path.join(out_dir, "gallery.html"), "w", encoding="utf-8") as f:
        f.write("""<!doctype html><meta charset="utf-8"><meta name="viewport"
content="width=device-width,initial-scale=1"><title>Photo albums</title><style>
body{font:14px system-ui;margin:16px;background:#fafafa;color:#222}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:8px}
figure{margin:0;background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 1px 2px #0002}
img,video{width:100%;height:160px;object-fit:cover;display:block}
figcaption{padding:4px 6px;font-size:12px}small{color:#777;font-weight:normal}
@media(prefers-color-scheme:dark){body{background:#111;color:#eee}figure{background:#222}}
</style><h1>Your organised photo library</h1>""" + "".join(sections))


def cmd_organize(a):
    from classify import Classifier, load_albums

    os.makedirs(a.out, exist_ok=True)
    print("Scanning account A:", a.keep_dir)
    keep = fd.dedupe_within(fd.scan(a.keep_dir))
    print("Scanning account B:", a.clean_dir)
    clean = fd.dedupe_within(fd.scan(a.clean_dir))

    matches = fd.find_matches(keep, clean, a.similar)
    fd.write_reports(matches, len(keep), len(clean), os.path.join(a.out, "duplicates"))
    dup_shas = {it.sha for it, _, _ in matches}
    library = [(it, "A") for it in keep] + [(it, "B") for it in clean if it.sha not in dup_shas]
    print(f"\n{len(matches)} duplicates found; merged library has {len(library)} unique items")

    albums = load_albums(a.albums)
    clf = Classifier(albums, a.model, os.path.join(a.out, "classifications.json"), a.workers)
    print(f"Classifying with {a.model} ...")
    results = clf.classify([it for it, _ in library])

    used, entries = set(), []
    for it, src in library:
        r = results.get(it.sha) or {}
        album = r.get("album") or UNSORTED
        dest = place(it.path, os.path.join(a.out, "Albums", album), used)
        entries.append({"sha": it.sha, "name": it.name, "path": os.path.abspath(it.path),
                        "organized_path": os.path.abspath(dest), "account": src,
                        "album": album, "caption": r.get("caption", ""),
                        "google_photos_url": it.url})
    with open(os.path.join(a.out, "library.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1)
    write_gallery(entries, a.out)

    print("\nAlbums:")
    for album, n in Counter(e["album"] for e in entries).most_common():
        print(f"  {n:6d}  {album}")
    print(f"\nOpen {os.path.join(a.out, 'gallery.html')} to browse, and "
          f"{os.path.join(a.out, 'duplicates.html')} for the duplicates.")
    if any(e["album"] == UNSORTED for e in entries):
        print("Some items are Unsorted (unreadable or skipped). Re-run to retry them.")


def cmd_upload(a):
    from gphotos import Uploader, login

    with open(os.path.join(a.organized_dir, "library.json"), encoding="utf-8") as f:
        entries = json.load(f)
    if a.only_account_b:
        entries = [e for e in entries if e["account"] == "B"]
    if not a.include_unsorted:
        entries = [e for e in entries if e["album"] != UNSORTED]
    print(f"About to upload {len(entries)} items into "
          f"{len({e['album'] for e in entries})} albums.")
    if not a.yes and input("Sign in to the Google account to upload into and continue? [y/N] "
                           ).strip().lower() != "y":
        return
    session = login(a.credentials, os.path.join(a.organized_dir, "google_token.json"))
    up = Uploader(session, os.path.join(a.organized_dir, "upload_state.json"))
    n = up.upload([{"sha": e["sha"], "path": e["path"], "album": e["album"],
                    "caption": e["caption"]} for e in entries])
    print(f"Done: {n} uploaded this run. Re-run any time to resume.")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    o = sub.add_parser("organize", help="dedupe both accounts and sort photos into albums")
    o.add_argument("keep_dir", help="Takeout folder of account A (the main account)")
    o.add_argument("clean_dir", help="Takeout folder of account B (the second account)")
    o.add_argument("--out", default="organized")
    o.add_argument("--albums", default=os.path.join(HERE, "albums.json"),
                   help="JSON of album name -> what belongs in it")
    o.add_argument("--model", default="claude-opus-5-5")
    o.add_argument("--workers", type=int, default=8)
    o.add_argument("--similar", action="store_true",
                   help="also treat visually near-identical photos as duplicates")
    o.set_defaults(func=cmd_organize)

    u = sub.add_parser("upload", help="upload the organised albums into Google Photos")
    u.add_argument("organized_dir")
    u.add_argument("--credentials", required=True, help="OAuth client_secret.json")
    u.add_argument("--only-account-b", action="store_true",
                   help="upload only photos that are missing from account A")
    u.add_argument("--include-unsorted", action="store_true")
    u.add_argument("-y", "--yes", action="store_true", help="don't ask for confirmation")
    u.set_defaults(func=cmd_upload)

    a = ap.parse_args()
    a.func(a)


if __name__ == "__main__":
    main()

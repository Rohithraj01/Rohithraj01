"""Checks for the duplicate finder and the organizer. No downloads, no API calls.

    python tests/test_photo_organizer.py        (or: python -m pytest tests)

Builds two small Google Photos Takeout exports from generated pictures, with the
kinds of copies that occur in real exports, and checks every copy is found, nothing
that only looks alike is matched, and the organizer copes with a busy API.
"""
import csv
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-not-used")

import find_duplicates as fd  # noqa: E402

T0 = int(datetime(2024, 5, 1, 8, 0, tzinfo=timezone.utc).timestamp())


def scene(seed, w=1600, h=1200, shift=0):
    """A photo-like picture: soft light, shapes, texture. shift moves one shape."""
    rng = np.random.default_rng(seed)
    base = Image.fromarray(rng.integers(0, 255, (6, 8, 3), dtype=np.uint8)).resize(
        (w, h), Image.Resampling.BICUBIC)
    d = ImageDraw.Draw(base)
    for k in range(14):
        x, y = rng.integers(0, w), rng.integers(0, h)
        r = rng.integers(40, 260)
        if k == 0:
            x += shift
        colour = tuple(int(c) for c in rng.integers(0, 255, 3))
        (d.ellipse if k % 2 else d.rectangle)((x - r, y - r // 2, x + r, y + r // 2), fill=colour)
    texture = rng.normal(0, 9, (h, w, 1))
    img = np.clip(np.asarray(base, np.float32) + texture, 0, 255).astype(np.uint8)
    return Image.fromarray(img).filter(ImageFilter.GaussianBlur(0.6))


def chat(lines):
    im = Image.new("RGB", (1080, 2340), "#efeae2")
    d = ImageDraw.Draw(im)
    font = ImageFont.load_default(size=40)
    d.rectangle((0, 0, 1080, 200), fill="#075e54")
    for i, t in enumerate(lines):
        x0, y = (40 if i % 3 else 330), 260 + i * 170
        d.rounded_rectangle((x0, y, x0 + 700, y + 130), 22, fill="white" if i % 3 else "#d9fdd3")
        d.text((x0 + 26, y + 44), t, fill="black", font=font)
    return im


def exif(ts, orientation=1):
    ex = Image.Exif()
    ex[0x0112] = orientation
    ex.get_ifd(0x8769)[36867] = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y:%m:%d %H:%M:%S")
    return ex


def save(im, path, ts=None, q=92, orientation=1):
    ext = os.path.splitext(path)[1].lower()
    kw = {"exif": exif(ts, orientation)} if ts is not None and ext != ".png" else {}
    if ext == ".png":
        im.save(path)
    elif ext == ".heic":
        im.save(path, "HEIF", quality=q, **kw)
    else:
        im.convert("RGB").save(path, "JPEG", quality=q, **kw)
    return path


def sidecar(folder, name, ts, url):
    with open(os.path.join(folder, name + ".supplemental-metadata.json"), "w") as f:
        json.dump({"title": name, "photoTakenTime": {"timestamp": str(ts)},
                   "url": f"https://photos.google.com/photo/{url}"}, f)


def build(root):
    """Two Takeout exports. Returns {kind: (path in B or A, path it copies)} and look-alikes."""
    gp = ("Takeout", "Google Photos")
    A = os.path.join(root, "A", *gp, "Photos from 2024")
    album = os.path.join(root, "A", *gp, "Holiday")
    B1 = os.path.join(root, "B", "takeout-001", *gp, "Photos from 2024")
    B2 = os.path.join(root, "B", "takeout-002", *gp, "Photos from 2024")  # second zip
    for d in (A, album, B1, B2):
        os.makedirs(d)
    a = {}
    for k in range(16):
        name, ts = f"IMG_{2000 + k}.JPG", T0 + k * 4000
        im = scene(k)
        if k == 6:  # stored upright in A; B has the camera's pixels plus a rotation tag
            im = im.transpose(Image.Transpose.ROTATE_270)
        a[k] = (save(im, os.path.join(A, name), ts), ts, im, name)
        sidecar(A, name, ts, f"a{k}")
    shutil.copy(a[0][0], os.path.join(album, a[0][3]))  # album copy: same photo, not a duplicate
    sidecar(album, a[0][3], a[0][1], "a0")
    # IMG_2001-edited.JPG is the same Google Photos item as IMG_2001.JPG
    save(a[1][2].point(lambda v: min(255, int(v * 1.2))), os.path.join(A, "IMG_2001-edited.JPG"), a[1][1])

    copies = {}

    def copy(kind, k, name, im, folder=B1, ts="same", q=80, orientation=1, json_folder="same",
             exif_ts="same"):
        ts = a[k][1] if ts == "same" else ts
        exif_ts = a[k][1] if exif_ts == "same" else exif_ts
        p = save(im, os.path.join(folder, name), exif_ts, q=q, orientation=orientation)
        if json_folder:
            sidecar(folder if json_folder == "same" else json_folder, name, ts, kind)
        copies[kind] = (p, a[k][0])

    shutil.copy(a[0][0], os.path.join(B1, "IMG_2000.JPG"))
    sidecar(B1, "IMG_2000.JPG", a[0][1], "identical")
    copies["identical"] = (os.path.join(B1, "IMG_2000.JPG"), a[0][0])
    copy("storage saver", 2, "IMG_2002.JPG", a[2][2].resize((1360, 1020)), q=70)
    copy("whatsapp", 3, "IMG-20240503-WA0007.jpg", a[3][2].resize((1024, 768)), q=60,
         ts=a[3][1] + 3 * 86400 + 517, exif_ts=None)
    copy("time zone", 4, "IMG_2004.JPG", a[4][2], ts=a[4][1] + 19800, q=85)
    copy("json in other zip", 5, "IMG_2005.JPG", a[5][2].resize((1440, 1080)), json_folder=B2, q=75)
    copy("exif rotation", 6, "IMG_2006.JPG", a[6][2].transpose(Image.Transpose.ROTATE_90),
         orientation=6, q=85)
    copy("no json, no exif", 7, "IMG_2007.JPG", a[7][2], json_folder=None, exif_ts=None, q=78)
    copy("png", 8, "IMG_2008.png", a[8][2].resize((800, 600)))
    if fd.HEIF:
        copy("heic", 9, "IMG_2009.HEIC", a[9][2], q=50)
    copy("edited (cropped)", 10, "IMG_2010.JPG", a[10][2].crop((80, 60, 1520, 1140)), q=88)
    # inside A: a WhatsApp copy of A's own photo
    p = save(a[11][2].resize((800, 600)), os.path.join(A, "IMG-20240601-WA0001.jpg"), q=60)
    sidecar(A, "IMG-20240601-WA0001.jpg", a[11][1] + 40 * 86400, "insideA")
    copies["inside A"] = (p, a[11][0])

    alike = {}
    # burst: the next frame, a shape moved a little, camera name one higher
    p = save(scene(12, shift=30), os.path.join(B1, "IMG_2016.JPG"), a[12][1] + 1)
    sidecar(B1, "IMG_2016.JPG", a[12][1] + 1, "burst")
    alike["burst shot"] = (p, a[12][0])
    # a similar scene under an unrelated name, taken two seconds later
    p = save(scene(13, shift=45), os.path.join(B1, "photo_from_friend.jpg"), a[13][1] + 2)
    sidecar(B1, "photo_from_friend.jpg", a[13][1] + 2, "friend")
    alike["two shots, seconds apart"] = (p, a[13][0])
    # screenshots one message apart
    msgs = [f"message number {i} about the trip" for i in range(14)]
    s1 = save(chat(msgs[:12]), os.path.join(A, "Screenshot_20240510-101500.png"))
    sidecar(A, "Screenshot_20240510-101500.png", T0 + 900000, "s1")
    s2 = save(chat(msgs[:11] + ["see you at the station"]), os.path.join(B1, "Screenshot_20240512-090000.png"))
    sidecar(B1, "Screenshot_20240512-090000.png", T0 + 990000, "s2")
    alike["screenshot, one message different"] = (s2, s1)
    s3 = save(chat(msgs[:12]), os.path.join(B1, "Screenshot_copy.jpg"), q=60)
    sidecar(B1, "Screenshot_copy.jpg", T0 + 995000, "s3")
    copies["screenshot copy"] = (s3, s1)
    return copies, alike


def run(root, out):
    cache = fd.Cache(out + "_files/fingerprints.sqlite")
    items = fd.load_account(os.path.join(root, "A"), "A", cache, 2, progress=lambda *a: None)
    items += fd.load_account(os.path.join(root, "B"), "B", cache, 2, progress=lambda *a: None)
    result = fd.find_all(items, 2, progress=lambda *a: None)
    fd.write_report(result, out, 2, progress=lambda *a: None)
    return items, result


def pairs_of(result):
    """{frozenset(paths): level} for every reported duplicate."""
    keep = {r["group"]: r["item"].path for r in result.rows if r["action"] == "keep"}
    return {frozenset((os.path.realpath(r["item"].path), os.path.realpath(keep[r["group"]]))): r["level"]
            for r in result.rows if r["action"] == "delete"}


def test_duplicates():
    root = tempfile.mkdtemp(prefix="dups-")
    try:
        copies, alike = build(root)
        out = os.path.join(root, "report")
        items, result = run(root, out)
        found = pairs_of(result)
        problems = []
        for kind, (p, q) in copies.items():
            level = found.get(frozenset((os.path.realpath(p), os.path.realpath(q))))
            want = ("check",) if "edited" in kind else ("identical", "same")
            if level not in want:
                problems.append(f"{kind}: got {level}, want {want}")
        for kind, (p, q) in alike.items():
            level = found.get(frozenset((os.path.realpath(p), os.path.realpath(q))))
            if level in ("identical", "same"):
                problems.append(f"{kind}: matched as {level}")
        expected = {frozenset((os.path.realpath(p), os.path.realpath(q))) for p, q in copies.values()}
        for pair, level in found.items():
            if pair not in expected and level != "check":
                problems.append(f"unexpected {level}: {[os.path.basename(x) for x in pair]}")
        names = {it.name for it in items}
        if "IMG_2001-edited.JPG" in names:
            problems.append("the edited version was counted as a photo of its own")
        if sum(it.name == "IMG_2000.JPG" and it.account == "A" for it in items) != 1:
            problems.append("the album copy was not merged with the photo")
        with open(out + ".csv", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        if not rows or "google_photos_link" not in rows[0]:
            problems.append("report.csv is missing or has no links")
        page = open(out + ".html", encoding="utf-8").read()
        if 'type="checkbox"' not in page or "Download the ticked photos" not in page:
            problems.append("report.html has no tick boxes")
        thumbs = [f for f in os.listdir(out + "_files") if f.endswith(".jpg")]
        if len(thumbs) < len(copies):
            problems.append(f"only {len(thumbs)} thumbnails")
        # a second run reads nothing again and finds the same
        items2, result2 = run(root, out)
        if pairs_of(result2) != found:
            problems.append("a second run (from the cache) found something else")
        assert not problems, "\n  " + "\n  ".join(problems)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_name_and_time_rules():
    assert fd.time_relation(100, 100) == "same"
    assert fd.time_relation(100, 100 + 19800) == "same"       # India vs UTC
    assert fd.time_relation(100, 100 + 2700) == "same"        # 45 min (Nepal-style offsets)
    assert fd.time_relation(100, 103) == "near"
    assert fd.time_relation(100, 100 + 86400) == "far"
    assert fd.name_key("IMG_1234(1)-edited.HEIC") == "img_1234"
    assert fd.different_shots("IMG_1234.JPG", "IMG_1235.HEIC")
    assert not fd.different_shots("IMG_1234.JPG", "IMG_1234.HEIC")
    assert fd.different_shots("PXL_20240101_101010123.jpg", "PXL_20240101_101010456.jpg")
    assert not fd.different_shots("IMG_20240101_101010.jpg", "20240101_101010.jpg")
    assert not fd.different_shots("IMG-20240101-WA0001.jpg", "IMG_1234.JPG")


def test_classifier_retries_when_busy():
    import anthropic
    import httpx2
    import classify

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    calls = {"n": 0}

    def busy_then_ok(self, item):
        calls["n"] += 1
        if calls["n"] <= 6:  # the first six requests hit the rate limit
            raise anthropic.RateLimitError("rate limited", body=None, response=httpx2.Response(
                429, headers={"retry-after": "1"}, request=req))
        return {"album": "Travel", "caption": item.name}

    class It:
        def __init__(self, k):
            self.sha, self.path, self.name = f"sha{k}", f"/x/{k}.jpg", f"{k}.jpg"

    tmp = tempfile.mkdtemp()
    sleeps = []
    orig_ask, orig_sleep = classify.Classifier._ask, classify.time.sleep
    classify.Classifier._ask, classify.time.sleep = busy_then_ok, sleeps.append
    try:
        clf = classify.Classifier({"Travel": "trips"}, "claude-opus-5-5", os.path.join(tmp, "c.json"), 4)
        res = clf.classify([It(k) for k in range(10)], progress=lambda *a: None)
        assert all(r and r["album"] == "Travel" for r in res.values()), res
        assert sleeps and sleeps[0] >= 1, sleeps

        def no_credit(self, item):
            raise anthropic.BadRequestError("Your credit balance is too low", body=None,
                                            response=httpx2.Response(400, request=req))
        classify.Classifier._ask = no_credit
        clf = classify.Classifier({"Travel": "trips"}, "claude-opus-5-5", os.path.join(tmp, "d.json"), 2)
        try:
            clf.classify([It(k) for k in range(20)], progress=lambda *a: None)
            raise AssertionError("expected it to stop")
        except SystemExit as e:
            assert "credit balance" in str(e)
    finally:
        classify.Classifier._ask, classify.time.sleep = orig_ask, orig_sleep
        shutil.rmtree(tmp, ignore_errors=True)


def test_organize_keeps_unsure_copies():
    import classify
    import photo_app

    root = tempfile.mkdtemp(prefix="org-")
    orig = classify.Classifier._ask
    classify.Classifier._ask = lambda self, item: {"album": "Travel & Places", "caption": "test"}
    try:
        copies, alike = build(root)
        out = os.path.join(root, "organized")
        sys.argv = ["photo_app.py", "organize", os.path.join(root, "A"), os.path.join(root, "B"),
                    "--out", out, "--file-workers", "2"]
        photo_app.main()
        lib = json.load(open(os.path.join(out, "library.json"), encoding="utf-8"))
        paths = {os.path.realpath(e["path"]) for e in lib}
        for kind, (p, q) in copies.items():
            if "edited" in kind:  # unsure: kept, so nothing is lost
                assert os.path.realpath(p) in paths, kind
            else:
                assert os.path.realpath(p) not in paths, f"{kind} copy should have been left out"
        for kind, (p, q) in alike.items():
            assert os.path.realpath(p) in paths, f"{kind} is a different photo and must stay"
        edited = [e for e in lib if e["name"] == "IMG_2001-edited.JPG"]
        assert edited, "the edited version should be the one organised"
        assert os.path.exists(os.path.join(out, "gallery.html"))
        assert os.path.exists(os.path.join(out, "duplicates.html"))
    finally:
        classify.Classifier._ask = orig
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"ok    {name}")
            except Exception as e:  # noqa: BLE001
                failed += 1
                print(f"FAIL  {name}: {type(e).__name__}: {e}")
    print("all passed" if not failed else f"{failed} failed")
    sys.exit(1 if failed else 0)

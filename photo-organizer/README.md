# Photo Organizer

Merges two Google Photos accounts into one clean library:

1. **Finds duplicates** between the two accounts and inside each one, by looking at the
   photos and videos themselves (see [How duplicates are found](#how-duplicates-are-found)).
2. **Understands each photo** with Claude's vision and puts it in the right album
   (Family & Friends, Travel & Places, Food & Drinks, Screenshots, and so on). It also
   writes a short caption so the photo is easy to search for later.
3. **Uploads** the albums into Google Photos, with the caption as the description.

## What Google allows (please read)

Google's Photos API only lets apps **add** photos and create albums. It cannot read,
move or **delete** photos already in your account (since March 2025 not even reading).
So the app works from a **Google Takeout export** of each account, and anything that
has to be removed from Google Photos must be removed by you. The app gives you a list
with direct links for that.

## Setup (once)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...      # from https://platform.claude.com
```

On Windows PowerShell use `$env:ANTHROPIC_API_KEY = "sk-ant-..."`. The duplicate finder on its
own needs no API key. To match **video** copies by content, also install **ffmpeg** and have
it on your PATH (`winget install ffmpeg`, `brew install ffmpeg`, or `apt install ffmpeg`);
photos don't need it.

## 1. Export both accounts

At [takeout.google.com](https://takeout.google.com): *Deselect all → Google Photos →
Next → Create export*. Do this once in each account, then unzip into `takeout-A/` (the
main account you keep) and `takeout-B/` (the second account). A big export comes as several
zips: unzip **all** of them into that account's folder. It doesn't matter whether they end
up in one folder or side by side; a photo's date is found even when its JSON file landed in
another zip.

## 2. Organise

```bash
python3 photo_app.py organize takeout-A takeout-B --out organized
```

You get:

| file | what it is |
|---|---|
| `organized/gallery.html` | browse every unique photo, grouped by album, with captions |
| `organized/Albums/<album>/` | the photos sorted into folders (hard links, so no extra disk space) |
| `organized/duplicates.html` | the duplicates, side by side, with tick boxes and *Open in Google Photos* links |
| `organized/library.json` | everything the app worked out about each photo |

Only duplicates the app is certain about are left out of the merged library. The ones
marked *check* stay in, so nothing is lost if one turns out to be a different photo.

It's safe to stop and re-run: photos already classified aren't sent again, and files
already read aren't read again. If the Claude API says it's busy (rate limit), those photos
are tried again after a pause, fewer at a time, instead of ending up *Unsorted*. If the
API rejects every request (for example no credit left), the app stops at once with the
reason instead of going through every photo.

**Cost:** each photo is one request to the Claude API. With the default model
(`claude-opus-5-5`) a rough estimate is about **1 cent per photo** (~$100 per 10,000).
For a large library you can pick a cheaper model at much lower cost, e.g.
`--model claude-haiku-5-5` (a few dollars per 10,000). Try a small folder first to
compare quality.

**Your own albums:** edit `albums.json`. Each line is an album name and a description
of what belongs in it. The AI follows those descriptions.

## 3. Upload into Google Photos (optional)

One-time Google setup:
[Google Cloud Console](https://console.cloud.google.com) → new project → *APIs &
Services → Library* → enable **Photos Library API** → *OAuth consent screen* (External,
add your email as a test user) → *Credentials → Create credentials → OAuth client ID →
Desktop app* → download it as `client_secret.json`.

Then pick one:

```bash
# A) Bring everything from account B that's missing from A into A, sorted into albums
python3 photo_app.py upload organized --credentials client_secret.json --only-account-b

# B) Upload the whole merged, de-duplicated library with albums
#    (best into a new or empty account, otherwise account A's own photos end up there twice)
python3 photo_app.py upload organized --credentials client_secret.json
```

A browser window opens. Sign in to the account you want to upload **into**.
Uploads resume where they stopped if interrupted.

With option A, once the upload is done, account A has everything and **account B can be
emptied**. Photos that were already in A can't be put into the new albums by the app,
because Google doesn't allow it. `gallery.html` still shows which album each one belongs in.

## Just the duplicate list

`find_duplicates.py` is the duplicate finder on its own. It needs no API key:

```bash
python3 find_duplicates.py takeout-A takeout-B     # -> report.html, report.csv
python3 find_duplicates.py takeout-A               # duplicates inside one account
python3 find_duplicates.py takeout-A takeout-B --open   # also open the report when done
```

Open `report.html` in a browser (or pass `--open`). Each duplicate is shown next to the
copy that is kept (green = keep, red = delete), with its date, size, and a link to it in
Google Photos. Certain duplicates are ticked already; the button at the top downloads the
ticked ones as a list. Your ticks are remembered in that browser.

Deleted items stay in the Google Photos Bin for 60 days.

## How duplicates are found

The first version matched only identical files, or the same file name with the very same
date, so most real copies were missed. Now every photo is looked at:

1. Each picture gets a small fingerprint of what it looks like. Pictures with similar
   fingerprints, and files with the same name and date, are compared.
2. Those pairs are compared pixel by pixel at 384 pixels, after evening out brightness and
   contrast. A copy that was saved again differs a little everywhere; a different shot
   (a burst, or a screenshot one message further on) differs strongly in one place.

So these are found: identical files, *Storage saver* copies, HEIC photos saved as JPG,
PNG saved as JPG, WhatsApp copies (other name, other date, no EXIF), dates off by a time
zone, photos turned with an EXIF tag, photos without a JSON file, and copies inside one
account.

**Videos** are matched the same way. A handful of frames are sampled across each video and
compared frame by frame, so a re-encoded (*Storage saver*) or re-shared copy with another
name and date is found, not just a byte-identical one. Two different clips of the same
length do not match, because at least one sampled frame differs clearly. This needs
**ffmpeg** on your PATH (`winget install ffmpeg`, `brew install ffmpeg`, or
`apt install ffmpeg`); without it, videos still match by identical file or the same name
and date, and the app says so.

| label | meaning | ticked? |
|---|---|---|
| identical | the very same file | yes |
| same | the same photo saved again (smaller, re-compressed, other format or name) | yes |
| check | probably the same photo, but edited, cropped, turned, taken seconds apart, or the copy that would go is the bigger one | no, have a look |

Not reported: burst shots (`IMG_1234` and `IMG_1235` are never the same photo), two phones
photographing the same moment, and an `-edited` version next to its original (in Google
Photos they are one item). Copies in album folders of the export are counted once, because
Google Photos has them once.

Limits: RAW files and videos that can't be opened are matched by name and date only. Two
frames of a perfectly still scene can't be told apart and are reported as the same photo;
two different videos of the same length whose scene barely changes may land in *check*.

Tested on two sample photo exports built from real photos and video frames (`tests/` has a
smaller self-contained version): the old matcher found 43 of 93 and 0 of 42 copies, the new
one finds all of them. No screenshot that differs by one message or one switch, and no
burst of people walking, was taken for a copy. Video matching was checked on real clips:
re-encoded and re-shared copies (down to 320 px, another container) and byte-identical
copies are all found, while different clips of the same length — including two halves of
one source video — are not. Speed: about 20 ms per 12-megapixel photo on 4 cores; videos
cost one ffmpeg pass each (cached afterwards), and a re-run reads only new files.

## Checks

```bash
python3 tests/test_photo_organizer.py
```

Builds two small Takeout exports from generated pictures, with every kind of copy above and
look-alikes that must not match, and checks the report, the organizer, and that a busy or
failing Claude API is handled. No downloads, no API calls.

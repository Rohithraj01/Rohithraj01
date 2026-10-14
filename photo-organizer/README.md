# Photo Organizer

Merges two Google Photos accounts into one clean library:

1. **Finds duplicates** between the two accounts: identical files, "Storage saver"
   copies and (optionally) look-alikes.
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

## 1. Export both accounts

At [takeout.google.com](https://takeout.google.com): *Deselect all → Google Photos →
Next → Create export*. Do this once in each account, then unzip into `takeout-A/` (the
main account you keep) and `takeout-B/` (the second account).

## 2. Organise

```bash
python3 photo_app.py organize takeout-A takeout-B --out organized
```

You get:

| file | what it is |
|---|---|
| `organized/gallery.html` | browse every unique photo, grouped by album, with captions |
| `organized/Albums/<album>/` | the photos sorted into folders (hard links, so no extra disk space) |
| `organized/duplicates.html` | photos in account B that are already in A, each with an *open & delete* link |
| `organized/library.json` | everything the app worked out about each photo |

It's safe to stop and re-run: photos already classified aren't sent again.

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

`find_duplicates.py` is the stand-alone duplicate finder. It needs no API key or
installs:

```bash
python3 find_duplicates.py takeout-A takeout-B     # -> report.html, report.csv
```

Deleted items stay in the Google Photos Bin for 60 days.

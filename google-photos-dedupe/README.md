# Google Photos duplicate finder (two accounts)

Finds photos and videos that are in **both** of your Google Photos accounts and gives
you a clickable list of the copies to delete from one of them.

## Steps

1. **Pick a master account** — the one you will keep everything in (call it account A).
   The other one (account B) is the one you clean up.
2. **Export both accounts** with [Google Takeout](https://takeout.google.com):
   *Deselect all → tick Google Photos → Next → .zip, 50 GB parts → Create export*.
   Do this signed in to account A, then again signed in to account B.
3. **Unzip** each export into its own folder, e.g. `takeout-A/` and `takeout-B/`.
   (If an export has several zips, unzip them all into the same folder.)
4. **Run** (needs Python 3, nothing else to install):

   ```bash
   python3 find_duplicates.py takeout-A takeout-B
   ```

   Optional, to also catch look-alike images (resized or re-encoded copies):

   ```bash
   pip install pillow imagehash pillow-heif
   python3 find_duplicates.py takeout-A takeout-B --similar
   ```

5. **Open `report.html`.** Each row has an *open & delete* link that opens that photo in
   account B. Sign in to account B in your browser, click each link, and press the bin icon.
   `report.csv` has the same list for a spreadsheet.
6. Deleted items go to the Bin and stay there for 60 days, so you can undo mistakes.

## How matches are judged

| match     | meaning                                         | safe to delete?          |
|-----------|-------------------------------------------------|--------------------------|
| `exact`   | byte-for-byte identical file                    | yes                      |
| `meta`    | same file name and same capture time (to the second) — usually a "Storage saver" copy | yes, almost always |
| `similar` | looks visually the same (only with `--similar`) | check the photo first    |

Nothing is deleted by the script. It only reads the export folders.

## Why not fully automatic?

Since March 2025 Google's Photos API no longer lets any outside app list or delete
photos it didn't upload, so the deletion has to be done by you in Google Photos. The
report's direct links make that as quick as possible.

**Tip:** if *everything* in account B is already in account A (the report count is close
to B's total), you don't need to delete one by one. Use Google Photos'
**Partner sharing** (Settings → Sharing) from B to A with *Save to library* turned on, so
anything only in B is copied into A, then clear out B.

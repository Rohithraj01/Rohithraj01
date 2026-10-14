"""Upload organised photos into Google Photos albums (Google Photos Library API).

The API only allows *adding*: it can create albums and upload photos, but it
cannot list, move or delete photos that this app did not upload.
"""

import json
import mimetypes
import os
import time

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

API = "https://photoslibrary.googleapis.com/v1"
SCOPES = ["https://www.googleapis.com/auth/photoslibrary.appendonly"]
BATCH = 50  # batchCreate limit


def login(client_secret, token_path):
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(client_secret, SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
        with open(token_path, "w") as f:
            f.write(creds.to_json())
    return AuthorizedSession(creds)


def _request(session, method, url, **kw):
    for attempt in range(5):
        r = session.request(method, url, timeout=300, **kw)
        if r.status_code in (429, 500, 502, 503, 504):
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


class Uploader:
    def __init__(self, session, state_path):
        self.s = session
        self.state_path = state_path
        self.state = {"albums": {}, "uploaded": {}}
        if os.path.exists(state_path):
            with open(state_path, encoding="utf-8") as f:
                self.state = json.load(f)

    def _save(self):
        with open(self.state_path, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)

    def album_id(self, title):
        if title not in self.state["albums"]:
            r = _request(self.s, "POST", f"{API}/albums", json={"album": {"title": title}})
            self.state["albums"][title] = r.json()["id"]
            self._save()
        return self.state["albums"][title]

    def _upload_bytes(self, path):
        mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
        with open(path, "rb") as f:
            r = _request(self.s, "POST", f"{API}/uploads", data=f, headers={
                "Content-type": "application/octet-stream",
                "X-Goog-Upload-Content-Type": mime,
                "X-Goog-Upload-Protocol": "raw",
            })
        return r.text

    def upload(self, entries, progress=print):
        """entries: list of dicts {sha, path, album, caption}. Resumable."""
        todo = [e for e in entries if e["sha"] not in self.state["uploaded"]]
        progress(f"{len(entries) - len(todo)} already uploaded, {len(todo)} to go")
        by_album = {}
        for e in todo:
            by_album.setdefault(e["album"], []).append(e)
        done = 0
        for album, group in by_album.items():
            aid = self.album_id(album)
            for i in range(0, len(group), BATCH):
                chunk = group[i:i + BATCH]
                new_items, by_token = [], {}
                for e in chunk:
                    try:
                        tok = self._upload_bytes(e["path"])
                    except Exception as ex:
                        progress(f"  upload failed {e['path']}: {ex}")
                        continue
                    by_token[tok] = e
                    new_items.append({
                        "description": e["caption"][:1000],
                        "simpleMediaItem": {"uploadToken": tok,
                                            "fileName": os.path.basename(e["path"])},
                    })
                if not new_items:
                    continue
                r = _request(self.s, "POST", f"{API}/mediaItems:batchCreate",
                             json={"albumId": aid, "newMediaItems": new_items})
                for res in r.json().get("newMediaItemResults", []):
                    e = by_token.get(res.get("uploadToken"))
                    item = res.get("mediaItem")
                    if e and item:
                        self.state["uploaded"][e["sha"]] = item["id"]
                        done += 1
                    elif e:
                        progress(f"  not created {e['path']}: {res.get('status')}")
                self._save()
                progress(f"  {done}/{len(todo)} uploaded ({album})")
        return done

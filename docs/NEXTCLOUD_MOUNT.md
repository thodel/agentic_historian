# Reading the scans out of the Nextcloud

The Laßberg digitisations are not copied to tei any more. The batch runner reads
the share directly, one page at a time, and keeps a JPEG working copy of each.

**A mount is not needed and did not work.** The obvious route was davfs2 or
rclone; on 2026-09-18 both were tried and both are refused with `401` against
this share's endpoint, while `curl` and `utils/nextcloud.py` are accepted at the
*same URL* with the same token and password. Whatever the difference is, it is
not the credentials and not the endpoint. §1 keeps the mount recipe for a share
where it does work; **§2 is the route this corpus uses**, and it needs no mount,
no root and no second HTTP client.

**Why.** The scans are uncompressed TIFF: 2636 × 3212 at three bytes a pixel is
25 MB for one page, and the share holds several thousand. A full mirror comes to
roughly 160 GB against the 92 GB tei has in total — the first attempt filled the
root filesystem and died at page 899 (2026-09-16) with 53 MB free. There is no
version of "mirror it" that fits on this machine.

**What a mount costs, and what pays for it.** Mounting moves the transfer rather
than removing it: opening a page becomes a network read of 25 MB, and a batch
opens every page at least once *per model*. That is why `atr-batch --cache-dir`
exists. Each page crosses the network once, is written out as a JPEG working copy
at full resolution (~0.7 MB), and every later read — a retry, the next model's
pass, a re-run next week — is a local file. 899 pages cost about 22 GB of
transfer once and 0.6 GB of disk for ever.

---

## 1 · Mount it (davfs2, system-wide)

tei has sudo; only ports 80, 443 and ssh are open, which WebDAV over HTTPS fits
inside. davfs2 rather than an rclone fuse mount in a user session: the batch runs
under `systemd --user` and a mount that disappears with a login shell takes a
multi-hour run with it.

```bash
sudo apt-get install -y davfs2
sudo mkdir -p /mnt/gwdg
```

**The endpoint.** A share link is a browser URL, not a WebDAV one. This share,

```
https://cloud.gugw.tu-darmstadt.de/nextcloud/s/FaGXMmkkoY23eaA
```

is served by a Nextcloud mounted under `/nextcloud`, so the path prefix belongs
in the WebDAV URL too — a mount against `https://<host>/public.php/webdav` on this
server reaches the web root and fails:

```
https://cloud.gugw.tu-darmstadt.de/nextcloud/public.php/webdav
```

- public share → `https://<host><prefix>/public.php/webdav`
- named account → `https://<host><prefix>/remote.php/dav/files/<user>/`

**Credentials.** A public share link is its own account: the username is the share
token — the part after `/s/`, here `FaGXMmkkoY23eaA` — and the password is the
share password (the same one already in `.env` as `NEXTCLOUD_SHARE_PASS`). A named
account uses the account name and an *app password*, never the login password.

```bash
# /etc/davfs2/secrets      — root only, or davfs2 refuses to read it
sudo install -m 600 /dev/null /etc/davfs2/secrets
sudo tee -a /etc/davfs2/secrets >/dev/null <<'EOF'
https://cloud.gugw.tu-darmstadt.de/nextcloud/public.php/webdav  FaGXMmkkoY23eaA  <share password>
EOF
```

`/etc` on tei is under etckeeper, so this file is committed to the local `/etc`
git repository in clear text on the next `apt` run. The repository does not leave
the machine and is mode 0700, but `chmod 600` on the file alone no longer removes
the secret — rotating the share password means rewriting that history or
accepting it.

**Cache size.** davfs2 caches whole files locally and defaults to 50 MB, which is
two of these pages. Give it enough to work with and not enough to fill the disk —
the page cache in §2 is the durable copy, this is only scratch:

```ini
# /etc/davfs2/davfs2.conf
cache_size    1024        # MB
table_size    4096        # this share is ~6400 files
use_locks     0           # read-only; locking a share we never write is pure latency
delay_upload  0
```

**fstab.** Read-only, owned by the user the batch runs as (`dh`), and `noauto` +
`_netdev` so a boot without the network does not hang:

```
https://cloud.gugw.tu-darmstadt.de/nextcloud/public.php/webdav  /mnt/gwdg  davfs  \
    ro,noauto,_netdev,uid=dh,gid=dh,dir_mode=0555,file_mode=0444,x-systemd.automount  0  0
```

```bash
sudo mount /mnt/gwdg
ls /mnt/gwdg/digitalisate | head
```

If the listing works and the first `cat … | wc -c` returns 25 MB, the mount is
done. If it hangs or 401s, it is almost always one of two things: the credentials
line, which davfs2 matches against the *exact* URL in fstab character for
character, or a missing path prefix — a Nextcloud under `/nextcloud` answers
`/public.php/webdav` at the web root with something that is not WebDAV.

### Alternative: rclone

Where davfs2 is not an option, `rclone mount --read-only --vfs-cache-mode off`
over a `webdav` remote does the same job, and must be run from a unit with
`RemainAfterExit` rather than a shell. It is more forgiving about odd Nextcloud
deployments and less forgiving about being left unattended for six hours.

---

## 2 · Read the share directly (`dav:`)

```bash
cd /home/dh/agentic_historian
.venv/bin/python3 -m agentic_historian atr-batch \
    --source     dav:Digitalisate \
    --cache-dir  agentic_historian/data/page_cache/lassberg \
    --models     trocr-kurrent,qwen3vl-german-xix-v2 \
    --run        atr_corpus_v2 \
    --concurrency 4 --dry-run
```

`--cache-dir` is **required** for a `dav:` source: it is the only copy of a page
that ever lands on this disk. Each archival TIFF crosses the network once, is
written out as a full-resolution JPEG (~0.7 MB against 25 MB), and every later
read — a retry, the next model's pass, next week's re-run — is that local file.
Nothing is mirrored and no page is ever stored at full size.

The folder name is the one **in the share**, and it is case-sensitive:
`Digitalisate`, not `digitalisate`. The share root also holds `atr_test_lassberg`,
an earlier output folder — naming the root instead of the folder would sweep that
in as pages.

**The share is enumerated once, then cached.** The walk is one PROPFIND per
folder — 24 minutes for this share's ~1000 folders — and it runs before a batch
reads a single page. A 25-page smoke run therefore spent more time listing than
recognising, and a resumed run paid it again for a list it already had. The
listing is stored next to the pages for **12 hours** (`NEXTCLOUD_LISTING_TTL_S`,
0 disables it).

Twelve hours because what it describes is a scanning project's output folder:
pages arrive in batches days apart. A page added since the listing is simply not
read until the cache expires — `--no-listing-cache` forces a fresh walk when you
know something changed. A `--limit` walk never reads or writes the cache, since a
partial list stored as the corpus would silently shorten every later run.

**The keys are the same as a local walk's.** A page's key is its path relative to
the root with separators folded, so `dav:Digitalisate` and a mirror of the same
tree produce identical keys — which means a corpus half-read from the mirror can
be finished from the share, and the 899 pages already read are skipped rather
than repeated.

---

## 3 · Or point the runner at a mount

```bash
cd /home/dh/agentic_historian
.venv/bin/python3 -m agentic_historian atr-batch \
    --source     /mnt/gwdg/digitalisate \
    --cache-dir  agentic_historian/data/page_cache/lassberg \
    --models     trocr-kurrent,qwen3vl-german-xix-v2 \
    --run        atr_corpus_v2 \
    --concurrency 4 \
    --dry-run
```

Drop `--dry-run` once the page count looks right.

**Smoke-run the new model first.** `--sample 25` on the same seed reads the same
twenty-five pages every time, so a smoke run and the corpus run are comparable
and the smoke run's pages are already done when the corpus run reaches them.

For `qwen3vl-german-xix-v2` the smoke run is not a formality. It is served
whole-page — one call per page, no dependency on the segmenter — and its
published 7.65 % CER is a *line*-level number measured at an eighth of the pixel
budget a page gets. Nothing measures the page shape, so **read the readings**, and
read them to the end: a page-level VLM that is out of its depth returns a short,
fluent, entirely correct fragment and a `200`. That is exactly how v1 failed here,
and no column in `report.md` would have shown it.

The order of `--models` is the order they run in, and the runner is model-major
(every page of one model, then the next) because the gateway's vLLM models are
`lazy` on one card and page-major would evict and reload per page. Set `ATR_PAGE_CACHE` in `.env`
to make the cache the default and leave the flag off.

**The first `--dry-run` is slow.** Discovery is `rglob` over the mount, which is
one PROPFIND per directory — measured at 1.75 s against this share, so a corpus
of a hundred folders spends about three minutes listing before it reads a byte.
That is once per run, not once per page.

**Provenance is unaffected.** Each result's `source.sha256` is the digest of the
archival TIFF, taken during the single read that produced the working copy;
`source.working_copy` names the JPEG the model actually saw. The two are
deliberately separate — a JPEG re-encoded from a TIFF has different bytes, and a
result claiming "this reading came from these bytes" has to mean the bytes in the
archive.

---

## 4 · Check the report before publishing

```bash
.venv/bin/python3 -m agentic_historian report-run \
    --run-dir agentic_historian/data/vlm_test/atr_trocr_corpus --dry-run
```

The report a run writes records what the runner *observed*, which is not the same
as what is on disk: a resumed run sees most of its corpus as `skipped` and counts
nothing about it — not characters, not timings, and **not whether a page came back
empty**, which is the one failure no other column reveals. `report-run` reads the
results themselves. Drop `--dry-run` to overwrite `report.md` and `report.json`.

It is marked as rebuilt, and its `failed` column is not trustworthy: a page that
failed wrote nothing, and nothing is what a rebuilt report cannot see.

---

## 5 · Where the text goes

To git, and only to git:

```bash
.venv/bin/python3 -m agentic_historian publish-batch \
    --run-dir agentic_historian/data/vlm_test/atr_trocr_corpus
```

This opens a **pull request** against
[`michaelscho/lassberg`](https://github.com/michaelscho/lassberg/tree/main/data)
`main`, adding `data/textrecognition/<model id>/` — one `.txt` and one `.json` per
page. The commits go to our fork `thodel/lassberg`, so tei's `GITHUB_TOKEN` needs
`contents: write` on **the fork only**; nothing is written to the edition
repository until its maintainer merges.

Run it again after more pages are recognised and the new files join the same pull
request.

Nothing is written back to the Nextcloud. The mount is read-only by design, and
the division is the point: the share holds the scans, the repository holds the
readings, and neither is a backup of the other.

---

## Troubleshooting

| symptom | cause |
|---|---|
| `Input/output error` on one page mid-run | a dropped WebDAV read; the runner retries the page, and the cache means a success is never re-fetched |
| the run slows down over hours | davfs2's cache thrashing — it is scratch, not the page cache; raise `cache_size` or confirm `--cache-dir` is actually set |
| `no page images under /mnt/gwdg/…` | the mount is up but empty: wrong `NEXTCLOUD_REMOTE_DIR`-equivalent subfolder, or the share token has no access to it |
| pages re-fetched on every run | `--cache-dir` missing, or pointed at a tmpfs that does not survive |
| `ValueError: … is not in the subpath of …` | `--source` and the cached page disagree about the root; `--cache-dir` is keyed by path *relative to* `--source`, so both have to name the same root each run |

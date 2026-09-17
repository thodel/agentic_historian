# Reading the scans from a mounted Nextcloud

The Laßberg digitisations are not copied to tei any more. The share is mounted
read-only and the batch runner reads the corpus in place.

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

**Credentials.** A public share link is its own account: the username is the
share token (the part after `/s/`), the password is the share password. A named
account uses the account name and an *app password*, never the login password.

```bash
# /etc/davfs2/secrets      — root only, or davfs2 refuses to read it
sudo install -m 600 /dev/null /etc/davfs2/secrets
sudo tee -a /etc/davfs2/secrets >/dev/null <<'EOF'
https://cloud.example.org/public.php/webdav    FaGXMmkkoY23eaA    <share password>
EOF
```

- public share → `https://<host>/public.php/webdav`
- named account → `https://<host>/remote.php/dav/files/<user>/`

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
https://cloud.example.org/public.php/webdav  /mnt/gwdg  davfs  \
    ro,noauto,_netdev,uid=dh,gid=dh,dir_mode=0555,file_mode=0444,x-systemd.automount  0  0
```

```bash
sudo mount /mnt/gwdg
ls /mnt/gwdg/digitalisate | head
```

If the listing works and the first `cat … | wc -c` returns 25 MB, the mount is
done. If it hangs, it is almost always the credentials line — davfs2 matches it
against the *exact* URL in fstab, character for character.

### Alternative: rclone

Where davfs2 is not an option, `rclone mount --read-only --vfs-cache-mode off`
over a `webdav` remote does the same job, and must be run from a unit with
`RemainAfterExit` rather than a shell. It is more forgiving about odd Nextcloud
deployments and less forgiving about being left unattended for six hours.

---

## 2 · Point the runner at it

```bash
cd /home/dh/agentic_historian
.venv/bin/python3 -m agentic_historian atr-batch \
    --source     /mnt/gwdg/digitalisate \
    --cache-dir  agentic_historian/data/page_cache/lassberg \
    --models     trocr-kurrent \
    --run        atr_trocr_corpus \
    --concurrency 4 \
    --dry-run
```

Drop `--dry-run` once the page count looks right. Set `ATR_PAGE_CACHE` in `.env`
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

## 3 · Where the text goes

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

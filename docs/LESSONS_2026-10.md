# What measuring the candidates cost, and what it taught

Written 2026-10-03, at the end of the day that got seven candidate engines
reading the 276 pages this corpus has hand-corrected text for. Not a changelog —
the commits are that. This is the set of mistakes worth not repeating, each with
the evidence that produced it.

Its predecessor is [LESSONS_2026-09.md](LESSONS_2026-09.md), written at the end
of the day that first reached tei at all. Two of today's five have the same shape
as one of its, which is the main reason this file exists.

---

## 1. A filter and a cut do not commute

The smoke run over the ground-truth pages failed like this:

```
warning: 276 of 276 key(s) are not in this source: Aarau__upload__lassberg-letter-0892__00012-…
Error: none of the 276 key(s) in …/gt-keys.txt name a page under dav:digitalisate
```

Which reads as a key-spelling problem and is not one. `--limit 3` was applied
inside discovery, *before* `--keys-from` filtered: the alphabetically first three
pages of a 6742-page corpus were matched against 276 keys and matched none of
them. The keys were correct — they had been written by a scoring run over a
reading of that same share.

Two things are worth keeping from it. The first is that **`docs/BATCH_ATR.md`
already described the right order** ("`--limit` and `--sample` apply to what is
left after the filter"), so the disagreement was between the code and its own
documentation, and the documentation was right. When those two disagree about an
*order*, the symptom surfaces at the far end as a data problem — a key list that
appears to be entirely wrong — and nothing points back at the ordering.

The second is that the fix is a function. The first-N / N-at-random cut existed
twice, near-identically, in the two discovery functions; neither could be reused
by a caller that has to cut after doing something else first. It is
`atr_batch.narrow` now, and the CLI calls it after the filter.

## 2. A status code is about the server, not about the process asking

The share spent the afternoon answering differently to the same code, and three
rounds of diagnosis went into the wrong half of the system each time.

What finally settled it was measuring the matrix instead of reading one number:

| endpoint | `/` | `digitalisate` | `digitalisate/Aarau` |
|---|---|---|---|
| `public.php/webdav` | 207 | 404 | 404 |
| `public.php/dav/files/<token>` | 207 | 207 | 207 |

So the legacy endpoint serves the share root and 404s everything below it, and
`_connect` was asking it first. That explained the path-dependent status codes.
It did not explain the rest: the same call that answered 207 from an interactive
shell answered 401 from `atr-mcp`, seconds apart, from the same checkout and the
same `.env.gpustack`.

```
atr-mcp pid: 894
NEXTCLOUD_SHARE_PASS   13 char(s)
diese shell: 14 char(s) aus /home/dh/agentic_historian/.env.gpustack
```

The service was started with `EnvironmentFile=/etc/atr-mcp.env`, which carried a
stale password, and **a value already in `os.environ` wins over every `.env`
file**: `load_dotenv(override=False)` cannot replace it. So the service kept
using a password that had been corrected in the file weeks earlier, and the file
looked innocent. `config.py` has carried a comment saying exactly this since
#106.

The lesson is not "check the environment". It is: **when two callers of the same
code get different answers from the same server, the difference is in the
callers, and no amount of reasoning about the server will find it.** The cheap
move — printing the password's *length* from both processes — settles in one
command what three rounds of inference did not.

A 401 from this share now says which file the password came from, or, when it was
inherited, that the environment won and where to look (`/proc/<pid>/environ`, the
unit's `EnvironmentFile=`). A 404 everywhere says the path is not there and that
the legacy endpoint 404s below the root. A failure with no status code adds
nothing, because a guess would be worse than the two endpoint errors.

## 3. A shell is not dotenv, and a probe that lies is worse than none

The first attempt to measure that matrix produced three clean 401s and nearly
sent the diagnosis into a fourth round. The probe was wrong:

```
./.env.gpustack: line 36: syntax error near unexpected token `newline'
./.env.gpustack: line 36: `ATR_WATCH_MENTION=<@817396581317738546>'
```

`set -a; . ./.env.gpustack` stops at the first line bash cannot parse — `<` is a
redirect — and every variable below it stays unset. The password was empty, and
an empty password is a 401. Nothing in the three result lines said so.

Two rules follow. **Read that file with the thing that reads it in production**,
which is Python: a one-line `python3 -c` that prints `len(...)` is both safer and
more accurate than sourcing. And **a probe has to report its own
preconditions** — the version that finally worked printed `password: 14 char(s)`
before the first request, which is what made the 13-vs-14 comparison possible at
all.

## 4. The same name is a file here and a string there

The run printed thirteen of these:

```
[batch] PA 82a B 9.pdf: cannot read the PDF ([Errno 2] No such file or directory:
  'digitalisate/Basel/PA 82a B 9.pdf')
```

A missing-file error for files that are on the share and had simply not been
downloaded. The seam was already in place — `_expand` takes its page counter as
an argument *because* "the two listings stand in different places" — and the
remote listing used the local default anyway. A relative path from a WebDAV
listing is a name; handing it to `Path.read_bytes` asks the local filesystem
about it, and the local filesystem answers about something else entirely.

The skip reason now says `PDF in the share, not counted without downloading it`,
and the local walk keeps `PDF could not be read`, because a file that will not
open is a different thing from one nobody fetched.

## 5. Files are not pages

Two ground-truth files can describe the same corpus page — a Transkribus document
exported twice, or two corrections of one leaf. Dropping one would throw away a
person's work. Counting both as pages weights that page twice in every average
afterwards, and the report said "552 ground-truth page(s)" either way.

`gt_score.duplicate_pages` groups them by the page they locate and the report
names them: how many files cover how many pages, each file keeping its own
section, and the paragraph only appears when there are duplicates. A report that
always warned about double weighting would be ignored on the day it mattered.

## 6. What the diagnosis itself got wrong

Worth writing down, because the pattern repeated inside one afternoon.

- **A mechanism was asserted as a cause without evidence.** A cold-tier job that
  moves working copies to the research share would explain an empty cache, and it
  was stated as the explanation before anything had been looked at. It had to be
  retracted, then partly re-affirmed when the cache turned out to hold one image.
  *(Corrected 2026-10-07: the job does exist. See §7 — the error was asserting it
  first and, four days later, denying it on a search that could not have found
  it.)*
- **A plausible number was read as a conclusion.** "500 is not what a wrong
  password looks like" was right about the code and wrong about the cause, twice.
- **A derived value was computed at import time.** `ATR_PAGE_CACHE_DEFAULT =
  ATR_PAGE_CACHE or DATA_DIR / "page_cache"` broke five existing tests at once,
  and the tests were right: a constant that derives from a value stops reading
  its input. It is `config.page_cache_dir()`.

The correction is the same each time, and it is not "be more careful". It is:
**name the single observation that would distinguish the hypotheses, and go and
make it.** The password length. The endpoint matrix. The two spellings the index
holds versus the one the export wanted. Each was one command away, and each was
reached only after ranking hypotheses by plausibility had failed.

## 7. A mechanism that keeps a log is not a mechanism to search for

Written 2026-10-07, four days after the rest of this file, because the afternoon
it cost belongs beside §2 and §6 rather than in a file of its own.

`export-hf` over MCP died before it reached a single page:

```
Error: no page images under …/data/page_cache. The page cache evicts to a cold
tier after two days (#487); pass --archive, or set ATR_PAGE_CACHE_ARCHIVE.
```

Four rounds went into "where did the images go", in this order, each a guess at
the *mechanism*: the service's environment (lesson §2, correctly suspected and
wrong this time), then `/etc/cron.*` and the systemd timers, then the mount table,
then the eviction script's shell wrapper. Round two produced the worst moment of
the day — `grep` over `/etc` found nothing, and that was read as **"there is no
such job on tei"**, which also looked like a confirmation of §6 above. It was a
search that could not have found the job: the entry is in `dh`'s *user* crontab,
which `/etc` does not contain. A non-observation was read as evidence, which is
§2 and §6 in one move: first the mechanism asserted without looking, then its
absence asserted from a search of the wrong place.

What settled it in one command, available from the first minute:

```
=== 2026-10-06T04:10:01   agentic_historian-page_cache: moved 238 files, 43.4 MB
=== 2026-10-07T04:10:01   agentic_historian-page_cache: moved 245 files, 220.2 MB
```

**238 is exactly the number of images the 5 October dry run found.** The same
number on both sides, from the job's own log — not an inference about a mechanism
but its record of what it did. The code names that job twice (`#487`,
`tei-vm-sanity#7`) and the job writes `archive.log` next to its script.

So the rule this adds to §2's: **a job that runs on a schedule writes down what it
did. Read its log before looking for the job.** Searching for the mechanism asks
"could this happen?"; reading the trace asks "did it happen, to these files, on
this night?" — and only the second one has the file count in it.

Two smaller things from the same afternoon, both the same shape:

- `ls -d …/*page_cache* …/*/*page_cache*` did not find the archive, and that was
  nearly taken as "the job writes elsewhere". It is at
  `/mnt/wbkolleg_dh_1/tei/archive/agentic_historian-page_cache` — depth three.
  A glob that misses is not an answer about where something is.
- A `grep` for `archive_root` missed `ARCHIVE_ROOT` on the next line of output,
  because it was case-sensitive. The constant was two lines from a match.

## 8. A configuration nobody set is not a default that works

The variable the error message named, `ATR_PAGE_CACHE_ARCHIVE`, was **not set
anywhere** — not in the process environment, not in `.env.gpustack`, not in
`.env`. Which means `images.cold_fetch`, added in #487 precisely so an evicted
working copy would be fetched back from the share instead of re-downloaded, had
**never run** since it was written. Every batch since 24 September pulled 25 MB
TIFFs over the wire and converted them again, while the 1.5 MB working copies sat
on a share that reads at 410 MB/s. The cold tier held 6719 of them — the whole
corpus.

Nothing was broken. `cold_fetch` returns `None` when the archive is unconfigured,
which is the right behaviour on any host without a share, and the docstrings
describe the two-tier cache as though it were in operation. **The documentation
described the state that was intended, not the state that held**, and there was no
measurement anywhere that would have noticed the difference — a cache that is
never consulted and a cache that always misses look identical from the outside.

The cheap thing that would have: `page_index` logs
`N page(s) in the cache, M in the cold tier` and always has. On 7 October it
printed `0 … 6719 (6719 only there)` for the first time. A line that says "the
cold tier contributed nothing" on every run for two weeks is a line nobody reads;
a config check at startup that names an unset variable the code depends on is one
somebody does.

---

## What held up

Not everything needed fixing, and two decisions paid for themselves today.

**Resumability by presence.** The smoke run was stopped mid-flight when the share
turned out to be unreachable, having written nothing. The seven-model run over
the same `--run` picked the three finished pages up and read the remaining 273
without being told anything about them. There is no state file to reconcile
because the output *is* the state.

**`--keys-out` writing to `GT_ROOT` rather than `/tmp`.** The key list survived
tei's reboot, which the earlier `/tmp/gt-keys.txt` did not — that cost sixteen
minutes of matching the first time.

**Named skips.** Every unread file in this run is in the report with a reason,
which is how the thirteen PDFs were noticed at all. A silent drop would have left
a corpus thirteen containers short with nothing saying so.

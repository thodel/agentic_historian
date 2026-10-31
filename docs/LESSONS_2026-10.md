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

## 9. The second time, with the same shape, six days later

`/pull_folder missiven` on 2026-10-09:

```
❌ Error: received 401 (Unauthorized)
```

The search ran network → endpoint → path → credentials, in that order, and the
credentials were last. They were the answer. Lesson §2 is about exactly this and
was written six days earlier about the Nextcloud share; it did not stop the
repetition, because the thing it taught was written in a document and not in the
code that produces the message.

### What the 401 could not say, and what cost the time

A `PROPFIND` on `…/agentic_historian_hotfolder/missiven/` answers 401 **before it
looks at the path**. So "the app passcode is dead" and "that folder is not there"
arrive as the same sentence, and there is no way to tell them apart from the
error. Asking the **root** first splits them in one request:

| root | target | the answer |
|---|---|---|
| 401  | *not asked* | the credentials; the path is innocent |
| 207  | 404 | the path; the credentials are fine |

That reordering is the whole of #563. It is now `webdav_probe.probe`, and the
test that pins it fails thirteen times if the auth layer is pointed at the
target.

### Two detours that were mine

**A `grep -o` ate the answer.** The first command handed over piped the status
through `grep -o '<d:href>…'`, which prints only matches — so the `--w "status
%{http_code}"` line could never survive it, and the output was empty. An empty
answer from a command that cannot print the answer is not evidence of anything.

**A shell is not the service** (§3 again). The same command assumed
`$SWITCHDRIVE_USER` and `$SWITCHDRIVE_PASS` were exported in an interactive
login shell. They live in `.env.gpustack` and reach the process through
python-dotenv, so bash had neither, and `curl -u ":"` returns 401 for its own
reasons. Two different causes, one indistinguishable symptom.

### The diagnosis that was right, and two reasons for it that were wrong

The credentials were indeed rejected, and a new App Passcode in
`~/agentic_historian/.env.gpustack` returned 207 at the root. The *mechanism* I
offered for it was wrong twice.

**First: "SWITCH forces periodic password rotation."** Not true. Switch edu-ID's
password page cites NIST SP 800-63B and lists periodical password changes under
its **don'ts**, and nothing in the help pages says App Passcodes expire on a
schedule.

**Then: the stored value was the account password, which mandatory MFA
(September 2025) finally shut out.** Also not true. The value that was replaced
was itself an App Passcode, of the same shape and length as the one that fixed
it.

What is **established** is narrow: a valid App Passcode stopped being accepted,
and a freshly created one is accepted. Why is **not known**. What can be named
without asserting it — SWITCH documents one passcode per device, and that
deleting one closes its connection immediately, so a passcode shared across
machines dies when any one of them is tidied up; and SWITCHdrive's login moved
to the edu-ID screen at some point, which is the kind of migration that can
invalidate what came before. The check that would settle it is on the Security
page: **is the old passcode still listed?** Gone means it was removed or
invalidated. Listed and rejected means it is SWITCH's end, and a ticket.

Two lessons, and the second is the useful one.

**A mechanism offered as the cause of a measured failure is still a guess**, and
it reads as a finding unless it is marked as one. The 401 was measured. The
rotation was invented, and so was the account-password story — the second one
proposed with the first one's refutation already written on the page above it.

**The cause not being knowable is the argument for the watcher, not against it.**
Three mechanisms proposed, two refuted, one still open — while the *failure*
itself was measurable in seconds from the start. A credential whose breaking
cannot be predicted is exactly the one that has to be observed: #589's watcher
needs no theory of why passcodes die in order to say that this one has. Had it
existed, none of the paragraphs above would have had to be written.

One passcode per machine from now on, since that is the one thing the
documentation is explicit about.

### Why a restart was half the fix, and why a reload is not the other half

`config.py` reads the `.env` files at **import**. The running bot therefore held
the old passcode after the file was corrected, and `/pull_folder` would have kept
answering 401 with a perfectly good password on disk.

A `/reload` command looks like the obvious fix and silently does nothing.
Measured:

```
1. first load            : alt
2. file changed, load    : alt   <- override=False
3. with override=True    : neu
```

`load_dotenv(override=False)` puts the file's value into `os.environ` on the
first load, so every later load finds the key already present and leaves it
alone. `importlib.reload(config)` would re-run that same no-op. A reload that
reports success and changes nothing is worse than no reload.

It *is* implementable, and `ENV_SOURCE` is what makes it so: it holds exactly
the keys whose value came from a file, which are exactly the keys it is safe to
drop from `os.environ` and read again. The keys it does **not** hold came from
the real process environment, where #106 says the environment must win. The
distinction the module was added for turns out to be the precondition for
reloading at all.

### What must not be built

Not a Discord command that takes the passcode. A slash-command parameter and a
modal field are both transmitted to Discord and kept in its interaction logs;
`ephemeral` hides a reply from other people's clients and does not make the
value a secret again. It would also mean the bot writes its own credential
store, which turns "can run an admin command" into "can change any secret".

What the bot can do without ever seeing it: say that the credentials are being
rejected, say which file holds them, and say so **before** somebody discovers it
through a failed pull. Nothing was watching that endpoint, which is why a dead
passcode presented itself as a mystery about a folder.

## 10. A count is a complaint; only the keys are a task

The report had columns for the three ways a reading comes back unusable — empty,
padded, cut off — and for two of them it named the pages. That looked complete
until somebody had to act on it. `gemini-300-strict` flagged 33 of 300 pages;
`report.md` named 21 of them, because each section capped its list at twenty **as
the keys were collected**, so the twenty-first empty page was counted into a
number and then dropped. Nothing held it. For a cut-off page there was no list at
all, only the runner's warning, which names the image file (`002.jpg`) and not
the page key a selection is made of.

So the next step — read those pages again — was a reading exercise: open the
Markdown, copy keys by hand, hope the count and the list agree. Three defects,
three remedies, one next step, and the one next step was the only part the
pipeline could not do.

The fix is small and the shape of it generalises: the cap belongs to the
rendering, never to the record, and anything a report complains about should come
out as the input of the thing that would fix it. `--keys-out` and
`batch_report(keys_out=…)` now write the flagged pages as the key list
`--keys-from` already took. The handover already existed for ground truth
(`score_ground_truth` → `start_batch`); this is the same handover for failures.

**The generalisation to be suspicious of:** a number in a report is not evidence
that anybody can act on the thing it counts. Ask what the next command would be,
and whether the report can produce its argument.

## 11. A prompt that forbids guessing buys silence, and silence passes every check

The strict prompt fixed what it was written for: no preamble, no invented
structure labels, no Markdown, over 300 pages rather than three. It also made the
model stop answering. Of the 205 pages both runs read, **21 came back empty under
the strict prompt that the same model had answered with text under the original**
— and one more came back as 24 consecutive lines of `[...]`, which is the refusal
marker used as exactly the line filler the prompt forbids. "Answered with text"
is as far as that measurement reaches, and the distance between it and "read" is
the whole lesson below.

An empty page is a success by every signal the runner has: HTTP 200, no
exception, both files on disk, and `is_complete` will skip it for ever. The
column exists precisely because nothing else moves — and the column's own
explanation was still the gateway's ("the segmenter found no lines"), which for a
chat completion is not merely wrong but points the reader at a component that is
not in the path.

So the number was read the wrong way round, including by me when I first reported
it. Prompt *and* thinking level had changed between the two runs, so the run did
not isolate the cause; the discriminating run — the same 33 keys, original
prompt, thinking held at `low` — took two minutes and a few cents, and is the
reason lesson 10 exists at all: it needed the keys.

**The prompt was the cause: 26 of the 28 empty pages come back with text.** And
reading those 28 one by one is what the number was actually worth:

| what is on the page | pages |
|---|---:|
| a real transcription the strict prompt lost | **2** |
| a stamp or an archival note — text, but not the letter's hand | 3 |
| **prose about a blank page** ("Vakatseite", show-through, a watermark) | 21 |
| empty under both prompts | 2 |

Twenty-one of twenty-eight were blank versos, and on a blank page **nothing is
the correct transcription**. The strict prompt was right; the other one wrote
museum-label prose into the transcription field, and `page_quality` counted it as
a reading. The `empty` column looked like a 9 % failure because the comparison
was against a run that never left a page unanswered.

That inverts which run is damaged. Every run made with the original prompt
carries, for each blank page, an invented *Befund* where the transcription should
be — publish that and it is attributed fabrication. The silence was the feature.

What is genuinely lost is 5 pages of 28, and it has a shape: the strict prompt
also keeps quiet where something *is* written that is not the letter's hand — two
written pages and three library stamps or registry notes. That is a sentence
missing from the prompt, not a model failure: the prompt says at length what not
to write and never says that stamps, foliation and later hands are part of the
page.

Two things this cost me, both worth saying plainly:

- **I reported "21 pages the model read with text" before knowing what that text
  was.** It was accurate about the files and wrong about the thing anybody would
  conclude from it. A character count is not evidence that a page was read.
- **The cheap instrument was not the image.** The report says "look at the images
  before reading anything into the number", and that is good advice for 28 pages
  and useless for 2 800. What settled it was reading the *same* pages under the
  other prompt — a comparison the stack already had a tool for.

## 12. A column can hide a different failure than the one it names

`gemini-300-strict` reported four pages as `cut off`. The column's own
explanation — in this repository, written by me the day before — said a longer
ceiling fixes those, delete the results and re-run. Reading them showed that
**three carried no transcription at all**:

```
- e - n - k - t !
LOOK AT WORD 3:
g - e - s - c - h - e - n - k - t !
Wait, does it have 'sch'?
…
Entry stroke, downstroke, loop up to the right... THAT'S A 'V'!
```

The model's deliberation in the content channel. For those pages the advice was
exactly inverted: a bigger budget buys more arguing. And every signal the
pipeline has said they were fine — not `empty`, not `repetitive` (every line
different, longest character run 3), not `short` (816–928 characters). They were
visible *only* because the arguing happened to run into a ceiling. A page that
argues for four hundred characters and stops cleanly would have gone into the
edition as a transcription of Laßberg's hand.

Three things worth separating:

- **The find came from validating something else.** I was checking whether a
  prompt variant recovered five lost pages. The variant's readings of the
  *other* pages were what made the old ones legible as nonsense. Comparing two
  readings of the same page keeps paying for itself in ways no single run's
  report can: the report has no column for "this is not a transcription", and
  the comparison does not need one.
- **A detector with no measured negative is a guess.** The cue threshold (0.2)
  sits between 0.38–0.70 on the three leaked pages and 0.00 on clean readings of
  the same pages. Both sides are real pages of this corpus, not invented text,
  which is why the margin can be stated rather than hoped for.
- **Where the column goes matters as much as that it exists.** `arguing` sits
  next to `empty` and `looped` in the same table, and its pages join
  `flagged_keys` — so the response is a selection (#616) rather than a sentence
  in a document somebody has to read.

**The generalisation:** when a column is not zero, read a page it names before
acting on what the column is called. The name encodes a guess about the
mechanism, and `cut off` was a guess that held for one page of four.

## 13. The same mistake, one hour later, in my own fix for it

§8 of this document is "a configuration nobody set is not a default that works".
§10 is "a count is a complaint; only the keys are a task". An hour after writing
the detector for pages that argue with themselves, I asked the tool how many
pages of the 300-page run were affected and got back:

```
"per_model": [{"model": "gemini-3.8-flash", "empty": 28,
               "repetitive": 1, "truncated": 4, "flagged": 33}]
```

No `deliberated`. I had added the verdict to the classifier, to the report table
and to the key lists, and left the MCP reply listing the three counts it was born
with. The reply did not say "I do not know"; it said nothing, which reads as
nothing found — against a run I was about to publish, and the second time in two
days that a number which was never computed presented itself as a measurement
(§7: a mechanism that keeps a log is not a mechanism to search for).

What the field-per-defect shape made inevitable: every new verdict needs three
edits in three files and the one that is forgotten is silent. So the reply now
carries the classifier's own `verdicts` table, which gains a key when
`page_quality` gains a verdict, and the list of verdicts lives in one place —
`page_quality.VERDICTS` and `DEFECTS` — instead of in a comment on a dataclass
field that nothing could check itself against. A test walks `DEFECTS` and
requires each to have somewhere in the reply to be counted.

**The generalisation, and it is the uncomfortable one:** writing a rule down,
even writing it down well, does not make the next instance of it visible. Both
times what exposed the gap was asking a concrete question of real data and
noticing that the answer was shaped wrong — not a review of the diff, which I
had done, and not the lesson, which I had written. The check that works is
reading the answer with the question still in hand.

## 14. "Deployed" is a claim about a process, not about a checkout

Three merges in a row this morning were followed by "pull und restart sind
durch", and the third time the reply still had the shape it had before any of
them. Everything before that had worked, which is the part worth understanding:
`start_batch` runs the stack as a **subprocess**, and a subprocess reads the
files on disk, so a pull alone was enough for a new prompt, a new flag, a new
classifier used by a batch. `batch_report` was the first tool this session used
that computes *in the server process* — and that process had been serving code
it loaded hours earlier.

From the reply alone, three states were indistinguishable: the pull did not
land, the restart did not happen, and there is nothing to find. The last one is
the dangerous reading, and it was the one available at the moment somebody could
have published a corpus on the strength of it.

What makes it decidable is a distinction the obvious implementation gets wrong.
`provenance.git_commit()` already existed and reads the checkout — which after a
pull without a restart reports the **new** commit while the old code answers. A
reply stamped with that is not merely uninformative, it is a false
reassurance. So the stamp has two halves:

- `loaded` — fixed when the module was **imported**, so it is the code that is
  actually answering. Read from `.git` directly rather than through a
  subprocess, because a module-level call has to be cheap enough for every CLI
  invocation to pay it.
- `checkout` — read now. `stale` is the comparison, and it is `null` when either
  half is unknown, because "cannot tell" and "up to date" are different claims
  and collapsing them is exactly how this was missed.

**The generalisation:** a long-lived process is a cache of your source code, and
nothing in a reply tells you so unless the reply says which source it came
from. Any answer that is computed rather than read from a file should carry the
version of the thing that computed it.

## 15. I compared runs all day without measuring the noise floor

Every claim I made on 11 October rests on a difference between two runs: the
prompt silences pages, the variant recovers four of five, 22 of 22 blank pages
stay blank, the median disagreement is 6.6 %. Not once did I ask what two
*identical* runs differ by. The API is sampled, not deterministic. I knew that
and did not act on it.

Measured, finally, on the 25 flagged pages — same prompt, same thinking level,
second attempt: **3 of them came back with text where the first run answered
nothing.** One is a complete letter, 978 characters, Justizrat Zellweger from
Trogen, 27 April 1831. That page had been empty in **four** previous runs,
including one under a different prompt, and reads itself through on the fifth.

So two things I said this morning were wrong, and in the direction that matters:

- *"22 of 22 blank pages stay blank"* was the control of **one** attempt, and
  `Winterthur__310-MsBRH_513-91-131` was among those 22 — filed as a blank
  verso with a letter on it.
- *"21 of 28 are blank versos"* counted the same page as blank.

What survives: on ordinary pages the variance is small. The 6.6 % median across
240 pages is a real result, because prose is stable. The noise lives exactly at
the edge where a page flips between *I read this* and *I answer nothing*, which
is the edge every interesting number today sat on.

**The remedy is cheaper than the diagnosis.** The report's advice for `empty` is
"look at the images", which scales to 28 pages and not to 2 800. Reading the
same page a second time scales, costs 25 calls rather than 300, and gives a
defensible definition: a page is blank when it is empty in *every* attempt.
Resumability-by-presence actively prevents this — `is_complete` skips the page —
so a second attempt needs a new run name, and the comparison of the two runs is
the measurement.

**The generalisation:** before attributing a difference to the thing you
changed, measure what changes when you change nothing. With a sampled model that
is not a formality; it is the denominator. I had the tool for it (`compare_readings`
over two runs) on the first day and used it only across prompts.

## 16. A function that preserves on purpose is not a function that refreshes

A page published into the edition held two contradictory verdicts for itself:
`merged_from.verdict: ok` beside `quality.verdict: repetitive`. I said a second
`merge-runs` would smooth it over. It would not have: `merge_runs` copies the
attempt's payload **deliberately**, so that `settings`, `timing_ms` and
`gateway_version` stay true to the call — and a stale verdict rides along on
exactly that guarantee. Re-running it would have written the same wrong number a
second time, and I would have reported the fix as done.

What saved it was reading the artifact rather than the log: the two fields sit
four lines apart in the published JSON and disagree.

The repair is a distinction worth keeping: in a record of a machine reading,
some fields are **facts about the call** — what was asked, when, by which
version — and some are **judgements our code derives from the answer**. The
first must never be rewritten. The second must never be stale. `quality` was in
the wrong group.

**The generalisation:** before claiming that re-running something fixes it, name
the line that would change. I claimed a remedy from the shape of the problem
rather than from the code, and the code was built to do the opposite.

## 17. Two non-observations in one verification

Both looked like findings, and neither was.

**276 of 300 files.** The first count of the published tree came back 276 `.txt`
and 277 `.json`, which reads as an incomplete push into somebody else's
edition. Git quotes paths containing non-ASCII — and the Laßberg keys are full
of `Zürrich` and `Mülinen` — so those lines end in `"` and my `grep '\.txt$'`
skipped them. `-c core.quotePath=false` gives 300 and 300. A count that depends
on how a tool *displays* a name is not a count of the thing.

**"forced update".** Fetching the republished branch printed
`+ 794ed54...5eab78c (forced update)`, which on a shared pull-request branch
would mean somebody rewrote history under the reviewers. In a **shallow** clone
that message appears for an ordinary fast-forward too, because ancestry cannot
be proved past the shallow boundary. `git fetch --depth 12` then
`merge-base --is-ancestor` settles it: appended, not rewritten.

Both belong to the same family as §6 and §7 of this document, and the family
keeps growing: the cheapest way to turn a non-observation into a finding is to
accept a tool's output as an answer to the question you had in mind, rather than
to the question the tool was actually asked.

## 18. My own remedy from §15 had the right shape and the wrong axis

§15 ends with a definition I was pleased with: *a page is blank when it is empty
in every attempt*. It is wrong, and it is wrong in the one direction that
matters — it measures the variance of **one reader** and reports it as a property
of the **page**.

Measured on 13 October, on the 19 pages the published corpus still had empty:

| | Pages recovered |
|---|---:|
| A fourth attempt, same reader, same prompt, same thinking level | **0 of 19** |
| A second *reader* — two local engines from other families, one of them with line segmentation | **1 of 19** |

The yield curve over four attempts of the same reader is 3 → 2 → 0. So the
two-attempt policy survives, and "try again" is exhausted as a lever. The page
that came back is `blb lassberg__K 2911,93__page_0001`: both local engines return
**24 lines**, 14 of the 24 agree, they agree on the shelfmark in line 1, on `67`
in line 2, on a salutation in line 3, and both end the last line with a
hyphen — the text runs on. Neither reading is usable as a transcription. That was
never the question; the question was whether there is ink on the leaf.

The same measurement produced its own negative control, which is what makes it a
measurement rather than a hope. On five of the nineteen, both engines also
returned something, and all three signals say it is noise over a nearly blank
page: **zero** shared words of five characters or more across those five pages,
each engine repeating its own phrases on different leaves
(`haußhaltung zu Buchstetten verschicken`, verbatim, twice), and the engine
trained on the 16th–17th century dating a Laßberg leaf to `1655`. Agreement
between independent readers is evidence; output from independent readers is not.

**The generalisation:** repeating a measurement with the same instrument
estimates that instrument's noise. It cannot distinguish a property of the object
from a bias of the instrument — for that the instrument has to change. §15 got
"re-read rather than look at images" right and then varied the one parameter
that could not answer the question.

A second thing this settles, and it is not about blankness: the prompt tells the
model, in so many words, that an empty answer asserts the leaf is blank and that
a page it cannot read is `[unlesbar]`. Four times it took exactly the route the
sentence forbids. **A sentence in a prompt is not an enforcement mechanism** —
which is the same lesson as §11, now with the instruction present rather than
missing.

## 19. A verdict is currency inside one reader and not between two

Having found the written page, the obvious move was to merge the gateway run into
the published corpus and let the ranking pick the better reading. I ran it as a
dry run first, and the result was not what I expected:

```
merge_runs(["gemini-300-final", "gateway-final-empty-19"]) →
pages: 338    improved: []    38 page(s) appear only in a later run
```

300 + 38 = 338. **A page is identified by (model, key), not by key alone**, so
the two engines' readings never met the Gemini pages at all, and `improved` is
empty because nothing was compared.

That is the safe side of the mistake, and it took the dry run to see which side I
was on. Had the merge keyed on the page alone, the five **noise** pages would
have won: their verdict is `short` (rank 2) and the Gemini pages' is `empty`
(rank 1), so `orchecker classica` would have been published as a transcription in
somebody else's edition. The ranking was built to keep `repetitive` and
`deliberation` below `empty`, on the grounds that nothing is better than
something invented. A *short, fluent* invention by a foreign model walks straight
past that guard, because the guard reads the verdict and the verdict was computed
per reading, not per reader.

So `MERGE_RANK` compares attempts of one reader, and between readers a verdict is
not a common currency: `short` from a 16th-century line model and `short` from
the engine that read the corpus are not the same claim. Nothing in the merge says
so, and the one line in its report that pointed at the situation —
"38 page(s) appear only in a later run … also what a mistyped run name looks
like" — was written for a different mistake and fired correctly for this one.

**What I will not do:** reach for the ranking to import a cross-engine reading.
Taking one page's reading from another model is an editorial act, it has to name
the model on that page, and it belongs in a decision by the edition's maintainer
rather than in a merge that looks like bookkeeping.

## 20. I made a rule out of the one signature the report let me see

`export-hf` refused 43 pages for a geometry mismatch and printed five names, of
which exactly **one** was a geometry case. From that single pair — XML
3333×4391, image 1420×1781 — I wrote that the share holds "a smaller, differently
cropped derivative", which was right, and then, once the report named eight
pairs, that the most frequent factor 2.2548 is "300 against 133 dpi, a systematic
relationship between two derivative pipelines". That second claim was a rule
drawn from one number.

The full table has **21 pairs** and seven factors: 1.58, 1.62, 1.99, 2.10, 2.26,
2.63, 3.31. There is no single relationship to name. 2.2548 is the most frequent
because six pages share it, not because anything normalises to it — nine archives
delivered their own derivatives, which is what a correspondence held in nine
houses looks like.

This is the third time in two weeks, with the same shape each time: three pages
became "8192 is a generous ceiling", one prompt comparison became a prompt
effect (§15), and now one size pair became a dpi ratio. **The sample that shows
me a pattern is not the sample that establishes it** — and in each case the full
data was one command away, which is the part that stings.

Two things the complete table gave that the single pair could not, and neither is
a detail:

- **1420×1781 is a fixed frame.** Sixteen pages, three different XML heights, one
  identical image size. A derivative that maps pages of different heights onto
  the same format is not scaled per page — arithmetic: that page at width 1420
  would be 1858 high, not 1781. Unrepairable by construction, and I would have
  called it "a smaller derivative" forever.
- **Two pages are not the same picture at all.** `1566×2639 → 1566×1325` has
  identical width and half the height — a scanned opening against one half-leaf.
  `2479×3508 → 2968×2553` is portrait against landscape, and not a rotation
  (rotated would be 3508×2479). For those, "which scan is authoritative" is the
  wrong question; the question is which photograph shows the page.

## 21. A fixture can encode a policy, and then the policy changes

`test_a_mismatched_page_is_refused_not_rescaled` used 2938×3638 against
1469×1819 — an exact halving — to prove that a page whose XML disagrees with its
image is refused rather than rescaled. It was a good test of the policy it was
written for.

Since the measurement above, that pair is the **repairable** case: one factor on
both axes is the same scan at two resolutions, so the coordinates are divided by
it and the page is exported saying so in its own first comment. The fixture had
quietly picked the one shape of mismatch that later turned out to be benign.

So the test now uses 3333×4362 against 1420×1781, the fixed frame, and both
versions stand in its docstring. The rule I am taking from it: when a test's
fixture is an *example* of a category, say in the test which property of the
example matters. "A mismatched page" was a category with two members, and nobody
had noticed it needed splitting until 43 real pages said so.

## 22. "Is it my commit" is the wrong question about a deployment

§14 ends with the fix: every report carries the commit the answering process
actually holds, so "deployed" stops being a claim about a process and becomes a
readable fact. Four days later that fact arrived and I nearly read it as a
failure.

The server reported `loaded: 3c360ccd`. My merge was `a94d80d`. Those are not
equal, and the deployment was perfectly correct — `main` had taken two more pull
requests from other sessions in between, and the pull brought all of it. An
equality check would have reported a healthy state as broken and held back a
27-minute run on the strength of it.

**The question is containment, not identity:**

```bash
git merge-base --is-ancestor <my commit> <loaded>
```

Two neighbouring cases where ancestry is also the only usable answer, and this
session hit both:

- **After a squash merge, my branch commit is not an ancestor of `main`.** The
  squash is a new commit with the same content, so a `--force-with-lease` that
  wants to be sure nothing is lost has to compare *trees* —
  `git diff --stat <branch> origin/main -- <the files it touched>` — and an
  empty diff is the proof. Checking history there says "unmerged commits" about
  a branch whose every line is already in `main`.
- **In a shallow clone, ancestry cannot be computed at all** past the boundary,
  which is why `git fetch` prints `(forced update)` for an ordinary
  fast-forward (§17). Fetching a little more depth turns an unanswerable
  question into an answerable one.

**The generalisation**, and it is the same shape as §5 and the 27-against-43
mix-up below: a verification compares two things, and its worth depends entirely
on whether they are the two things the question is about. `loaded == my merge`
and `loaded contains my merge` look alike and differ in exactly the case that
matters — the ordinary one, where other people are working in the same
repository.

## 23. Two numbers from one printout, and I used the wrong one for days

The export's report prints both `unbestimmt: 27` — pages whose hand no dateline
names — and `geometry: 43`, pages whose transcription does not match their scan.
I carried "27 geometry rejections" in my own task list from one reading of that
output until the day I went to diagnose them, and the number was simply the
wrong column's.

Nothing in between could have caught it. There was no second source to disagree
with, the figure appeared in my notes and not in any file, and 27 is a plausible
count for either. It survived because it was never used for anything until it
was.

The family is §5 (*files are not pages*) one level up: not a file mistaken for a
page, but one column's answer given to another column's question. What makes it
worth its own entry is where the error lived — not in the code and not in the
report, both of which were right, but in the sentence I wrote down about them.
**A number copied out of a report loses the column header that made it true.**
When it matters later, it has to be re-read from the report rather than
remembered.

## 24. Eine Prüfung, die niemand prüft, verfällt — zweimal am selben Tag

Beim Abschluss der Sicherheits-Epic #571 standen alle 17 Haken. Keiner war
falsch. Beide Nachträge, die trotzdem nötig waren, haben dieselbe Form, und es
ist nicht die Form der Befunde selbst.

**SEC-18 (#641).** Jeder der 17 Belege pinnt eine *fest verdrahtete Liste*:
`test_ah_105` nennt neun Befehle namentlich, SEC-6 prüft `atr_status`, SEC-14 die
Aufrufstellen vom Oktober. Seit dem Audit sind zehn Befehle dazugekommen. Alle
zehn sind gegatet — aus Disziplin, nicht durch Konstruktion; die Suite wäre
genauso grün gewesen, wenn keiner es wäre. An einer Stelle ist die Disziplin
tatsächlich gerissen: `agent_a/training_client.py`, ein zweiter HTTP-Client auf
dasselbe Gateway, nach dem Audit geschrieben, mit dem umgekehrten Vertrag von
`atr_status` auf *beiden* Punkten, die dem Audit wichtig waren — `job_id` roh im
URL-Pfad und die interne VPN-Adresse in Meldungen, die bot.py in den Kanal
postet. Alle Tests blieben grün.

**SEC-19 (#646).** `config.py` behauptete über `REQUIRED_DISCORD_ROLE_ID`, der
Bot „warns at startup". Tat er nicht. Die einzige Warnung sass in `_authorised`
und lief *pro Befehlsaufruf* — wer den Bot in einen offenen Server stellte,
bekam kein Signal, bis irgendwann ein Mitglied einen gegateten Befehl aufrief
und jemand ins Log sah. Die Epic hat also auf eine Absicherung gezeigt, die es
nicht gab, und zwar an der Stelle, an der ich selbst „liegt beim Operator"
geschrieben hatte.

Das ist dieselbe Familie wie §11, §23 und wie der Befund, der sich durch dieses
Projekt zieht — ein Wert, der mehr behauptet, als gemessen wurde (#119, #595,
#631, #633) —, nur eine Ebene höher: eine **Prüfung**, die mehr behauptet, als
sie prüft. Und es ist der Grund, warum der Startbericht aus SEC-19 dreiwertig
ist: die Discord-seitigen Befehlsberechtigungen wirken *vor* dem Code und sind
aus dem Prozess nicht lesbar, also sind sie `[?]` und nicht `[ok]`. Ein
Lagebericht ist genau die Stelle, an der der alte Fehler zurückkäme — eine
saubere Liste ist, was ein Bericht gerne produziert.

**Was daraus folgt:** die Regel gehört dorthin, wo sie bei Konstruktion gilt,
nicht in eine Liste von Namen. Konkret: ein Abdeckungstest, der *jeden*
registrierten Slash-Befehl aufzählt und für jeden ein Gate oder einen Eintrag
auf `bot.OPEN_COMMANDS` mit Begründung verlangt; eine Regex, die beide Clients
importieren, statt zweier Kopien, die driften; und eine Sperre in `_get`/`_post`
statt eines Validators, an den jede Aufrufstelle denken muss.

## 25. Ein Platzhalter, der vor dem Einsatz korrigiert werden muss, wird nicht installiert

`deploy/systemd/hardening.conf` lag seit dem Audit im Repo, und #567 führte
Punkt 2 als „✅ (repo side)". Auf tei war es nicht installiert. Der Grund stand
in der Datei:

```ini
# CHANGEME: the bot's writable dirs. Example for a /srv checkout; edit for tei.
ReadWritePaths=/srv/agentic_historian/agentic_historian/data
```

Das Checkout liegt unter `/home/dh`. Unverändert installiert macht die Datei
`data/` schreibgeschützt, und mit `ProtectSystem=strict` ist das kein Degradieren,
sondern ein Scheitern beim ersten Schreibvorgang. Die Reibung hat genau die
Massnahme verhindert, die sie absichern sollte.

Dieselbe Form bei `dh-bot.env.example`: der Kopf sagte, nicht-geheime
Einstellungen hätten „defaults in config.py — set one here only to override it".
Für einen Schwellwert stimmt das. Für `ENABLE_ATR_WATCH`, `ATR_WATCH_CHANNEL_ID`
und `CREDENTIAL_WATCH_CHANNEL_ID` nicht: dort ist der Standard *aus*, ihn
weglassen behält keinen Standard, sondern lässt eine Funktion nicht laufen — und
eine Funktion, die nicht läuft, sieht aus wie eine kaputte. Wer die Datei aus dem
Beispiel füllte, bekam einen Bot mit zwei still nicht laufenden Watchern.

**Was daraus folgt:** eine Vorlage, deren Werte vom Host abhängen, soll die Werte
*ableiten* statt sie als Beispiel zu zeigen. `deploy/systemd/writable_paths.py`
liest sie aus der laufenden Konfiguration. Und beim Bauen davon fiel der Fall
auf, der schlimmer gewesen wäre als kein Platzhalter: mit dem System-Python ist
`sys.prefix` gleich `/usr`, die ausgegebene Zeile also `ReadWritePaths=/usr` —
Schreibrecht auf das ganze System, und es sähe wie Härtung aus. Ein Pfad, der
sich nicht messen lässt, wird benannt und nicht geraten.

## 26. Zwei Diagnosefehler, die mir dieselbe Stunde zweimal gekostet haben

Beim Einrichten des Bots meldete der Startbericht die Rollen als gesetzt,
während keine der Dateien, die ich geprüft hatte, sie enthielt. Ich habe daraus
einen Widerspruch gemacht und zwei Hypothesen gebaut — `export`-Präfixe, die
`python-dotenv` toleriert, und eine Injektion durch systemd. Beide falsch.

**Fehler eins: das Messwerkzeug hatte ein blindes Feld, das ich selbst gebaut
hatte.** Meine Dateiliste liess `/home/dh/agentic_historian/.env` aus, weil ein
früheres `ls` sie als nicht existent gemeldet hatte — *vor* dem `cat >>`, das
meine eigene Anleitung dort hatte schreiben lassen. Die Datei war die Antwort.

**Fehler zwei: `/proc/<pid>/environ` kann die Frage nicht beantworten.** Es zeigt
die Umgebung, mit der der Prozess *gestartet* wurde; was `load_dotenv` zur
Laufzeit in `os.environ` legt, steht dort nicht. Ich habe den Befehl als
Unterscheidung dreier Fälle verkauft, obwohl er nur einen ausschliessen konnte.

Die richtige Frage war die ganze Zeit verfügbar: `config.ENV_SOURCE` führt Buch,
„welcher Key kam aus welcher Datei", und wurde für genau diesen Zweck gebaut.

**Was daraus folgt:** wenn Messung und System sich widersprechen, erst das
Messwerkzeug verdächtigen — und vor dem Bau eines eigenen prüfen, ob das System
die Frage schon beantwortet. Die Folge reicht weiter als die verlorene Zeit: die
„würden beim Umschalten wegfallen"-Liste von 37 Keys kam aus demselben `grep`
und ist aus demselben Grund unzuverlässig. Sie kann zu kurz sein, nie zu lang —
die Warnung „so nicht umschalten" galt also, die Vollständigkeit nie.

## 27. Atomar ist nicht korrekt

R3 (#393) hat die Mutatoren des Knowledge Hub in einen kritischen Abschnitt
gelegt und den Konstruktor übersehen. `_load` endete in einem bedingungslosen
`self._write(copy.deepcopy(DEFAULTS))`, sobald die Datei fehlte. Fünf Threads
mit je einem Hub auf einem frischen Pfad:

```
persons kept: 49 of 50
blind seeding writes from __init__: 5
```

`write_json_atomic` hat diesen Schreibvorgang atomar gemacht. Das ist eine
andere Eigenschaft als richtig: ein atomarer Schreibvorgang, der nicht weiss,
worüber er schreibt, verliert die Arbeit des anderen Workers genauso. Der Test
aus #393 war deshalb in 5 von 12 Läufen rot — ein echter Fund, kein Flake.

Die lehrreichste der zwölf Rückabwicklungen war **zuerst grün**: derselbe blinde
Schreibvorgang hinter einem `if not self.path.exists()` lässt nur ein Fenster
von Mikrosekunden offen und übersteht damit jeden Zeittest. Ein Test, der fast
immer besteht, ist schlechter als keiner.

**Was daraus folgt:** die Regel prüfen, nicht das Symptom. Der Test verlangt
jetzt, dass der Konstruktor `_write` *gar nicht benutzt* — eine Methode, die die
Datei aus einem Speicherabbild ersetzt, ohne sie gelesen zu haben, ist das Eine,
was er nicht darf. Dieselbe Lehre in #644, von der anderen Seite: dort hing ein
Test an 200 × 10 ms Wartezeit für zwei Heartbeats im 20-ms-Takt und war unter
Last in etwa jedem zehnten Lauf rot — mit der Meldung „the heartbeat never
fired", während er einmal geschlagen hatte. Er wartet jetzt auf ein `Event`,
hängt also davon ab, *dass* geschlagen wird, und nicht davon, wie schnell.
## 28. My control measured the instrument, and that is why I did not publish a wrong answer

The question was whether the line polygons of the 13 rescaled pages sit on the
ink after being divided by a measured factor. The metric: what share of a page's
ink falls inside the line polygons. The control, chosen to calibrate it: 25
pages whose polygons were never touched and are correct **by construction** —
they are in the export because their XML and their image already agreed on size.

The rescaled 13 scored a median of **0.830**. The control scored **0.166**.

Taken at face value that reads as a triumph, and it is nothing of the kind. The
control's polygons cannot be misplaced, so a score of 0.166 is not a statement
about placement at all — it says the metric is dominated by something else. It
was: Otsu binarisation on an archival scan with a dark surround counts the
surround as ink, and the surround lies outside every line. One page carried
**0.195 ink by area**, where a handwritten page carries 0.02–0.04; with a
border-robust mask the same page measured 0.056, so 72 % of its "ink" was the
edge of the scan.

Without the control I would have reported 0.830 as proof. The answer would even
have been **right** — the measurement that finally settled it agrees — and it
would have been right for a reason I had not established. That is the failure
mode this document keeps finding in a dozen shapes, and it is worse than being
wrong, because nothing afterwards contradicts it.

**Two sharper rules came out of the same hour.**

*An argmax at the edge of the swept range is not an optimum.* One page reported
its best fit at scale 1.10 with the maximum shift in both axes — all three
parameters pinned to the boundary, for a gain of 0.246 → 0.264. A real optimum
sits inside the range and pays much more; a boundary argmax with a small gain is
a **gradient pointing out of the frame**, here towards the dark border. Widening
the range and cleaning the mask put the peak at 1.00 with 0.856, falling away on
both sides.

*A confound can be indistinguishable from the defect you fear.* Those three low
scores were all Basel pages, and "three pages from one holding are misaligned"
is an entirely plausible defect — plausible enough that I was ready to pull them
out of a published dataset. The thing that separated the two readings was not
more thought about placement but **one column I had almost left out**: the ink
fraction of the whole page. 0.177–0.245 against 0.016–0.043 said *image*, not
*geometry*, in one glance.

**The generalisation:** a control is not there to make the result look solid. It
is there to tell you what your instrument is actually responding to — and when
it disagrees with the thing you are measuring, believe the control first,
because it is the half of the experiment whose answer you already know.

## 29. Somebody else's contract is in their code, and mine was in a constant

The Gemini readings had to become a dataset, and the project refuses to write a
second converter — so the question was whether `pagexml-hf`, a **PAGE XML**
converter, can be fed text that has no geometry at all. I could have guessed from
its documentation (it says nothing about this) or from its mode names. Instead I
read the twelve lines that matter:

```python
def _get_text_equiv(self, element):
    unicode_el = element.find("pc:TextEquiv/pc:Unicode", self.namespace)
    if unicode_el is not None and unicode_el.text:
        return unicode_el.text.strip()
    lines = element.findall("pc:TextLine", self.namespace)
    if not lines:
        return ""
```

A region's text is its **own** `TextEquiv`, and its lines are only the fallback.
That single `if` is the whole design of the export: one region, a page rectangle,
no lines at all. Reading it took two minutes and decided a module.

Then I wrote in the docstring that `--mode line` "yields nothing" from such a
tree — which was me reasoning about code I had just read instead of running it.
It does not yield nothing: it **dies**, `ZeroDivisionError`, computing statistics
over zero lines. I only know that because the round-trip through the real
converter was cheap enough to run (`pip install` the clone into a scratch venv,
three modes, four minutes), and the docstring now says what the run said. The
measurement also handed me `--mode region`, which I had not thought to ask about
and which works, producing one row per page whose polygon is the page.

**The second half, and it is the sharper one.** The upload runs through an MCP
tool that takes `tree`, `repo_id`, `dry_run` — and the mode was
`UPLOAD_MODE = "raw_xml"`, a module constant. So the tree I was about to build
*could not be uploaded correctly by the tool I had*, and the failure would not
have been an error: the synthesised documents would have landed in an
`xml_content` column beside fourteen datasets of real Transkribus output and
looked like more of the same. The fix was not a new parameter on four call sites.
It was to put the mode in the tree — `export.json` beside the pages, read by the
upload — because the mode is a property of the data and not of whoever types the
command. A tree without a manifest reads as `raw_xml`, so every older upload is
unchanged, and the upload plan now prints one line that no caller can forget:
`text      : MACHINE READING, uncorrected`.

**The generalisation:** when a value has to be right for data that will outlive
the session, store it *with* the data. A constant in my module and a parameter on
somebody's tool are both things a future caller has to remember; a manifest in
the directory is a thing they cannot miss.

## 30. Der Walker starb an einem Fehler, den er noch nie gesehen hatte — und der Umweg war der kürzere Weg

22. Oktober. Der Trockenlauf über `dav:digitalisate` begann die volle Begehung
des Shares, weil die Listing-Cache (48 h) abgelaufen war, und starb nach zwölf
Minuten und 520 Ordnern am 521.: `St. Gallen Kantonsbibliothek/lassberg-letter-1648`
antwortete 500, dann 404. Der Walker *hat* eine Regel für unlesbare Ordner —
überspringen, aufzeichnen, die Liste nicht cachen; sie ist am 23. September
gegen genau diesen Verlust gebaut worden. Sie griff nicht, weil sie nur die
Fehler kannte, die der Code damals gesehen hatte: Timeout, Transportfehler,
Nicht-XML. Ein 404 kam als webdav4s eigene `ResourceNotFound` an, fiel durch
beide Netze und endete den Lauf wie vor der Regel.

**Die Lehre ist nicht „fange auch 404".** Sie ist: eine Schutzregel, die auf
einer Liste bekannter Fehler steht, ist gegen den nächsten unbekannten Fehler
nichts wert. Der Fix sortiert nach dem, was ein Fehler *bedeutet* — hat er einen
HTTP-Status? 404: der Ordner ist nicht da, sofort überspringen, nicht dreimal
fragen. Ein anderer Status: der Moment des Servers, wiederholen wie ein Timeout.
Kein Status: ein Fehler im Client, der propagieren muss. Die Wurzel bleibt
fatal. Fünf Tests, einer pro Fall.

**Und der zweite Teil:** ich wollte die Begehung einfach neu starten. Zwanzig
Minuten, und bei einem dauerhaft kaputten Ordner wieder dasselbe Ende. Stattdessen
lag jede Seite, die der Lauf lesen sollte, im Kalt-Tier des Seiten-Caches —
`ATR_PAGE_CACHE_ARCHIVE`, 6783 Arbeitskopien unter dem Share-Pfad, also
**derselbe Schlüssel**, und der 300er-Lauf hatte 125 seiner Seiten genau von
dort gelesen. Der Trockenlauf gegen das Archiv: Sekunden, 35 von 35 aufgelöst,
nichts fehlend. Ich musste es per CLI tun, weil `_roots()` der MCP das Archiv
nicht kannte (#686, seit demselben Abend). Bevor man einen Lauf wiederholt, der
zwanzig Minuten kostet, lohnt die Frage, ob die Daten nicht schon unter einem
anderen Namen auf der Platte liegen.

## 31. Jeder Job läuft auf dem Checkout, und der Checkout gehört nicht mir

Dreimal an einem Tag dieselbe Überraschung in drei Formen.

Erstens: `export-run-hf` war auf tei „invalid choice", obwohl #682 gemerged war.
Meine Hypothese war der Self-Updater; `git reflog` sagte `checkout: moving from
main to ruemlang-test` — ein anderer Strang hatte den Produktions-Checkout
umgeschaltet, und **jeder MCP-Job ist ein Unterprozess auf diesem Checkout**,
läuft also auf diesem Branch, bis jemand zurückschaltet. Der Diff zwischen den
Branches für den Batch-Code war leer, darum lief der Gemini-Lauf trotzdem
richtig; der Export existierte dort nicht.

Zweitens: der Arbeitsbaum, den ich als Ausweg anlegte (`/home/dh/ah-main`),
hielt `main` — und damit konnte der Produktions-Checkout nicht mehr auf `main`
wechseln (`fatal: 'main' is already used by worktree`). Git lässt einen Branch
nicht zweimal auschecken; das war vorhersehbar und mir entgangen. Dazu hatten
die Datenwurzeln im Arbeitsbaum eine leere Welt, weil `VLM_TEST_ROOT` und
`GT_ROOT` *code-relativ* vorbelegt sind, und `AH_ENV_FILE` ersetzt die ganze
Kette statt sie zu ergänzen — zwei weitere Runden, bis Quelle und Kalt-Tier
explizit übergeben waren.

Drittens, und das war ich selbst: `git pull origin claude/…` im Arbeitsbaum zog
den lokalen `main` auf meine Branch-Commits. Nach dem Squash-Merge war
`main` damit um zwei Commits abgebogen, die inhaltlich längst drin waren, und
`git pull --ff-only` auf tei verweigerte. Ein `reset --hard origin/main` nach
leerem `git status` hat es gerichtet — aber ich hatte den Zustand hergestellt,
den ich eine Stunde zuvor diagnostiziert hatte.

**Was daraus wurde:** `server_status()` (#690) — Branch, Commit, Arbeitsbäume,
uncommittete Dateien, `loaded` gegen `checkout`, die Env-Datei-Kette, die
Datenwurzeln. Sehen, nicht schalten: das Umschalten des Checkouts, den jeder Job
danach ausführt, bleibt eine Handlung auf der Maschine, und ein Werkzeug hinter
einem Bearer-Token im offenen Netz darf es nicht. Und eine Regel für mich: in
einen Arbeitsbaum wird mit `git fetch` plus `checkout <sha> -- datei` geholt,
nie mit `pull` in einen Branch, der woanders Produktion ist.

## 32. Alles, was durchs Terminal ging, war ein fehlendes Werkzeug — oder eine Entscheidung

Am Abend die Frage: was von diesem Tag lief über Terminalbefehle, obwohl die
Session ein MCP hat? Die Liste war länger als erwartet, und jeder Posten hatte
dieselbe Form — ein Schritt, der *zwischen* zwei Werkzeugen lag, die schon
existierten: `score_ground_truth` schreibt Schlüssel, `start_batch` liest sie,
aber die GT-Dateien dorthin zu bringen war eine `while read … cp`-Schleife.
`export_hf` und `upload_hf` gab es, der Lauf-Export dazwischen nicht. Die
Bewertung lief als Job, ihr 484-zeiliger Bericht war aus der Session nicht
lesbar. Sieben Issues (#685–#691), sieben PRs, am selben Abend gemerged.

Zwei Dinge daran sind die Lektion. **Erstens die Ausschlüsse, mit Begründung:**
nicht alles gehört ins MCP. `git checkout` auf der Produktion ist eine andere
Zugriffsklasse als „Seiten wählen" und bleibt draussen; `nohup` war nur nötig,
weil der Upload per CLI lief; `tail -c 300` einer Lesung war mein Umweg —
`read_pages` konnte das seit je, ich hätte es benutzen sollen. Ein Inventar
ohne die Spalte „warum nicht" wäre eine Liste von Wünschen.

**Zweitens das Muster der Werkzeuge selbst:** jedes nimmt **Namen unter einer
Wurzel**, nie Pfade — `run` unter `VLM_TEST_ROOT`, `name` unter `GT_ROOT`,
`manifest` unter `ATR_RESEARCH_DIR`, `card` unter `docs/`. Die Containment-Regel
von #509 hat sich siebenmal wiederholt, und einmal wurde sie zu einer zweiten
Wurzel (der Forschungsspeicher), deren Manifestzeilen den Mountpunkt einer
*anderen* Maschine nennen und über den Projektordnernamen abgebildet werden. Was
nicht abbildet, wird genannt, nie verschluckt. Und jedes Werkzeug, das etwas
schreibt, hat einen Trockenlauf als Vorgabe, weil ein Baum mit Dateien oder ein
Upload nicht die Stelle ist, an der man das erste Mal hinsieht.

Und eine Kleinigkeit, die eine Viertelstunde gekostet hat: GitHub schliesst
Issues nur auf „Closes", nicht auf „Schliesst". Sieben Issues von Hand, mit
Kommentar.

## 33. Der Vergleich, der zugunsten des Verlierers verzerrt war

Gemini auf dem Eval-Split der HTR-Ground-Truth: 4,8 % Median-CER gegen 14,5 %
des feinjustierten Zeilenmodells, über dieselben 34 lokalisierten Seiten. Die
Zahl ist sauber, und trotzdem war die erste Frage nicht „ist sie richtig",
sondern „in welche Richtung kann sie verzerrt sein". Das Zeilenmodell ist im
September auf der Projekt-GT trainiert worden; die Aufteilung in
`train`/`dev`/`eval` stammt vom 8. Oktober. Ob die 35 Eval-Seiten in Qwens
Trainingsmenge lagen, sagt kein Protokoll hier — ausschliessen lässt es sich
nicht. Gemini hat sie sicher nie gesehen.

Das heisst: **ein Vorsprung des Zeilenmodells wäre damit erklärbar gewesen, der
Rückstand nicht.** Eine mögliche Verzerrung, die den Befund nur stärken kann,
ist kein Grund, ihn zurückzuhalten — aber sie gehört in den Satz daneben, und
sie gehört *vor* die Zahl, nicht als Fussnote dahinter. Was der Vergleich
ausserdem nicht misst: Gemini liest ohne Geometrie, Qwen über die Segmentierung
des Gateways, und ein Segmentierfehler zählt nur bei einem. Die Spannen sagen,
wo der Unterschied sitzt — die beste Qwen-Seite (6,6 %) ist besser als Geminis
p90, verloren wird auf den schweren Händen, wo Qwen bis 37 % geht und Gemini bei
17 % bleibt.

## 34. Der Text lag öffentlich bereit, und erst eine Messung machte ihn zu Ground Truth

Am 23. Oktober die Frage, ob die Badische Landesbibliothek neue Bilder zu
Laßberg hat. Sie hatte mehr als das: ein OAI-Set (`blbknajvl`) mit 426
digitalisierten Objekten und 4305 Seiten, 359 Objekte davon nicht im Share, und
für 401 Objekte einen ALTO-Volltext. Ein Volltext in einer digitalen Sammlung
ist zunächst nichts, und am wahrscheinlichsten ist er eine OCR, die niemand
korrigiert hat. Hätte ich ihn als Ground Truth behandelt, weil er „Volltext"
heisst, wäre jedes Training darauf ein Training auf Maschinenlesung gewesen.

Gemessen gegen die eigenen handkorrigierten Transkribus-Seiten, auf den sechs
Seiten, die beide Seiten haben: **auf fünf zeichengleich, auf der sechsten
2,8 %** auseinander, und zwar dort, wo zwei Menschen verschiedene Konventionen
haben, nicht wo eine Maschine sich verlesen hat. Gemini liegt auf denselben
Seiten bei 7,5 %, Qwen bei 16,5 %. Erst diese Zahl macht aus dem Text Ground
Truth. **Was ein Text ist, Transkription oder Lesung, ist eine Messung und kein
Etikett**, und sechs Seiten reichen dafür, weil eine Maschine auf diesem Material
nirgends in die Nähe von null kommt. Woher die BLB den Text hat, bleibt offen.
Die Antwort ändert nicht, was der Text ist, aber vielleicht, wohin er darf. Bis
dahin ist der Datensatz privat.

Der Anfang kostete eine Runde. Die BLB sperrt Rechenzentrums-Adressen (403 von
nginx), also lief alles auf tei, wo ich keine Shell habe. Die erste
Signaturzuordnung fand nichts, weil Anker-Records ohne Dateien dieselbe
Signatur tragen wie das Objekt mit den Seiten und der erste gelesene gewann.
Jetzt gewinnt der Record mit Seiten, in beiden Skripten und im Modul danach.

## 35. Der Hold-out nach Seiten misst Hände, die das Modell kennt

Der Trainer teilt nach Seiten, wenn ein Datensatz keine `eval_projects` nennt.
Zwei Seiten eines Briefs haben dieselbe Hand, dasselbe Papier, dieselbe Tinte.
Eine Seitenteilung meldet also eine Fehlerrate auf Händen, die im Training
waren, und diese Zahl sieht aus wie ein Hold-out und ist keiner. Deshalb geht
vor dem Upload ein gesätes Zehntel der **Objekte** jedes Projekts nach
`<projekt>_eval`: 40 Objekte, 288 Seiten. Drei Objekte werden erzwungen,
`K 2911,17`, `,107` und `,110`. Es sind die BLB-Objekte im Eval-Split des
HTR-Projekts, auf dem Gemini am 22.10. 4,8 % und Qwen 14,5 % gemessen haben. Ein
Modell, das auf diesem Datensatz trainiert, bleibt damit an dieser Messung
vergleichbar.

Die Lektion steckt im zweiten Lauf. Das Split-Skript lief einmal und tat, was es
sollte. Erst beim Umzug ins MCP (#711, #712) las ich es mit der Frage, was beim
**nächsten** Lauf passiert, und fand zwei Fehler im Baumbau. Ein Rebuild nach
dem Split hätte die Seiten eines Eval-Objekts im Trainingsprojekt vermisst und
sie dort neu geschrieben. Dann wäre dasselbe Objekt auf beiden Seiten gewesen,
ohne dass eine Zahl es zeigt. Und der Rebuild ersetzte `export.json` und löschte
damit den Split-Eintrag. **Ein Skript, das einmal läuft, hat die Fehler, die
erst der zweite Lauf zeigt**, und ein Werkzeug wird genau dafür gebaut, dass es
ein zweites Mal läuft. Jetzt sucht der Build auch auf der Eval-Seite und
schreibt neue Seiten eines gehaltenen Objekts dorthin. Das Manifest wird
zusammengeführt, nicht ersetzt. Ein Split nach einem verzeichneten Upload wird
abgewiesen.

## 36. Ein Drittel der Seiten hatte keinen Text, und ich hatte sie mitgezählt

Der Trockenlauf des Baumbaus sagte 4123 Seiten und rund 15,6 GB. Die Karte nannte
„rund 4100 Seiten", der Trainingsplan rechnete mit 70 000 Zeilen. Gebaut wurden
2700 Seiten: **1419 ALTO-Dateien enthielten keine einzige Zeile**. Das sind
leere Versos, Adressseiten, Umschläge und im Almanach ganze Strecken, die die
Bibliothek nicht transkribiert hat. Der Almanach schrumpfte von 1478 auf 896
Seiten, der Trainingsplan auf die Hälfte.

Gezählt hatte ich Seiten, die auf eine ALTO-Datei **verweisen**. Ein Verweis auf
einen Volltext ist aber keine Zeile Text, und was der Trockenlauf nicht öffnet,
kann er nicht zählen. Die Zahl im Trockenlauf ist eine Obergrenze und muss so
heissen. Die Karte und der Plan stehen jetzt auf den gebauten Zahlen. Ein
Upload-Trockenlauf zählt seither auch die **Objekte** pro Projekt (#710). Das
ist die Grösse, über die ein Hold-out gezogen wird, und dafür war am 24.10. eine
eigene `ls | sed | sort -u`-Schleife nötig.

## 37. „Läuft" in einem Terminal ist nicht „angekommen"

Der Upload von `lassberg-blb-gt` lief am Abend des 24.10. per CLI an, und der
letzte Stand war „Map: 12 %". Am nächsten Nachmittag gab es auf dem Hub keinen
Datensatz. Gefunden hat das kein Blick auf den Upload, sondern eine Prüfung, die
für etwas anderes gebaut war. `train_spec` pinnt jede `revision: null` auf den
Commit des Hubs. Für diesen Datensatz nannte der Hub keinen, und das Werkzeug
hielt an, statt den Trainer `main` lesen zu lassen.

Warum der Upload starb, sagt nichts mehr, weil sein Terminal nicht mehr da ist.
Darum lief der zweite Anlauf als Job (`upload_hf(tree="datasets:…")`, #710): Ein
Job überlebt ein geschlossenes Fenster, und sein Log bleibt lesbar. Seit #711
trägt `upload-hf` einen erfolgreichen Upload in `export.json` ein, und
`hub_state` nennt den Commit. Ein Upload ist erst angekommen, wenn die Hub-Seite
ihn bestätigt.

## 38. Zweimal grün gegen ein Schema, und der Trainer hätte trotzdem abgelehnt

Die drei Trainings-Specs habe ich gegen das `TrainRequest`-Modell des Trainers
validiert, sogar gegen dessen neuesten `main`. Alle drei waren gültig. Die
kraken-Spec nannte als Basis `kraken-german-xix-v1`, ein Modell, das dieser
Trainer selbst trainiert hat. Am 24.10. hatte er genau so eine Basis abgelehnt
(„not a file, a registry id, or a Zenodo reference"), und `kraken_train` kennt
dafür seither `trained:<id>`. Im Schema ist `base_model` ein String. Ein String
besteht jede Validierung, und erst der Trainer entscheidet, ob er ihn kennt.
**Ein Schema prüft die Form einer Anfrage, nicht ob sie angenommen wird.** Das
ist §29 noch einmal, vom anderen Ende: Der Vertrag steht im Code des anderen,
und diesmal stand er auch in meinem eigenen Code, ein Werkzeug weiter. Jetzt
löst `train_spec` `trained:<id>` nach derselben Regel auf und prüft, ob die
Datei auf dem Share liegt, bevor eine GPU gefragt wird (#718).

Zwei Fakten zum Deployment hat an diesem Wochenende nur die Unit-Datei gekannt.
`ProtectSystem=strict` macht für den MCP-Server alles schreibgeschützt, was
nicht unter `ReadWritePaths` steht. Die neue Datensatz-Wurzel auf dem Mount
hätte `split_tree` und `oai_gt_import` beim ersten Schreiben scheitern lassen.
Ein Drop-in behebt das jetzt, und die Vorlage sagt es. Und die
Transkribus-Zugangsdaten stehen in `.env.gpustack` der Produktion. Aus einem
Arbeitsbaum heraus sucht `config.py` neben dem Arbeitsbaum, findet nichts und
meldet „must be set". Im MCP-Server stellt sich das nicht, denn er läuft auf dem
Produktions-Checkout. `server_status` meldet die Zugangsdaten jetzt als
`set`/`unset`.

## 39. Die Fehlermeldung stand über dem Text, den sie beendete

Der briefweise Upload aller Briefe brach nach 23 Briefen ab, bei
`lassberg-letter-1209`, Seite 1 von 12. Die letzten Zeilen des Job-Logs zeigten
genau das und sonst nichts, also einen stillen Stopp. Die Meldung stand **ganz
oben** im Log: „Submitted files already exist for this upload". stdout in eine
Datei ist blockgepuffert, stderr nicht. Was am Ende auf stderr geschrieben
wurde, erreichte die Datei vor allem, was stdout noch im Puffer hatte. Jeder Job
läuft jetzt mit `PYTHONUNBUFFERED=1`, und der Läufer setzt das selbst.

Der Fehler dahinter war einer der Gruppierung. Die Seiten eines Briefs werden
über das ganze Kalt-Tier gesammelt, und 1209 liegt dort in zwei Ordnern. Jede
Seite kam doppelt, und ein Transkribus-Upload nimmt einen Dateinamen nur einmal.
Eine identische Kopie wird jetzt einmal hochgeladen und gezählt. Zwei
*verschiedene* Bilder unter einem Namen sind zwei Kandidaten für eine Seite.
Dann wird der Brief benannt und ausgelassen, denn eine Wahl wäre eine Raterei,
und hochgeladen werden sollte nur, was eindeutig einem Brief gehört.

Gehalten hat das Abbrechen beim ersten Fehler. Der Lauf hielt an, nannte den
Brief und die Möglichkeit eines halben Uploads, und die 23 Briefe davor stehen
mit Upload- und Job-Id im Log. Ein Lauf, der weitergemacht hätte, hätte 101
weitere Briefe hinter einer Meldung hochgeladen, die niemand gesehen hätte.

## 40. Vor dem Lauf gemessen: vier Stopps, die das Runbook nicht kannte — und zwei danach

S2 von #567 (eigener Benutzer, Sandbox, eigene Secrets-Datei) war seit dem 21.10.
als Skript mit Trockenlauf vorbereitet, Runbook in SEPARATION.md, alles im Repo
grün. Am 22.10. habe ich den Host angesehen, bevor Tobias den Lauf startete, mit
den Skripten aus `origin/main` gegen die laufende Maschine. Vier Befunde, jeder
hätte den Lauf gestoppt oder verfälscht, keiner war im Repo zu sehen:

1. **`ATR_PAGE_CACHE_ARCHIVE` stand in den Schreibpfaden.** Auf tei ist das
   ein CIFS-Mount mit `forceuid`: Schritt 0 wäre mit exit 6 stehen geblieben,
   und hätte man den Pfad eingetragen, wäre `chown -R` in Schritt 4 unter
   `set -e` gestorben — nach `useradd`, vor den Drop-ins, also mitten im
   Zustand, den das Skript vermeiden soll. Der Bot *liest* das Archiv nur.
2. **Das env-Beispiel kannte eine von drei Quelldateien.** Mit `AH_ENV_FILE`
   liest der Bot nur noch die neue Datei. Gemessen (`env_diff.py`, zweimal
   `config` importieren, Namen vergleichen): vier Settings wären still auf
   Defaults gefallen, darunter der Mount-Pfad des ATR-Gateways. Nichts davon
   wäre im Log aufgetaucht.
3. **`acl` fehlte**, und der Trockenlauf verschwieg es — die Prüfung war mit
   `DRY = 0` verriegelt. Ein Trockenlauf, der an einer Stelle schweigt, wo der
   echte Lauf stoppt, ist kein Trockenlauf.
4. **Der Checker sagte «Nichts konnte geprüft werden»** bei sechs gemessenen
   Fehlschlägen; die Meldung hing an «nichts erfüllt». Genau die Verwechslung,
   die er verhindern sollte, in seinem eigenen Urteilssatz.

Nach dem Lauf (23.10.) noch zwei: der Checker war 0644 mit Shebang
(`command not found`, und im Switch-Skript still in `|| true`), und er meldete
die Secrets-Datei als Befund — mit genau dem Modus, den das Switch-Skript selbst
installiert (root:agentic_historian 0640, damit der Bot seine Datei liest).

**Was daraus folgt.** Ein Runbook, das noch nie gelaufen ist, beschreibt das
Repo, nicht den Host; der Unterschied zeigt sich nur, wenn man die Skripte
*vor* dem Lauf gegen die Maschine hält — als Messung, nicht als Lektüre. Und
eine Prüfung, die ihren eigenen Sollzustand als Fehler meldet, lehrt den Leser,
sie zu ignorieren (§24, dieselbe Familie). Die Lehre aus §25 hat gehalten: der
Platzhalter war weg, die Pfade kamen aus der Konfiguration — und genau das hat
den CIFS-Pfad hereingebracht, weil «was die Konfiguration schreibt» und «was
die Konfiguration nennt» nicht dasselbe sind.

## 40. Drei Abbrüche, drei Ursachen, und ich habe zweimal die falsche behoben

Der BLB-Datensatz ging vom 24. auf den 26. Oktober viermal an `pagexml-hf`. Die
ersten beiden Läufe endeten mit `No space left on device` bei Seite 1280. Die
Bibliothek lagert etwa das Dreifache des Baums zwischen: den Arrow-Datensatz,
seine gemappte Kopie und die Parquet-Shards. Auf tei's Platte waren 22 GB frei,
gebraucht wurden rund 25 GB. Also zog alles auf den Mount (#722). Der dritte
Lauf baute den Datensatz durch und wurde beim ersten Batch des Mappings per
SIGKILL beendet. Ich schrieb das dem Memory-Mapping auf CIFS zu und legte Arrow
wieder lokal ab (#725). Das ist ein brauchbarer Umbau, aber es war nicht die
Ursache.

Das Kernel-Log sagte `CONSTRAINT_MEMCG`, `oom_memcg=/system.slice/atr-mcp.service`.
**Der Dienst läuft mit `MemoryMax=200M` auf einem Rechner mit 3 GB RAM**, und
in diese 200 MB zählt der Seitencache eines Uploads von 7,8 GB. `pagexml-hf`
selbst hatte 200 MB eigenen Speicher. Der Rest war Cache, der schneller wuchs,
als der Mount ihn abnahm, und den MCP-Server hat es gleich mitgenommen. Am
Nachmittag hatte dieselbe Grenze den lokalen Lauf bis Seite 1280 durchgelassen,
weil die lokale Platte ihren Cache schnell genug zurückschreibt. Deshalb sah die
Grenze nach Dateisystem aus und nicht nach Speicher.

Die Lektion ist die Reihenfolge. **Die billigste Messung vor dem zweiten Fix
wäre das Kernel-Log gewesen**, ein `journalctl -k` nach `oom`. Ich habe stattdessen aus
dem Verlauf eine Ursache abgeleitet, die zum Verlauf passte. `upload-hf` liest
jetzt die Speichergrenze seines eigenen Cgroups. Liegt sie unter 2 GB und ist
der Baum grösser als 1 GB, lehnt es ab und nennt den Befehl für eine Shell auf
dem Host. Der vierte Lauf ging von dort aus.

## 41. Der Bewerter liess genau die Seiten weg, an denen das Modell scheiterte

Am 27. Oktober war das erste der drei Fine-Tunes auf dem BLB-Datensatz fertig:
`kraken-german-xix-blb-v1`, das 19.-Jahrhundert-Modell des Projekts, nur auf den
BLB-Zeilen nachtrainiert. 38 360 Zeilen, 136 Epochen, beste Epoche 126. Der
Trainer meldete 8,7 % CER auf seinen zurückgehaltenen Zeilen. Das ist eine Zahl
pro Zeile auf den Zeilenboxen der Ground Truth. Fehler der Segmentierung kann
sie nicht enthalten.

Gemessen habe ich deshalb ganze Seiten: die `_eval`-Projekte des Datensatzes,
Objekte, die das Modell nie gesehen hat, gelesen über das Gateway mit dessen
Segmentierung, altes und neues Modell im selben Lauf. Dafür fehlte dem ATR-MCP
ein Weg zu den Seiten, `start_batch` und `score_ground_truth` nehmen jetzt
`datasets:<baum>/<projekt>` (#752). Ergebnis, Median und nach Zeichen gewichtet:
Laßbergs Hand 5,3 / 7,1 % gegen 14,5 / 15,9 % vorher, Manuskripte 10,1 / 12,1 %
gegen 20,6 / 21,9 %, Korrespondenten 23,0 / 25,6 % gegen 23,0 / 28,1 %.

**Die erste Zahl für die Korrespondenten war 14,0 %.** `score_ground_truth`
ordnet eine Transkription über den Inhalt einer Lesung zu, weil
Transkribus-Seiten keinen gemeinsamen Schlüssel mit unseren Seiten haben. Eine
Lesung, die schlecht genug ist, sieht dann aus wie „diese Seite ist nicht im
Lauf“ und fällt aus jeder Zeile. Das waren sechs von 22 Seiten: Tabellen und
Listen, an denen die Segmentierung zerfällt, mit 55 bis 89 % CER. Der Bericht
nannte sie „nicht zugeordnet“, eine Kategorie für Unsicherheit über die
Identität. Hier gab es keine Unsicherheit: Datei und Lesung heissen gleich.

Bemerkt habe ich es nur, weil ich die Liste der nicht zugeordneten Seiten
gelesen habe statt nur die Tabelle. Seither ordnet der Bewerter eine Seite mit
Namen über den Namen zu (#758). Die Abnahme war, dass er von selbst auf die
Werte kommt, die ich von Hand nachgerechnet hatte. Dabei fiel noch etwas auf:
Für eine der sechs Seiten war meine Nachrechnung eine Untergrenze, weil der
alte Bericht die CER zur eigenen Seite nirgends enthielt.

**Was daraus folgt.** Ein Median über „die Seiten, deren Identität feststeht“,
ist dann eine Qualitätszahl, wenn das Feststellen der Identität nicht davon
abhängt, wie gut gelesen wurde. Bei der Zuordnung über den Inhalt hängt es
davon ab. Die Liste dessen, was eine Messung weglässt, gehört zu jeder
Messung. Ein Nebenbefund, der den gleichen Mechanismus hat: Der Standardtext
des Berichts behauptete, Laßbergs Hand lese sich viel schlechter als die der
Korrespondenten. Auf den BLB-Seiten ist es umgekehrt (5,3 gegen 23,0 %).
Der Satz stammte aus der Transkribus-Auswertung, und bei der lässt der
Inhaltsabgleich die schlechtesten Seiten ab etwa 45 % CER ebenfalls weg. Er
ist jetzt neutral (#761).

## 42. Der Log kam in 64-KiB-Blöcken, und der Fix kam beim laufenden Job nicht an

Das olmOCR-Training auf UBELIX (Slurm 20187922) zeigte 12,7 Stunden lang keine
einzige Fortschrittszeile und dann alles auf einmal, als die Zeitgrenze den
Prozess beendete. Ob es nach dem Requeue am Checkpoint weitermachte oder bei
null begann, war von aussen nicht zu sehen. Ich habe es aus Zeiten
erschlossen: eine Validierung nach fünf Stunden passte nur zu einem Neustart
ab Schritt 700. Das stimmte, war aber geraten.

Die Ursache stand im Trainer: `_mirror` liest die Pipe des Kindprozesses mit
`BufferedReader.read(65536)`. Auf einer Pipe gibt das erst zurück, wenn 64 KiB
beisammen sind oder der Prozess endet. Bei 64 s pro Schritt und rund 70 Bytes
pro Zeile sind das viele Stunden. Ein Kindprozess, der jede Sekunde eine Zeile
schreibt, zeigte es in drei Sekunden: `read` lieferte beim Prozessende,
`read1` sofort. Der Fix ist `read1` plus `PYTHONUNBUFFERED` für das Kind
(training-atr-models#329).

Zwei Dinge daran. Meine ersten Tests waren auch ohne den Fix grün. Der eine
fand seinen Suchtext in der Kopfzeile des Logs, die das Kommando enthält. Der
andere lief in einer Shell, die `PYTHONUNBUFFERED` schon setzt. Erst die
Gegenprobe ohne Fix zeigte das. Und der Fix kam beim laufenden Job nicht an,
obwohl der Checkout gepullt war: Der Trainer friert für jeden Job einen
Arbeitsbaum des Commits beim Einreichen ein (`~/.cache/…/worktrees/448bef6…`),
und ein Requeue läuft auf diesem Stand weiter. Gesehen habe ich das erst an
`PYTHONPATH` in `/proc/<pid>/environ` des Trainingsprozesses. Das Einfrieren
ist richtig, ein Job soll mit dem Code fertig laufen, mit dem er begann. Ich
hatte nur angenommen, ein Pull wirke beim nächsten Start.

## 43. Gleichwertig wird ein Vergleich erst, wenn alle Leser im selben Durchgang bewertet werden

Am 30. Oktober las `kraken-german-xix-blb-v1` die 276 Transkribus-Seiten mit
Ground Truth. Bewertet allein, über seinen eigenen Lauf, ergab das 12,2 % im
Median, und alle 276 Seiten waren zugeordnet. Die veröffentlichte Tabelle der
acht Leser steht auf 226 Seiten. Die 12,2 % daneben zu stellen, wäre bequem
gewesen und falsch: Ein Lauf mit 276 Seiten bietet einer Transkription weniger
Seiten zum Verwechseln als die Lesung des ganzen Korpus mit 6719 Seiten. Die
Zuordnung über den Inhalt fällt dann leichter, und genau die schwer gelesenen
Seiten bleiben drin, die in der Tabelle fehlen.

Also habe ich alles in einem Durchgang bewertet: die beiden Läufe der Tabelle
und den neuen gegen dieselben 552 Dateien. Das kostete 45 Minuten statt
zwei. Dafür kamen dieselben 226 Seiten heraus und jede veröffentlichte Zahl
unverändert, und das ist die Probe, dass der Vergleich gleichwertig ist. Auf
diesen Seiten liest das nachtrainierte Kraken mit 12,0 % gegen 16,1 % beim
Zeilen-VLM, das das Korpus gelesen hat. Auf 154 von 226 Seiten ist es besser.
Sein Ausgangsmodell, das nie einen Laßberg-Brief gesehen hat, liegt mit
16,7 % gleichauf mit den beiden VLMs.

Die Gegenprobe auf Überschneidung gehört dazu. 18 der 226 Seiten sind
BLB-Signaturen. Neun davon stammen aus Objekten der BLB-Trainingsseite, ihre
Zeilen kann das Modell gesehen haben. Ohne sie bleibt der Median bei 12,0 %
(217 Seiten). Das Ergebnis hängt also nicht an ihnen. Ich hatte das vorher
nicht gewusst, und die Trennung musste vor dem Hinschauen festliegen: Die
Liste der Eval-Objekte stand in der Anweisung, bevor die Zahlen da waren.

**Was daraus folgt.** Eine Zahl wird mit einer Tabelle vergleichbar, wenn sie
mit demselben Verfahren über dieselben Seiten entsteht. Bei einer Zuordnung
über den Inhalt gehören auch die anderen Lesungen zum Verfahren. Dass die
alten Zahlen unverändert wiederkommen, ist der billigste Beleg dafür. Und ein
Ergebnis, das allen Erwartungen über Modellgrösse widerspricht (vier Millionen
Parameter vor vier Milliarden), verlangt zuerst die Prüfung, ob die Messung
dem kleinen Modell etwas geschenkt hat. Erst danach kommt die Freude.

## 44. Ich habe die Schritte einer Epoche für die Länge des Laufs gehalten

Das olmOCR-Seitenmodell auf dem BLB-Datensatz (Slurm 20187922) begann am
27. Oktober. Im Log des Runners stand `convergence: 947 steps/epoch × 1 epochs
= 947 steps`. Daraus habe ich gefolgert, der Job passe in ein Fenster von
15 Stunden, und das so gemeldet. Er brauchte vier Fenster und war erst am
31. Oktober fertig. Der Fortschrittsbalken des Trainings rechnete mit
**2841** Schritten: Die Anfrage setzt `--epochs 1 --max-epochs 3 --patience 1`,
und weil sich der Validierungsverlust nach Epoche 1 und 2 noch verbesserte,
liefen alle drei Epochen. Die Zeile im Runner-Log beschreibt das Minimum,
nicht den Lauf.

Zu sehen war das erst, als der Log am Ende des ersten Fensters endlich kam
(§42). Bis dahin habe ich aus einer Zahl, die ich nicht geprüft hatte, eine
Dauer abgeleitet und dem Nutzer eine Zeit genannt.

Das Ergebnis selbst ist brauchbar. Auf denselben 168 Eval-Seiten und mit
denselben Einstellungen wie das Qwen3.5-Seitenmodell misst der Trainer
**16,6 %** CER (gekürzt 16,2 %) gegen 21,5 % (19,9 %). Beide Auswertungen
brechen nach 1536 Ausgabe-Tokens ab, sodass beide Zahlen Obergrenzen sind.
Und beide sind nicht mit den Kraken-Zahlen über das Gateway vergleichbar,
solange die Modelle dort nicht registriert sind.

**Was daraus folgt.** Die Dauer eines Trainings steht in der Gesamtzahl der
Schritte, die der Trainer selbst ansetzt (`x/2841`), nicht in einer Zeile über
die Konvergenz. Wo eine Anfrage `max_epochs` über `epochs` setzt, ist die
obere Grenze die Zahl, mit der man rechnen muss. Das Early Stopping kann sie
nur verkürzen, und ob es das tut, weiss man erst nach der ersten Validierung.

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

**One letter before all of them.** The letter-wise Transkribus upload went
first with `lassberg-letter-0952` alone (upload 19763082, job 31683095), was
looked at in the interface, and only then the other 124. That order is now
enforced by the tool and the CLI alike, and the collection itself is the
evidence: `--all` is accepted once a letter of the plan is in it.

**A draw that reproduces.** The split moved into a module and ran again over
the tree the script had split. It moved nothing, because seed, per-project
stream and share were the same. An idempotent hold-out is the only kind that
can be re-run after a rebuild without anyone checking which objects it chose.

**Named skips.** Every unread file in this run is in the report with a reason,
which is how the thirteen PDFs were noticed at all. A silent drop would have left
a corpus thirteen containers short with nothing saying so.

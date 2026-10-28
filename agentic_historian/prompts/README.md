# Prompts, versioned

A prompt is an experimental parameter, not a detail of invocation. Two readings
of the same page by the same model with different prompts are different
measurements, and a comparison that cannot say which prompt produced which text
is not a comparison.

So each prompt lives here as a file, its digest is recorded in every reading it
produces (`external_atr`, in the record's `gateway_version` field), and changing
one is a commit with a diff rather than an edit to a shell line somebody pasted.

- `lassberg_atr.md` — the Laßberg correspondence, diplomatic transcription.
  Supplied verbatim by the project on 2026-10-09 and **kept verbatim**,
  including the "refers" that does not agree with its plural subject: it is the
  prompt that was measured, and silently repairing its grammar would make every
  later number incomparable with the first run. Fix it in a commit that says so,
  and the digest in the records will mark the boundary.

  Its last rule mentions a "normalized field" that the prompt itself does not
  define. `--structured` supplies one (`diplomatic` + `normalized` as JSON);
  without that flag there is nowhere for an expansion to go and the rule reads
  as "do not expand", which is also a defensible reading of a diplomatic
  transcription. Which of the two ran is in the record.

- `lassberg_atr_strict.md` — the same framing, with the output discipline the
  first measured run showed it needed. On 2026-10-10 all three pages of the
  pilot carried text that is not on the page: `Hier ist die getreue
  Transkription des Briefes:`, invented structure labels (`[Kopfvermerk von der
  Hand des Empfängers:]`, `[Anrede:]`, `[Text:]`), `**[Transkription]**`, and
  Markdown the prompt never mentioned (`~~durchgestrichen~~`). One page also
  carried four bracketed lines that are not coherent German — plausible filler
  where a passage was hard to read.

  A **new file**, not an edit, because the digest is what makes the two runs
  comparable. `092fb6b0` is what the pilot measured and stays that way.

  Three of its rules answer a specific thing the model did: no preamble, no
  structure labels, no Markdown. A fourth narrows `[...]` to a single
  *unreadable word*, since the original's wording let it swallow whole lines —
  which is exactly where the filler appeared.

  **One editorial decision is mine and is open to being overruled.** The
  original prompt is silent on deletions and insertions, so the model invented
  `~~strikethrough~~` per page. Silence invites improvisation, so this one names
  a convention — `[durchgestrichen: …]` and `[einfügung: …]`, in the same plain
  bracket family as `[...]` and `[?]` — rather than leaving the question open.
  The edition publishes TEI and these outputs are plain text, so the mapping to
  `<del>`/`<add>` happens later and whoever owns that mapping should pick the
  marks. Changing them is a new prompt file and a new digest.

- `lassberg_atr_strict2.md` — the strict prompt with the one thing the 300-page
  run showed it had wrong. Again a **new file**: `03c5a660` is what read those
  300 pages and stays that way.

  What the strict prompt got right, measured on 300 pages: the format discipline
  held throughout, and of the 28 pages it answered with nothing, **21 were blank
  versos** — where nothing *is* the transcription, and where the original prompt
  wrote museum prose into the transcription field ("*[Die Seite enthält keinen
  handschriftlichen Text / vacat. In der Papiermitte ist schwach ein
  Wasserzeichen erkennbar.]*"). The silence was the feature and this prompt
  keeps it, now said outright: never a sentence saying a page is blank.

  What it got wrong is 5 of those 28, and all five are the same confusion. `An
  empty page is an empty answer` was the prompt's only statement about
  emptiness, and the model read it as covering any page it did not want to
  write out:

  - two **written** pages came back empty (`Donaueschingen …0258__002`, 1143
    characters under the other prompt; `StATG …1339_00101`, a Pupikofer letter
    of 25 July 1829)
  - three pages carried only a library stamp or an archivist's docket, which
    the prompt never said was text — it names the correspondence and nothing
    else on the leaf

  So: an empty answer now *asserts* that the leaf is blank, and the two other
  cases get their own answers. A hard page is still transcribed. A page that is
  written and illegible is `[unlesbar]`, once — because the alternative, which
  the run also produced, is a column of 24 `[...]` standing in for a page.

  Show-through, watermarks and the physical state of the paper are named as
  *not* text, since that is what most of the 21 blank-page descriptions were
  made of.

  **The marks are my editorial decision, as before, and open to being
  overruled.** `[stempel: …]` and `[fremde hand: …]` continue the lowercase
  German bracket family of `[durchgestrichen: …]`; the point is that a stamp or
  a docket is *recoverable and strippable* rather than silently joining the
  letter — an unlabelled "Schillermuseum 11551" in a transcription is worse than
  no stamp at all. `[unlesbar]` exists so that a decline is recorded instead of
  being indistinguishable from a blank leaf: an empty answer is a success by
  every signal the runner has, which is exactly how 5 lost pages hid inside a
  column called `empty`.

- `bern_1528.md` — the Bern Disputation volumes in the Staatsarchiv Bern
  (A V 1443 Hertwig, A V 1447 Schlussredaktion), for reader G in
  `ruemlang/pipeline.py eval` (#747). Built on `lassberg_atr_strict2.md`'s
  output discipline, with the conventions of the ground truth it is scored
  against: diplomatic, abbreviations as written, one answer line per page line
  (G gets a line CER only when its line count equals the GT's), and no
  editorial marks beyond `[...]` — every mark the GT does not carry counts as an
  error. Once the first eval shows which conventions the GT does carry
  (deletions, insertions, expansions), align them in a new file.

- `federal_minutes_line.md` — one line of the Federal Council minutes
  (1848–1903), for `external_lineset.py`. The trainer's own line prompt
  ("Transcribe the handwritten text in this image exactly as written.") with
  the context a general model needs and the output discipline of the strict
  prompts. No editorial marks: the test set's GT has none, so a `[?]` would
  only cost characters.

- `sg_missiven_page.md` — whole pages of the St. Gallen Missiven
  (`dh-unibe/image-text_sg-missiven`), for `external_lineset.py --pages`.
  The `bern_1528.md` discipline for a 15th–17th-century chancery: diplomatic,
  abbreviations as written, one answer line per page line, no marks — a best
  reading instead of `[...]`, because the GT carries no marks either.

- `sg_missiven_page_v2.md` — the Missiven prompt in the conventions the GT
  turned out to use (28.10., 20 pages, Argon median 16.2 %): its most frequent
  differences were `u`→`v` 288×, missing commas and points, `,`→`/`, `i`→`I`,
  `u`→`ů`, `ü`→`ú`, `s`→`ß` — notation, not reading. So: u/v by sound,
  virgule as comma, no superscripts, no ß/ſ, lower case except names and
  sentence starts. A **new file**: `67efd42a` is what the first 20 pages
  measured. Inferred from the confusions, not from an edition guideline; where
  the guideline is known, it wins.


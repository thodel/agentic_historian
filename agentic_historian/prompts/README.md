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

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

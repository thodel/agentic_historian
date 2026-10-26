# Pinned requests, as submitted

Each file is the body `train-spec --body-only` produces for a spec in
`../specs/`: the `_note` keys dropped, every `revision: null` pinned to the
dataset commit in the file name. They are kept because a spec with a null
revision does not say what a run read, and because UBELIX fetches them from
here (tei has no route to copy them there).

On UBELIX:

```
curl -fsSL -o ~/ubelix/specs/<name>.json \
  https://raw.githubusercontent.com/thodel/agentic_historian/main/training/requests/<name>@<rev>.json
```

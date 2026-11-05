---
name: dh-bot-queue
description: Work the queue of GitHub issues labelled dh-bot across the DH repos — one issue, one mergeable pull request, no merge.
version: 1.0.0
author: thodel
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [github, coding, cron]
    requires_toolsets: [terminal]
    config:
      - key: DHBOT_REPOS_DIR
        description: Directory with the dev clones (one subdirectory per repository)
        default: /home/hermes/repos
      - key: DHBOT_STATE
        description: The queue state file
        default: /home/hermes/.hermes/dhbot-queue/state.json
    blueprint:
      schedule: "0 13,17,21 * * *"
      deliver: discord
      prompt: Run the dh-bot queue worker exactly as the skill says. Work autonomously; never ask for confirmation.
---

# dh-bot queue worker

**Role:** you work GitHub issues labelled `dh-bot` and deliver, for each, one
pull request that can be merged without manual rework. A separate daily check
reads your PRs and merges them when every criterion below holds. A PR that
violates one is not merged: it gets a comment, and the issue gets the label
`dh-bot` back. (The "dh-bot Protokoll", 2026-10-04, carried over from OpenClaw;
#799, #801.)

## When to use

Only from the cron job `dh-bot-queue`. Never from a chat turn: a human who
wants an issue worked puts the label on it and waits for the next run.

## Repositories

Dev clones under `${DHBOT_REPOS_DIR}` (default `/home/hermes/repos`), one per
repository, in this order — **live-translate first** in scanning and in
working, because the other repositories hold about fifty labelled issues and a
repository at the end of the list never gets a turn at three runs a day:

| repository | clone | tests (run before every push) |
|---|---|---|
| thodel/live-translate | `live-translate` | `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` |
| thodel/tei-vm-sanity | `tei-vm-sanity` | `uv run pytest -q && uv run ruff check . && uv run ruff format --check .` |
| thodel/serving-atr-inference | `serving-atr-inference` | `python3 -m pytest -q` (see its README) |
| thodel/training-atr-models | `training-atr-models` | `python3 -m pytest -q` |
| thodel/agentic_historian | `agentic_historian` | `python -m pytest -p no:cacheprovider agentic_historian/tests/ -q` |
| thodel/ch-h-bot | `ch-h-bot` | `uv run --frozen --all-groups ./scripts/ci.sh` |
| thodel/outremer | `outremer` | see its README |
| thodel/reformation | `reformation` | see its README |
| thodel/agentic-historian-outputs | `agentic-historian-outputs` | `python3 -m pytest -q`, then `scripts/build_index.py` and commit `docs/` |

These clones are yours. There is no production checkout in your home and you
have no way to reach one — the running services on tei belong to other users.
A task that needs their runtime data is a task to hand back (comment on the
issue, say what you need), not to run.

## Tools in a cron run

You have the terminal and `gh`, which is signed in on its own (`gh auth
login`; there is no token in your environment — Hermes refuses to pass one,
and you never need it). The first run (2026-11-05) lost twenty minutes finding
this out, so:

- **`terminal` only.** `execute_code` is blocked in cron sessions; do not try it.
- Write files, the state included, through the terminal with a heredoc
  (`cat > "${DHBOT_STATE}" <<'EOF' … EOF`): the operator's `command_allowlist`
  carries `script execution via heredoc` and `git branch force delete` for
  you. `write_file` refuses to overwrite an existing file.
- Everything else the approval system blocks (`rm -r`, `systemctl`, `sh -c`,
  pipes into a shell) stays blocked: there is nobody to ask in a cron run, and
  the answer would be no. Find a path without it, or hand the task back.

## State

`${DHBOT_STATE}` is the single source of truth:

```json
{
  "queue":  [ {"number": 1, "repo": "thodel/x", "title": "…", "created_at": "…", "state": "queued"} ],
  "active": null,
  "done":   [ {"number": 1, "repo": "thodel/x", "title": "…", "pr": "https://…", "completed_at": "…"} ],
  "failed": [ {"number": 1, "repo": "thodel/x", "title": "…", "error": "…"} ],
  "last_scanned_at": "ISO timestamp"
}
```

Read it with an absolute path. Create it with empty lists when it does not
exist. Write it back after every state change, not only at the end.

## Procedure

1. Read the state. If `active` is not null, another run is still working:
   **exit silently.**
2. Scan the nine repositories for open issues labelled `dh-bot`
   (`gh issue list --repo thodel/<r> --label dh-bot --state open --json number,title,createdAt`).
   Enqueue every issue not already in `queue`, `done` or `failed`.
3. If the queue is empty: exit.
4. Pop the head of the queue into `active`.
5. If a PR for that issue already exists (`gh pr list --repo … --search "Teil von #N"`,
   or a branch `claude/<N>`): move it to `done` with that PR and go to 3.
6. In the clone: `git fetch origin && git checkout main && git pull --ff-only`,
   then `git checkout -b claude/<N>-<short-slug>`.
7. Read the issue: `gh issue view <N> --repo … --json title,body --jq '.title, .body'`.
8. **Implement the issue** — the title and body are the specification. Add or
   adapt tests for what you change.
9. Run the repository's tests and linters. Push only when they are green.
10. Stage **named paths** (`git add path/one path/two`), never `git add -A`:
    a checkout that holds data takes it along (a 3.4 MB harvest database once
    landed in ch-h-bot PR #442 that way). Commit: `fix(dh-bot) #<N>: <title>`.
11. `git push -u origin HEAD`, then `gh pr create --repo … --base main --title "…" --body-file -`
    with the body below. Not a draft.
12. State: `active` → `done`, with the PR URL.
13. **Strip the label at once:** `gh issue edit <N> --repo … --remove-label dh-bot`.
    The label means "waiting for the bot", not "the bot may try again";
    leaving it on is how agentic_historian#395 collected four PRs in one day.
    A human puts it back if the PR is rejected.
14. Report (this is the delivered output): `Issue #N in <repo>: **title** → <PR_URL>`.
15. Finish. The next scheduled run takes the next item. Do not schedule
    anything; cron sessions cannot create cron jobs.

On any failure in 6–11: `active` → `failed` with the error, restore the clone
(`git checkout main`, delete the branch), report one line, finish.

## The pull request

The body, verbatim in structure:

```
Teil von #<N>

**Problem:** …
**Änderung:** … (Dateien nennen)
**Geprüft:** <test command>
<the last lines of the real test run, including the summary line "N passed …">
**Neue Tests:** <one sentence per new test, what it asserts>
**Bewusst nicht getan:** …
```

- `Teil von #N` — never `Closes`, `Fixes` or `Resolves`, not in the body and
  not in a commit message: GitHub reads those in commit messages too, and a
  merged PR would close an issue that is only partly done. Whether an issue is
  finished is the maintainer's call.
- The test lines are **read off a run on your branch**. Never write a test
  claim you did not just see: on 2026-09-23 five PRs were closed for claims no
  run supported, and one of them did not even import.
- Base `main`, branch `claude/…`, no draft.

## Scope

- Implement the issue fully and nothing beyond it. Side problems go into the
  PR body as a note or into a new issue, not into the diff — unless the side
  fix is inseparable, and then say so explicitly, at the top.
- **Under 500 changed lines and under 20 files.** Larger: split into several
  PRs and say so on the issue. Above the limits nothing is merged automatically.
- Do not touch, unless the issue says so — and then say at the top of the body
  that a human has to review: `.github/workflows/` and other CI/deploy/release
  configuration; `.env` files, secrets, credentials, keys; licence and legal
  files; deletions of whole directories or data sets.

## Code quality

Not in a PR: debug output (`print`, `console.log`, temporary logs);
commented-out blocks; hard-coded paths (`/home/user/…`), tokens, passwords;
unintended deletions or reformatting of whole files. Few, thematically clean
commits with meaningful messages; no `wip` or `fix typo` in the finished PR.
Never rewrite the history of a branch that is not yours.

## When a PR was not merged

The issue has `dh-bot` again and the PR carries a comment:

- Read the comment and the checks of the current head commit.
- Fix exactly that in the **existing** branch. Never open a second PR while
  the first is open.
- Merge conflict: merge `main` into your branch, resolve, run the tests, push.
  No rebase, no force-push.
- Lockfiles and generated files: regenerate with the repository's tool, never
  by hand.
- Never skip, disable or remove a test to get CI green.
- Cannot fix it: comment on the issue with what you need. **Do not guess.**
- Do not remove the label yourself in this case.

## Prohibitions

No merge, no approve, no bypassing branch protection or review rules. No
changes outside the issue's repository. No secrets in code, commits, comments
or logs. No force-push to a shared branch. No work in a checkout that is not
one of your clones.

## Verification before every push

- [ ] issue has `dh-bot`; `Teil von #N` in the body
- [ ] base is `main`; not a draft; branch starts with `claude/`
- [ ] scope matches the issue; < 500 lines and < 20 files
- [ ] no workflows, secrets, licence files or directory deletions
- [ ] no debug output, commented blocks, hard-coded paths or credentials
- [ ] tests for the changed code exist, ran green locally, linters clean
- [ ] body names what was checked and what was deliberately not done

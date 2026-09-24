# What this build cost, and what it taught

Written 2026-09-15, at the end of the day that got a Claude session from "cannot
open a socket to tei" to calling tools on it. Not a changelog — the commits are
that. This is the set of mistakes worth not repeating, each with the evidence
that produced it.

---

## 1. Inferring an interface from its protocol is not knowing it

Three times in one day, in the same shape.

**The connector dialog.** The MCP server was built around a static bearer token
because the protocol carries one. The claude.ai dialog takes a URL and, under
Advanced settings, an OAuth client id and secret — and nothing else. The whole
authentication design was unreachable from its only client, and the way it
surfaced was a German error message about a failed *Anmeldedienst* registration.
Two hundred lines of server, a runbook, a PR and a merge, all on an assumption
that one look at the dialog would have settled.

**The GPU endpoint.** The runbook said `GET /gpu` for weeks. The route is
`/train/gpu` — it lives on the trainer's router and carries its prefix. Nobody
noticed until the first live tool call returned a 404 inside an otherwise
successful response.

**The engines' merge failure.** A peft version warning looked like the cause of
three failed LoRA merges. It was not; the adapter's own `tokenizer_config.json`
was malformed. Two wrong hypotheses were built and one nearly implemented before
the traceback was read in isolation.

The correction is not "be more careful". It is: **when an interface can be
looked at, look at it before building against it.** The dialog, the route table,
the traceback. Each was one command away.

## 2. Test where it will run, not where it is convenient

`streamable_http_app()` defaults to `host="127.0.0.1"`, which quietly switches on
DNS-rebinding protection allowing only loopback `Host` headers. Every request
through nginx — where the header says `tei.dh.unibe.ch` — would have returned
421. The OAuth flow passed ten steps end to end against 127.0.0.1 and would have
failed completely on the first real request.

It was caught by installing nginx in the sandbox and putting it in front, with
the federation's neighbours faked. That is the general rule: **the proxy is part
of the system.** A test that skips it tests a different system.

The same class, from the deployment side: `python-dotenv` was in
`requirements-dev.txt` and not in `requirements.txt`. CI installs the first, the
deployment installs the second, and only the first was ever exercised — so CI
was green for however long while nothing could start from the other file. The
fix was not the missing line; it was making CI install both, so the two cannot
drift again. (It then immediately caught the same gap running the other way.)

## 3. A timeout belongs to the client, not to the server

`share_list` was a subprocess call with a 120-second budget. The MCP broker gives
a tool 60 seconds. It cut the call while the listing ran on, which is the worst
of both: the caller sees a failure and the work continues unobserved.

Raising the server's own budget would have moved the failure, not removed it.
**Work that can outlast the client's patience has to be a handle, not a call.**
The shape that fits: start it, watch it briefly, and answer with whatever is true
by then — inline when it was quick, a job id when it was not.

## 4. Constants that know neither side of the question

`--gpu-memory-utilization` is a fraction of a card's *total* memory, and vLLM
refuses to start unless that much is *free*. A single number in a config file
cannot satisfy both, and for three consecutive failures it did not: 0.70 asked
for 31 GB of a card with 19 free; a hand-lowered 0.35 died one percent short of
a KV cache after a minute of loading weights.

The registry knows the model's size. `nvidia-smi` knows the card. The arithmetic
between them is four lines. **When a constant is being tuned by hand more than
once, it is standing in for a computation nobody has written yet.**

## 5. An authorization server without a login is an open door with extra steps

The temptation, when OAuth turned out to be mandatory, was to implement the
endpoints and let `/authorize` mint a code. Every specification box ticked, and
anyone who knows the URL gets a token — worse than the shared secret it replaced,
because it *looks* authenticated.

Related, and cheaper to get wrong: the first version of the token store wrote the
pre-shared static token to disk, contradicting the docstring directly above it.
A test written from that docstring found it on its first run. **Write the
property down, then test the property — not the code.**

## 6. Say what is not verified, out loud

Several things were shipped today whose correctness could not be checked from
the sandbox: nginx behaviour (until nginx was installed), whether the broker
would reach the endpoint at all, whether the GPU arithmetic matched a real A40.
Each was named as unverified in the PR that shipped it, with the specific
command that would settle it.

Two of the three then failed on first contact. The naming is what made those
failures cheap: the check was already written down and the expected answer with
it.

## 7. What "prüf das log" should mean

The most useful artefact of the whole day is an nginx access log with timestamps.
It showed, without interpretation:

- the broker arriving from AWS at 13:39 and following the discovery pointer to a
  404 — the failed registration, in two lines;
- nothing at all after the OAuth deploy, which is what said the connector had not
  been re-created rather than that it had failed again;
- and at 17:19, the full sequence — `register` 201, `authorize` 302, `login` 302,
  `token` 200, then `POST /mcp` 200 repeating.

Three separate wrong hypotheses were abandoned on the strength of log lines
today. **A measurement beats a plausible story, and it is usually one command
away.**

---

## The standing items

| | |
|---|---|
| `vram_mb` in the registry | marked "rough estimates" and now used to size every vLLM launch. Wrong values are launch failures. See serving-atr-inference |
| `ATR_VLLM_MAX_NEW_TOKENS` | defaults to 512, which silently truncates any page-level model. Set to 4096 on idhefix by hand |
| the two qwen3.5 fine-tunes | unservable until the driver reaches ≥ 580 and vLLM knows the architecture |
| the batch itself | never run. Everything above is scaffolding for it |

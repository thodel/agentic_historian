# SPIRE and this pipeline — comparison and add-on backlog

Status: 2026-11-05. A reading of one paper against this repository, and the
add-ons it suggests. Ideas 1 and 2 are issues; ideas 3–8 are recorded here
until someone opens them. Ideas 1 and 2 have first implementations; the rest is
not implemented.

**Paper.** Yating Pan, Jiajun Zhang, Jun Wang, Qi Su (Peking University):
*Extending AI for Research to the Humanities: A Multi-Agent Framework for
Evidence-Grounded Scholarship.* EMNLP 2026 (Main), arXiv:2605.30947, v4
31.08.2026. Code and benchmark: <https://github.com/YatingPan/SPIRE>.
Citation files for Zotero: [`references/pan2026spire.bib`](references/pan2026spire.bib),
[`references/pan2026spire.ris`](references/pan2026spire.ris).

> The method and corpus sections were read verbatim (arXiv HTML v4). The
> benchmark, results, ablations and limitations below come from a summary of
> the rest of the paper; check numbers against the PDF before citing them.

---

## What SPIRE is

Input: an edited text corpus (classical Chinese from *Zhongguo Xueshu Mingzhu
Tiyao*; Latin from *The Latin Library*), an entity/relation schema and a
research question. Output: a ranked **EvidencePool** and an essay in which
every claim is bound to evidence ids that resolve to primary-source passages.

- **Agents are Unsworth's scholarly primitives** (2000): Discovering,
  Annotating, Comparing, Referring, Sampling, Illustrating, Representing, run
  in that order, then **Reflection** (at most two rounds) that diagnoses gaps
  and triggers targeted retrieval.
- **Evidence item** = (text unit, source metadata with canonical locator,
  retrieval provenance — sub-question and tier, agent annotations —
  interpretation, relevance, comparison relation, provenance check, citation
  binding).
- **Three retrieval tiers** per sub-question: passages; Leiden communities
  over the LLM-extracted entity graph (with an LLM report per community);
  HDBSCAN clusters over BGE-M3 embeddings. Cluster reports are labelled as
  embedding affinity, *not* documented influence.
- **Substrate:** 936 works, 86,843 chunks of 1000 tokens (100 overlap),
  ~209k entities (Concept/Person/Work), DuckDB + FAISS.
- **Benchmark:** 406 peer-reviewed papers; the system gets title and research
  question and must recover the primary evidence the paper cited.
- **Reported results** (summary, k=10): EvidenceR 44.3 vs. 30.0 for ReAct,
  WorkR@10 42.4 vs. ≤17.4, SectionR@10 15.3 vs. ≤4.4. Without the community
  tier EvidenceR falls to 33.5, without the cluster tier to 32.5, passages only
  28.5. In a frozen-pool study, dropping Illustrating (claim binding) hurts
  most. Cost ≈ 4.4 min, ≈ 505k tokens, ≈ 22 calls per question.
- **Failure modes they name:** period conflation (later sources cited as
  "classical"), self-translated Latin instead of canonical translations, and —
  for GraphRAG — secondary literature substituted for primary text.

## Side by side

| | SPIRE | agentic_historian |
|---|---|---|
| Starts from | edited, correct text | a scan |
| Ends with | argued essay with citations | transcription, Ad-Fontes description, linked entities, one commit |
| Agents | primitives, fixed order + reflection | tasks A–E as a tool registry (`agent_tools.py`); planner optional (`nl_orchestrator.py`, off) |
| Retrieval | passage + community + cluster | passages (`passage_find.py`, `passage_embeddings.py`), k-means (`semantic.py`), SPARQL over corpus and Bestandsgraph (`knowledge_hub/queries.py`), MCP federation |
| Provenance | evidence id → work/section, tier, claim | every KG row carries its document; `pipeline.json`; passage anchors (#397); model per reading |
| Source criticism | bibliographic (work/section) | codicological, 16 Ad-Fontes elements (Agent B) |
| Uncertainty of the text | none assumed | central: no-merge band, Gate 2, comparisons not text |
| Human role | stated, not built | Gates 1–3 with stage invalidation (`runstate.py`) |
| Evaluation | recall of cited evidence + LLM/human judges | no ground truth claimed; comparisons, null baselines, "insufficient data" below ~10 |

**Reading.** SPIRE starts roughly where this pipeline stops. Shared: roles not
hierarchy, provenance as a first-class object. SPIRE has claim-level grounding,
which this repository lacks entirely; this repository has uncertainty about the
text itself, which SPIRE does not model at all. An argument built over HTR
output needs both.

---

## Add-ons

### 1. Evidence-grounded questions ("Agent F") — #812

`/ask`: plan → sub-questions → retrieval over own passages and the federation
→ balanced sampling → claim–evidence binding with a mechanical verbatim check
→ answer + audit → at most two reflection rounds, token-budgeted. Published as
a commit under `docs/questions/<id>/` in the outputs repo.
First version: `agentic_historian/agents/argument_agent.py` and
`python -m agentic_historian ask` ([README section](../agentic_historian/README.md#agent-f-questions-answered-from-evidence-812));
`/ask` in the bot and publishing to `docs/questions/<id>/` followed; a live
run on tei is still open.

### 2. Evidence unit with reading provenance — #811

`EvidenceItem` whose identity includes the reading (`engine/model_id`), whose
quote must be a substring of that reading, and which states its status
(`historian_chosen` / `auto_selected` / `uncorrected` / `ground_truth`) and the
engines' agreement over *that span* (`agreed` / `contested` / `single`, via
`path_compare.compute_disagreements`). This is the part SPIRE cannot do and the
prerequisite for 1. First version: `agentic_historian/evidence.py`, described in
[its README section](../agentic_historian/README.md#evidence-a-quote-names-its-reading-811).

### 3. Community tier over the Bestandsgraph

The tier SPIRE's ablation values most (44.3 → 33.5 without it).

- Graph: persons, places, groups and their co-mentions — the "named alongside
  whom" query already exists in `knowledge_hub/queries.py`.
- Leiden communities, **seeded** (reproducible, as `semantic.py` requires for
  k-means, #28), at two or three resolutions; per community a short report
  listing member entities and the supporting passages with their documents.
- Label: *co-mention affinity*, never "network" or "circle" — a community is a
  statement about our extraction, not about the 15th century.
- Optionally move `semantic.py` from k-means to HDBSCAN: its explicit noise
  label is "unclustered" said out loud, which fits *say when there is no signal*
  better than forcing every document into a cluster.
- Dependencies to decide: `igraph` + `leidenalg` (or `networkx` Louvain as the
  dependency-light fallback).

### 4. A Referring step for this material

- **Follow named sources.** A passage that names a charter, a seal holder, a
  person or a holding ("der brief … mit dem Ingesigel …") is followed up in KF
  or SSRQ via the federation; the hit becomes a second evidence item linked to
  the first, not a merged one.
- **Period check, deterministic.** Compare the period a claim is about with
  Agent B's `Datierung` (`wert`, `unsicher`) of each item; flag items outside,
  and flag `unsicher` dates separately. This catches SPIRE's main failure mode
  without a model.
- **Primary vs. secondary.** HBLS/HLS articles are secondary literature; an
  item from them is marked so and cannot be the sole support of a claim about
  a document.

### 5. Sampling for balance, with a coverage report

Balance the pool across holdings (KF, SSRQ, EOS/HGB, Dodis, own corpus),
centuries and languages. Report what was *not* covered in the same shape as
Agent D's `provenance` block (#634): sources asked, sources that did not
answer, hits dropped by sampling and why. `complete: true` with an empty
section then means *measured, nothing there*.

### 6. Gate 4 — claim–evidence review

The historian accepts or rejects each binding in an answer, logged like Gate 3
(`entity_review.py`): ids, never text. That yields **binding precision** without
ground truth. Report it next to the null baseline, as `eval/linking.py` does
for linking (proposing nothing scores well on reject-heavy gold), and below ~10
decisions as *insufficient data*.

### 7. A retrieval benchmark from Swiss historiography

SPIRE's benchmark idea, applied here: a research question plus the primary
sources a published text cites for it.

- Candidates: HLS/HBLS articles (sources named at the end, HBLS is federated),
  SSRQ edition commentaries (document numbers), Königsfelden edition notes.
- Metric: recall@k of the cited set, per holding, against a passages-only and
  a keyword baseline.
- Caveat, in the style of `EVALUATION_HARNESS.md`: *cited* is not *all
  relevant*. The number is recall of what one author chose to cite — closer to
  a Gate-2 choice than to ground truth — and a better retriever can score
  lower by finding relevant passages the author did not cite.

### 8. Translation rule

Quotations stay in the original language. A translation is labelled as the
model's own and never placed inside quotation marks attributed to the source.
SPIRE reports the opposite failure (self-translated Latin passed off as the
canonical translation).

---

## What not to adopt

- **LLM-judge scores as the headline quality number.** SPIRE reports high
  inter-judge agreement (Gwet's AC2 0.90 between LLM judges), but agreement
  between judges is not correctness, and a model-scored number contradicts
  *Quality without ground truth*. Gate decisions measure; models do not.
- **Fixed 1000-token chunks.** Pages, lines, entries and the `passage-<start>-<end>`
  anchors are better evidence units here: a historian can open them.
- **Cost without a budget.** ~505k tokens and ~22 calls per question on
  GPUStack's `gpt-oss-120b` (a reasoning model, generous `max_tokens` floor)
  needs a per-question budget and a stopping rule, as the ensemble already has
  (#406 arbitration budget, #419 escalation stopping rule).
- **A single "answer similarity" metric.** SPIRE itself notes BERTScore is flat
  across systems and that surface similarity rewards fluency over grounding.

## Order

#811 → #812 → (3, 4, 5 in parallel) → 6 → 7. Idea 8 is a rule inside #812.

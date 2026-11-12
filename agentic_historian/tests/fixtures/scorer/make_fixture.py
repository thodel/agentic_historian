"""Write the scorer fixture with the training side's own code (#491 L3).

Run from a checkout that has training-atr-models beside it:
    python agentic_historian/tests/fixtures/scorer/make_fixture.py ../training-atr-models/src

It trains a tiny cell on synthetic charter German (written for this fixture,
no edition text), calibrates it on four held-out lines, saves the artefact as
``CharNgramScorer.save`` does, and writes ``expected.json`` with bits/char and z
for the four #298 trace candidates — the numbers ``scorer_reader`` must
reproduce. Regenerate only when the training side changes its formula; the test
then says so.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from atr_training.scorer import Cell, CharNgramScorer  # noqa: E402

HERE = Path(__file__).resolve().parent
TRAIN = [
    ("u1", "Wir der schultheis vnd der rat der stat ze Bern tuͦn kunt allen den die disen brief ansehent oder hoͤrent lesen daz wir mit guͦtem willen vnd wolbedachtem muͦte verkouft haben"),
    ("u2", "Ich Cuͦnrat von Hallwil ritter vergich offenlich mit disem brief fur mich vnd min erben daz ich dem erwirdigen gotzhus ze Kungsfelden geben han ein guͦt gelegen ze Winditz"),
    ("u3", "Vnser fründlich willig dienst vnd was wir eren vnd guͦtes vermugen zuvor lieben getruwen wir haben uwer schriben wol verstanden vnd lassen uch wissen daz die stösse vnd spenne"),
    ("u4", "so sich zwüschent den von Zurich vnd den von Swytz erhept hand nit gericht sint darumb bitten wir uch fruntlich daz ir die sach mit flis an die hand nemen wellent vnd die selben"),
    ("u5", "Des ze warem vrkunde haben wir vnser stat ingesigel offenlich gehenket an disen brief der geben ist an sant Johans tag des toͤffers in dem jar do man zalt von gottes geburt"),
    ("u6", "tusent vierhundert vnd darnach in dem zwenzigosten jare Die aͤbtissin vnd der convent des closters ze Kungsfelden sant Claren ordens in Costentzer bistum hand geben vnd gelihen"),
    ("u7", "dem bescheiden man Heinrich Meyer von Brugg vnd sinen erben den hof gelegen ze Lupfang mit allen rechten nutzen vnd zuͦgehoͤrden umb einen jerlichen zins von vier mut kernen"),
    ("u8", "vnd zwey malter habern guͦter vnd geber frucht die er jerlich uf sant Martins tag antwurten sol in das closter one allen iren schaden vnd kosten wie das recht ist vnd gewonlich"),
]
HELD = [
    ("h1", "Wir die burgermeister vnd der rat der stat Zurich enbieten den fromen wisen vnsern guͦten frunden vnsern willigen dienst vnd was wir eren vermugen"),
    ("h2", "Item es sol ouch nieman kein holtz howen in dem banwald one urlob der aͤbtissin by der buͦsse die darumb gesetzt ist namlich funf schilling pfenning"),
    ("h3", "Geben ze Baden an dem nehsten mentag nach sant Gallen tag in dem jar do man zalt tusent vierhundert vnd sibenzig jar"),
    ("h4", "Vnd des ze urkund hat der edel wolgeborn her graf Hans von Habsburg sin eigen ingesigel gehenkt an disen brief"),
]
# the four readings of the same line from the tei trace (#298), verbatim
TRACE = {
    "trocr": "Vnser fründlich grus vor liebe getrune von der stösse wyse so daß nit zwüschent hemin , fast vnd Cuͦnratoͤffen , darumb and die selbe",
    "kraken-catmus": "duser feunilite grus vor liebe gerrmreuon de scosse roepse di fuitsriousthont homn vast uud emraro oigon darmus ue bee selbe",
    "kraken-mccatmus": "aInfer fommlurg deuis For lb grninv (Dan de Rof) avar Coda Gnit (Lionfgon Gemin (Vaft and Coivate auFen) Daommn6 aur Dle Pelle",
    "vlm": "Infer fremdlichs grüe vor liebe gerninre Von der koffe wer So da mit zwisstient hönn kraft und Eimrath neffon Darmmb und die sellic",
}

s = CharNgramScorer(Cell(lang="de", region="Swiss", document_type="charter", century_band="15-16"), order=4)
s.train(TRAIN, min_chars=1, sources=["synthetic charter German written for this fixture"], gt_commit="fixture")
s.documents.add("4711")                      # a document the cell "trained on", for the contamination test
s.calibrate(HELD)
s.save(HERE / "de-Swiss-charter-15-16.scorer.json")
expected = {k: {"bits_per_char": s.bits_per_char(v), "z": s.z(v)[0]} for k, v in TRACE.items()}
expected["_with_previous"] = {"bits_per_char": s.bits_per_char(TRACE["trocr"], previous=TRACE["vlm"])}
(HERE / "expected.json").write_text(json.dumps({"trace": TRACE, "expected": expected,
                                                 "heldout": s.heldout}, ensure_ascii=False, indent=1),
                                    encoding="utf-8")
print(json.dumps(expected, indent=1))

"""
source_heuristic.py — Prompt heuristics for manuscript/source description.

Based on the Ad Fontes (UZH) codicological framework:
https://www.adfontes.uzh.ch/tutorium/handschriften-beschreiben
https://www.adfontes.uzh.ch/tutorium/handschriften-beschreiben/handschriftenbeschreibung-ueberblick

Generates structured description prompts for the VLM following the
16-element schema used in archival science (14th-16th c. Swiss/German context).
"""

from dataclasses import dataclass, field
from typing import Optional

# The 16 elements of a full manuscript description (Ad Fontes overview)
MANUSCRIPT_SYSTEM = (
    "Du bist ein erfahrener Kodizologe / eine erfahrene Kodizologin, "
    "spezialisiert auf spaetmittelalterliche Verwaltungsquellen "
    "(Schweiz und deutschsprachiger Raum, 14.-16. Jahrhundert). "
    "Beschreibe Handschriften nach archiwissenschaftlichen Standards "
    "der Ad-fontes-Richtlinien (UZH). "
    "Sei praezise, nutze kontrolliertes Vokabular, und unterscheide "
    "zwischen sicher beobachteten Merkmalen und plausiblen Interpretationen."
)

HANDSCHRIFTEN_ELEMENTS = [
    "Aufbewahrungsort",
    "Beschreibstoff",
    "Blaetter",
    "Format",
    "Datierung",
    "Lagen",
    "Schriftraum_Gliederung",
    "Schrift",
    "Schreiber",
    "Ausstattung",
    "Sprache",
    "Einband",
    "Provenienz",
    "Literatur",
    "Inhalt",
    "Weitere_Hinweise",
]

HEURISTICS = {
    "Beschreibstoff": {
        "keywords": ["pergament", "papier", "papyrus", "wasserzeichen", "hadern"],
        "questions": [
            "Welcher Beschreibstoff (Pergament / Papier / Papyrus)?",
            "Ist ein Wasserzeichen erkennbar? Falls ja, welche Form (z.B. Ochsenkopf, Adler)?",
            "Wie ist die Qualitaet (duenn, dick, fleckig, vergilbt)?",
            "Gibt es Hinweise auf Palimpsest (abgeschabte Schicht)?",
        ],
        "archival_context": (
            "Im 14. Jh. betraegt der Pergamentanteil noch ca. 70%, "
            "sinkt im 15. Jh. auf 30%. Papier wird ab ca. 1330 im "
            "deutschsprachigen Raum fuer Verwaltungsschriftgut verwendet. "
            "Wasserzeichen ermoeglichen die Lokalisierung und Datierung "
            "von Papierhandschriften (Piccard-/Briquet-Verzeichnisse)."
        ),
    },
    "Format": {
        "keywords": ["folio", "quarto", "oktav", "cm", "format"],
        "questions": [
            "Welches Format hat der Codex/Rodel (Hoehe x Breite in cm)?",
            "Ist es ein Folio-, Quarto- oder Oktavformat?",
            "Gibt es abweichende Formate durch spaetere Ergaenzungen?",
        ],
        "archival_context": (
            "Rechnungsbuecher sind oft schmal (fuer Reittaschen). "
            "Prachtcodices koennen bis 90x49 cm gross sein."
        ),
    },
    "Schrift": {
        "keywords": ["gotisch", "humanistisch", "kursiv", "tinte", "rot", "schwarz",
                     "initial", "abkuerzung", "rubrizierung"],
        "questions": [
            "Welche Schriftart (gotische Schrift, humanistische Schrift, Kursivschrift)?",
            "Welche Tinte (schwarz, braun, rot fuer Rubrizierung)?",
            "Wie gross ist die Schrift (Zeilenhoehe in mm oder cm)?",
            "Gibt es Auszeichnungsschriften oder farbige Initialen?",
            "Welche Abkuerzungen werden verwendet?",
            "Sind Korrekturen oder Rasuren erkennbar?",
        ],
        "archival_context": (
            "Gotische Schrift dominiert im 14.-15. Jahrhundert. "
            "Humanistische Schriften treten ab dem spaeten 15. Jahrhundert auf. "
            "Im Spaetmittelalter werden vermehrt Abkuerzungen gebraucht."
        ),
    },
    "Schreiber": {
        "keywords": ["hand", "schreiber", "hende", "korrektur", "nachtrag",
                     "federprobe", "marginalie", "rasur"],
        "questions": [
            "Ist eine oder sind mehrere Haende erkennbar?",
            "Sind die Schreiber namentlich bekannt oder anonym?",
            "Welche Art von Korrekturen (Streichung, Ueberschreibung, Randkorrektur)?",
            "Gibt es Marginalien, Federproben oder Nachtraege?",
        ],
        "archival_context": (
            "In Verwaltungsquellen sind oft mehrere Haende taetig. "
            "Zeitgenoessische Nachtraege sind fuer die Rekonstruktion "
            "des Gebrauchs wertvoll."
        ),
    },
    "Schriftraum_Gliederung": {
        "keywords": ["spalte", "zeile", "liniierung", "layout", "schriftraum", "rand"],
        "questions": [
            "Wie ist der Schriftraum angelegt (Spaltenzahl, Zeilenzahl)?",
            "Sind die Blaetter liniert oder gerastert?",
            "Wie gross ist der Schriftraum (Hoehe x Breite in cm)?",
            "Sind Verweiszeichen (z.B. kleine Hand = nota bene) erkennbar?",
        ],
        "archival_context": (
            "Linien wurden mit Bleistift oder Griffel vorgezogen. "
            "Verweiszeichen zeigen intensiven Gebrauch."
        ),
    },
    "Blaetter": {
        "keywords": ["blatt", "folio", "seite", "foliierung", "paginierung",
                     "lage", "fehlblatt", "reklamant"],
        "questions": [
            "Wie viele Blaetter / Seiten umfasst die Handschrift?",
            "Existiert eine Foliierung (antike und/oder moderne)?",
            "Fehlen Blaetter (Textverluste)?",
            "Wie ist die Lagenstruktur (regelmaessig / unregelmaessig)?",
        ],
        "archival_context": (
            "Jeweils 4 Boegen = Quaternio = 8 Blatt = 16 Seiten. "
            "Unregelmaessige Lagen deuten auf Gebrauchsschriften hin."
        ),
    },
    "Datierung": {
        "keywords": ["datierung", "jahr", "jahrhundert", "jahrzahl"],
        "questions": [
            "Ist eine Datierung explizit angegeben?",
            "Falls ja, auf welches Jahr?",
            "Falls keine explizite Datierung: welches Jahrhundert / welche Haelfte ist plausibel?",
        ],
        "archival_context": (
            "Explizite Datierungen stehen oft am Ende eines Dokuments. "
            "Bei fehlender Datierung hilft die Schriftform als Datierungshilfe."
        ),
    },
    "Sprache": {
        "keywords": ["sprache", "deutsch", "latein", "mundart"],
        "questions": [
            "In welcher Sprache ist der Text verfasst (Deutsch, Latein, gemischt)?",
            "Welcher Dialektraum ist erkennbar (z.B. alemannisch, ostschweizerisch)?",
        ],
        "archival_context": (
            "Im 14.-16. Jahrhundert ist fuer den deutschsprachigen Raum "
            "eine Mischung aus Deutsch und Latein ueblich, besonders in Verwaltungstexten."
        ),
    },
    "Einband": {
        "keywords": ["einband", "leder", "holz", "pappe", "schliesse", "kette",
                     "signatur", "spiegelblatt"],
        "questions": [
            "Aus welchem Material besteht der Einband (Holzdecke, Pappe)?",
            "Welcher Bezug (Leder, Papier, Pergament)?",
            "Gibt es Schliessen oder Metallbeschlaege?",
            "Sind alte Signaturen oder Titelschilder erkennbar?",
        ],
        "archival_context": (
            "Bis ins 16. Jahrhundert dominiert der Holzeinband mit Lederueberzug. "
            "Ein Loch mit Rostspuren kann auf eine Kette hinweisen."
        ),
    },
    "Inhalt": {
        "keywords": ["inhalt", "verfasser", "titel", "gegenstand", "urbar",
                     "rechnung", "rodel", "statuten"],
        "questions": [
            "Welcher Dokumententyp (Urbar, Rechnungsbuch, Roedel, Urkunde, Statuten)?",
            "Welcher Sachinhalt (Gueterverzeichnis, Abgabenregister, Rechtsatzungen)?",
        ],
        "archival_context": (
            "Urbare sind Gueterverzeichnisse von Grundherrschaften. "
            "Roedel sind Rollenform-Dokumente (aneinandergenaehte Streifen)."
        ),
    },
    "Ausstattung": {
        "keywords": ["initial", "miniatur", "buchschmuck", "farbe"],
        "questions": [
            "Gibt es Buchschmuck, Miniaturen oder verzierte Initialen?",
            "Welche Farben (rot, blau, gold)?",
            "Wie gross sind die Initialen (in cm oder Zeilenzahl)?",
        ],
        "archival_context": (
            "Verzierte Initialen und Lombarden sind oft rot ausgefuert "
            "(Rubrizierung). Die Stilgeschichte erlaubt zeitliche und raeumliche Eingrenzung."
        ),
    },
    "Lagen": {
        "keywords": ["lage", "quaternio", "sexternion", "reklamant", "kustode"],
        "questions": [
            "Wie ist die Lagenstruktur (regelmaessig oder unregelmaessig)?",
            "Gibt es Lagensignaturen (Kustoden) oder Reklamanten?",
        ],
        "archival_context": (
            "Reklamanten wiederholen den Textanfang der naechsten Lage "
            "auf der letzten Seite. Unregelmaessige Lagen sind typisch fuer Gebrauchsschriften."
        ),
    },
}


@dataclass
class DescriptionPrompt:
    system: str = MANUSCRIPT_SYSTEM
    context: str = ""
    focus: Optional[list[str]] = None

    def build_user_prompt(self, image_available: bool = True) -> str:
        elements = self.elements if not self.focus else self.focus
        if image_available:
            img_block = (
                "Beschreibe die abgebildete Handschrift moeglichst vollstaendig. "
                "Gehe dabei alle der folgenden Beschreibungselemente durch."
            )
        else:
            img_block = (
                "Beschreibe die durch den folgenden Transkriptionstext "
                "repräsentierte Handschrift. Leiate fehlende aeussere Merkmale "
                "aus dem Textinhalt und der Struktur des Dokuments her."
            )
        element_lines = []
        for i, elem in enumerate(elements, 1):
            heur = HEURISTICS.get(elem, {})
            questions = heur.get("questions", [])
            arch_ctx = heur.get("archival_context", "")
            q_block = "\n".join(f"  - {q}" for q in questions) if questions else "  - keine gesicherten Angaben moeglich"
            ctx_block = f"\nKontext (Archivwissenschaft): {arch_ctx}" if arch_ctx else ""
            element_lines.append(f"{i}. **{elem}**\n{q_block}{ctx_block}")
        return (
            f"{self.system}\n\n"
            f"{img_block}\n\n"
            "Beschreibungselemente (in dieser Reihenfolge durchgehen):\n\n"
            + "\n\n".join(element_lines)
            + f"\n\n{self.context}"
            + "\n\nAntworte als strukturiertes Markdown mit allen Elementen. "
            + "Kennzeichne unsichere Angaben mit (?)."
        )

    @property
    def elements(self) -> list[str]:
        return HANDSCHRIFTEN_ELEMENTS


def for_transcription() -> DescriptionPrompt:
    """Prompt optimized for describing a manuscript from transcription alone."""
    return DescriptionPrompt(
        context=(
            "Hinweis: Du siehst nur den Text, nicht das Originalbild. "
            "Leite folgende aeussere Merkmale aus dem Text her:\n"
            "- Dokumententyp (Urbar, Roedel, Rechnungsbuch etc.)\n"
            "- ungefaehre Datierung (Schriftform, Explicit-Formeln)\n"
            "- Sprachregion (Dialektmerkmale)\n"
            "- Strukturelemente (Register, Listen, Abschnitte)\n"
            "- fehlende Merkmale explizit als 'nicht beobachtbar' markieren."
        ),
    )


def quick_check() -> DescriptionPrompt:
    """Minimal 5-element prompt for rapid triage."""
    return DescriptionPrompt(
        focus=[
            "Beschreibstoff",
            "Schrift",
            "Datierung",
            "Sprache",
            "Inhalt",
        ],
        context="Fuehre eine schnelle Triage-Beschreibung durch. Nur die fuenf wichtigsten Elemente. Sei knapp aber praezise.",
    )


def full_codicological() -> DescriptionPrompt:
    """Full 16-element codicological description."""
    return DescriptionPrompt()

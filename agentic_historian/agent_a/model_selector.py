"""
agent_a/model_selector.py — Kraken/HTR model selection based on Agent B metadata.

Given a source description (from Agent B / source_description.py), this module
ranks and selects the best-matching kraken model for transcription.

Agent B fields used for matching:
  - schrift / script      → script type (e.g. "Caroline minuscule", "Kursive", "Fraktur")
  - sprache / language    → ISO language code (e.g. "de", "la", "fr")
  - datierung / date      → century or year range (e.g. "14. Jh.", "1200-1250")
  - schreiber / scribe    → may carry dialect hints
  - ausstattung / layout  → book-hand vs. documentary
  - einband / binding     → archival context (not used for selection)
  - provienz / origin     → regional dialect hints
  - inhalt / content     → document type (charter, ledger, chronicle…)

Selection strategy:
  1. Exact script + language + century match   → score 1.0
  2. Script + language (century fuzzy)         → score 0.8
  3. Language + century (script fuzzy)         → score 0.6
  4. Script + century (language fuzzy)         → score 0.5
  5. Language only                             → score 0.4
  6. Script only                               → score 0.3
  7. Any partial match                         → score 0.1–0.2
  8. No match                                  → score 0.0 (use party / first available)

Usage:
  from agent_a.model_selector import select_kraken_model

  best_model = select_kraken_model(
      script="Caroline minuscule",
      lang="la",
      century=13,
  )
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from agent_a.models import KRAKEN_MODELS, KrakenModel


# ── Century utilities ─────────────────────────────────────────────────────────

CENTURY_ALIASES: dict[str, int] = {
    "10. jh": 10, "10. jahrhundert": 10,
    "11. jh": 11, "11. jahrhundert": 11,
    "12. jh": 12, "12. jahrhundert": 12,
    "13. jh": 13, "13. jahrhundert": 13,
    "14. jh": 14, "14. jahrhundert": 14,
    "15. jh": 15, "15. jahrhundert": 15,
    "16. jh": 16, "16. jahrhundert": 16,
    "17. jh": 17, "17. jahrhundert": 17,
    "18. jh": 18, "18. jahrhundert": 18,
    "19. jh": 19, "19. jahrhundert": 19,
    "20. jh": 20, "20. jahrhundert": 20,
    "mittelalter": 14,         # default medieval → 14th c
    "fruehneuzeit": 16,        # default early modern → 16th c
}


def parse_century(date_str: str) -> Optional[int]:
    """
    Extract a single integer century (1–21) from a date string.
    Handles: '14. Jh.', '13th century', '1200-1250', 'ca. 1350', '1803'.
    Returns the midpoint century for ranges.
    """
    s = date_str.lower().strip()
    # spelled-out century
    for alias, cent in CENTURY_ALIASES.items():
        if alias in s:
            return cent
    # explicit range: 1200-1250
    m = re.search(r"(\d{4})\s*[-–]\s*(\d{4})", s)
    if m:
        y1, y2 = int(m.group(1)), int(m.group(2))
        return int(round((y1 / 100 + y2 / 100) / 2))
    # single year near a century
    m = re.search(r"\b(1\d)\d{2}\b", s)
    if m:
        return int(m.group(1)) + 1
    return None


def centuries_overlap(century: int, model_centuries: list[int]) -> bool:
    """Check if a document century overlaps with a model's training centuries."""
    if not model_centuries:
        return True  # unknown → assume broad
    # Exact or adjacent century
    return any(abs(c - century) <= 1 for c in model_centuries)


# ── Script / language normalisation ─────────────────────────────────────────

SCRIPT_ALIASES: dict[str, list[str]] = {
    "caroline": ["caroline minuscule", "carolingische minuscule", "caroline"],
    "textura": ["textura", "gotische textura", "textualis"],
    "schwabacher": ["schwabacher", "schwabacher type"],
    "kursive": ["kursive", "cursive", " kursiv", "秘书体"],
    "fraktur": ["fraktur", "blackletter"],
    "humanistisch": ["humanistische kursive", "humanistic cursive", "italic"],
    "rotunda": ["rotunda", "gotische rundschrift"],
    "bastarda": ["bastarda", "bastard script"],
    "unzial": ["unzial", "uncial"],
    "minuskel": ["minuskel", "minuscule"],
    "halbkursive": ["halbkursive", "half cursive"],
    "messbuchstaben": ["messbuchstaben", "lombardic"],
    "Printed": ["printed", "druck", "typo"],
    "antiqua": ["antiqua", "roman type"],
    "kurrent": ["kurrent", "deutsche kurrent"],
    "sütterlin": ["sütterlin", "suetterlin"],
}

LANG_ALIASES: dict[str, str] = {
    "deutsch": "de", "german": "de",
    "latein": "la", "latin": "la",
    "franzoesisch": "fr", "french": "fr",
    "italienisch": "it", "italian": "it",
    "spanisch": "es", "spanish": "es",
    "englisch": "en", "english": "en",
    "niederlaendisch": "nl", "dutch": "nl", "flemish": "nl",
    "tschechisch": "cs", "czech": "cs",
    "polnisch": "pl", "polish": "pl",
    "ungarisch": "hu", "hungarian": "hu",
    "arabisch": "ar", "arabic": "ar",
    "hebraeisch": "he", "hebrew": "he",
    "griechisch": "el", "greek": "el",
    "zyprisch": "el",      # Greek script, Cypriot content
    " syrisch": "syr", "syriac": "syr",
    "koptisch": "cop", "coptic": "cop",
    "urdu": "ur", "urdū": "ur",
    "hindi": "hi", "hindi": "hi",
    "sanskrit": "sa", "sanskrit": "sa",
    "mittel": "de",         # Mittellatein → Latin
}


def normalise_script(raw: str) -> str:
    """Map a raw script description to a canonical key."""
    s = raw.lower().strip()
    for key, aliases in SCRIPT_ALIASES.items():
        if s in aliases or any(a in s for a in aliases):
            return key
    return s


def normalise_lang(raw: str) -> str:
    """Map a raw language description to an ISO 639-1 code."""
    s = raw.lower().strip()
    return LANG_ALIASES.get(s, s[:2])


# ── Score function ───────────────────────────────────────────────────────────

@dataclass
class ModelMatch:
    """A model together with its match score against query criteria."""
    model: KrakenModel
    score: float
    matched_on: list[str] = field(default_factory=list)
    reason: str = ""

    def __lt__(self, other: ModelMatch) -> bool:
        return self.score < other.score


def score_model(
    model: KrakenModel,
    *,
    script: Optional[str] = None,
    lang: Optional[str] = None,
    century: Optional[int] = None,
    document_type: Optional[str] = None,
) -> ModelMatch:
    """
    Score a single model against document criteria.
    Returns a ModelMatch with score (0.0–1.0) and explanation.
    """
    score = 0.0
    matched: list[str] = []
    reasons: list[str] = []

    norm_script = normalise_script(script) if script else None
    norm_lang   = normalise_lang(lang) if lang else None

    # Script match
    if norm_script and model.script:
        norm_model_script = normalise_script(model.script)
        if norm_script == norm_model_script:
            score += 0.4
            matched.append("script")
            reasons.append(f"script={model.script}")
        elif norm_script in norm_model_script or norm_model_script in norm_script:
            score += 0.2
            matched.append("script~")
            reasons.append(f"script fuzzy: {model.script}")

    # Language match
    if norm_lang and model.lang:
        if norm_lang == model.lang:
            score += 0.3
            matched.append("lang")
            reasons.append(f"lang={model.lang}")
        elif norm_lang in model.lang or model.lang in norm_lang:
            score += 0.15
            matched.append("lang~")
            reasons.append(f"lang fuzzy: {model.lang}")

    # Century match
    if century and model.centuries:
        if centuries_overlap(century, model.centuries):
            score += 0.2
            matched.append("century")
            reasons.append(f"centuries={model.centuries}")
        elif any(abs(c - century) <= 2 for c in model.centuries):
            score += 0.1
            matched.append("century~")
            reasons.append(f"centuries near {century}: {model.centuries}")

    # Document type heuristics (based on model notes)
    if document_type and model.notes:
        dtype = document_type.lower()
        notes = model.notes.lower()
        # Book manuscripts suit "catmus" models
        if "book" in dtype and "catmus" in notes and "catmus" in model.model_id:
            score += 0.1
        # Registers/charters suit specific models
        if any(k in dtype for k in ["register", "charter", "ledger", "urbar"]) \
           and "register" in notes:
            score += 0.1

    # Boost: known-good default medieval models always get a baseline
    if score == 0.0 and model.notes and ("medieval" in model.notes.lower() or "middle ages" in model.notes.lower()):
        score = 0.05  # baseline for unknown scripts that are at least medieval

    return ModelMatch(
        model=model,
        score=min(score, 1.0),
        matched_on=matched,
        reason="; ".join(reasons) if reasons else "no match",
    )


# ── Public selector ──────────────────────────────────────────────────────────

@dataclass
class SourceCriteria:
    """
    Structured criteria derived from Agent B source description.
    Construct via SourceCriteria.from_agent_b() or directly.
    """
    script: Optional[str] = None        # e.g. "Caroline minuscule"
    lang: Optional[str] = None          # ISO code, e.g. "de", "la"
    century: Optional[int] = None       # integer 10–20
    date自由: Optional[str] = None       # raw date string (e.g. "14. Jh.")
    document_type: Optional[str] = None # e.g. "charter", "ledger", "chronicle"
    region: Optional[str] = None        # e.g. "Bavaria", "Swiss", "Saxon
    notes: str = ""                     # full raw text of Agent B description

    @classmethod
    def from_agent_b(cls, description: str) -> "SourceCriteria":
        """
        Parse Agent B markdown/text description to extract selection criteria.
        Uses keyword extraction — lightweight, no LLM needed.
        """
        desc = description.lower()

        # Extract language
        lang = None
        for kw, code in LANG_ALIASES.items():
            if kw in desc:
                lang = code
                break

        # Extract script
        script = None
        for kw, canonical in SCRIPT_ALIASES.items():
            if kw in desc:
                script = canonical[0] if isinstance(canonical, list) else canonical
                break

        # Extract century / date
        century = None
        # Check for "14. jh", "15. jahrhundert", etc.
        for alias, cent in CENTURY_ALIASES.items():
            if alias in desc:
                century = cent
                break
        if century is None:
            # Year range like "1300–1350"
            m = re.search(r"(\d{4})\s*[-–]\s*(\d{4})", description)
            if m:
                y1, y2 = int(m.group(1)), int(m.group(2))
                century = int(round((y1 / 100 + y2 / 100) / 2))
            else:
                # Single year
                m = re.search(r"\b(1\d)\d{2}\b", description)
                if m:
                    century = int(m.group(1)) + 1

        # Document type keywords
        doc_type = None
        doc_keywords = {
            "urbar": "urbarium",
            "Zinsregister": "register",
            "steuerregister": "register",
            "lehenbuch": "register",
            "chronik": "chronicle",
            "diplom": "charter",
            "urkunde": "charter",
            "brief": "letter",
            "protokoll": "protocol",
            "rechnung": "ledger",
            "inventar": "inventory",
            "testament": "testament",
            "foliant": "book",
            "codex": "book",
            "handschrift": "book",
        }
        for kw, dtype in doc_keywords.items():
            if kw in desc:
                doc_type = dtype
                break

        return cls(
            script=script,
            lang=lang,
            century=century,
            date自由=description,
            document_type=doc_type,
            notes=description,
        )


def select_kraken_model(
    criteria: SourceCriteria,
    *,
    top_k: int = 3,
    require_score_above: float = 0.0,
) -> list[ModelMatch]:
    """
    Select the best-matching kraken models for given source criteria.

    Args:
        criteria:        SourceCriteria derived from Agent B description
        top_k:           Return top N candidates (descending score)
        require_score_above: Minimum score threshold (0.0–1.0)

    Returns:
        Sorted list of ModelMatch (best first). Empty if no models match.
    """
    if not KRAKEN_MODELS:
        logger.warning("[model_selector] KRAKEN_MODELS is empty — no models configured")
        return []

    scored = []
    for m in KRAKEN_MODELS.values():
        match = score_model(
            m,
            script=criteria.script,
            lang=criteria.lang,
            century=criteria.century,
            document_type=criteria.document_type,
        )
        if match.score >= require_score_above:
            scored.append(match)

    scored.sort(key=lambda x: x.score, reverse=True)

    if scored:
        best = scored[0]
        logger.info(
            f"[model_selector] Best match: {best.model.name} "
            f"(score={best.score:.2f}, {best.reason})"
        )

    return scored[:top_k]


def select_best_kraken_model(
    description: str | SourceCriteria,
    **kwargs,
) -> Optional[KrakenModel]:
    """
    Convenience: pass raw Agent B text, get the single best model or None.
    """
    if isinstance(description, str):
        criteria = SourceCriteria.from_agent_b(description)
    else:
        criteria = description

    matches = select_kraken_model(criteria, **kwargs)
    return matches[0].model if matches else None
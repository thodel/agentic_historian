with open('/home/dh/agentic_historian/agentic_historian/agent_a/model_selector.py', 'r') as f:
    content = f.read()

old = """            notes=description,
        )


def select_kraken_model("""

new = """            notes=description,
        )

    @classmethod
    def from_source_json(cls, source_json: dict,
                          fallback_description: str = "") -> "SourceCriteria":
        \"\"\"
        Parse the structured Ad-Fontes source_json dict returned by Agent B.

        Each element has the shape ``{ "wert": "...", "unsicher": bool }``.
        Unwrap the ``wert`` wrapper automatically.

        Falls back to ``fallback_description`` for any field that is missing
        or null in the JSON — so nothing regresses when a field can't be read.
        \"\"\"
        def _unwrap(val):
            if val is None:
                return None
            if isinstance(val, dict):
                return val.get("wert") or None
            return str(val) if val else None

        def _century_from_wert(wert):
            if not wert:
                return None
            return parse_century(wert)

        # Schrift -> script
        script_raw = _unwrap(source_json.get("Schrift"))
        script = normalise_script(script_raw) if script_raw else None

        # Sprache -> lang
        lang_raw = _unwrap(source_json.get("Sprache"))
        lang = normalise_lang(lang_raw) if lang_raw else None

        # Datierung -> century
        dat_raw = _unwrap(source_json.get("Datierung"))
        century = _century_from_wert(dat_raw)

        # Document type (from Inhalt element, lightweight keyword scan)
        inhalt_raw = _unwrap(source_json.get("Inhalt"))
        doc_type = None
        if inhalt_raw:
            inhalt_lower = inhalt_raw.lower()
            doc_keywords = {
                "urbar": "urbarium", "zinsregister": "register",
                "steuerregister": "register", "lehenbuch": "register",
                "chronik": "chronicle", "diplom": "charter",
                "urkunde": "charter", "brief": "letter",
                "protokoll": "protocol", "rechnung": "ledger",
                "inventar": "inventory", "testament": "testament",
                "foliant": "book", "codex": "book", "handschrift": "book",
            }
            for kw, dtype in doc_keywords.items():
                if kw in inhalt_lower:
                    doc_type = dtype
                    break

        fb = cls.from_agent_b(fallback_description) if fallback_description else None

        return cls(
            script=script if script is not None else (fb.script if fb else None),
            lang=lang if lang is not None else (fb.lang if fb else None),
            century=century if century is not None else (fb.century if fb else None),
            date_raw=dat_raw or (fb.date_raw if fb else ""),
            document_type=doc_type if doc_type is not None else (fb.document_type if fb else None),
            notes=fallback_description,
        )

    @classmethod
    def from_agent_b_and_json(cls, source_description: str,
                                source_json) -> "SourceCriteria":
        \"\"\"
        Build SourceCriteria preferring Agent B's structured ``source_json``,
        falling back to the markdown ``source_description`` scan for any
        missing or empty field.

        Call this from both Phase-3 kraken selection and RunState persistence.
        \"\"\"
        if source_json:
            return cls.from_source_json(source_json, fallback_description=source_description)
        return cls.from_agent_b(source_description)

    def select_kraken_model("""

if old in content:
    content = content.replace(old, new)
    with open('/home/dh/agentic_historian/agentic_historian/agent_a/model_selector.py', 'w') as f:
        f.write(content)
    print("Patched OK")
else:
    print("Pattern not found!")
    idx = content.find("notes=description,")
    print(repr(content[idx:idx+200]))
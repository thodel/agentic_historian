"""``requirements.txt`` has to cover what ``config.py`` imports.

It did not. `python-dotenv` was listed only in `requirements-dev.txt` — which CI
installs, so CI was green throughout — while `config.py` imports it at module
level and every module in the package imports `config`. Nothing noticed until a
process started from a venv built off `requirements.txt` alone: the MCP server on
tei, 2026-09-15, dying at `ModuleNotFoundError: No module named 'dotenv'` before
it could serve anything.

The gap is structural rather than careless: the test suite and the deployment
install different files, and only the deployment exercises the smaller one. So
this test reads the imports out of `config.py` and checks each third-party one
against `requirements.txt`, which is the check CI was missing.

`config.py` alone, deliberately. It is the one module every entry point reaches,
and widening this to the whole package would catch optional imports that are
*meant* to be absent (rapidfuzz has a pure-Python fallback; `mcp_atr.server`
defers its SDK import precisely so the package imports without it).

Import-name to distribution-name (`dotenv` → `python-dotenv`) comes from the
installed metadata rather than a hand-written map, which would be one more thing
to forget to update.
"""

import ast
import re
import sys
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
CONFIG = PKG / "config.py"
REQUIREMENTS = PKG / "requirements.txt"


def _top_level_imports(source: str) -> set[str]:
    """Modules imported at module level — the ones that must exist to import at all.

    Imports inside a function are deliberately excluded: the package uses them for
    heavy or optional dependencies, and they fail where a caller can react.
    """
    tree = ast.parse(source)
    names: set[str] = set()
    for node in tree.body:                      # body only == module level
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def _required_distributions(text: str | None = None) -> set[str]:
    """Distribution names in requirements.txt, normalised the way PyPI does.

    Takes the text rather than always reading the file, so the parser itself can
    be tested on the shapes it has to survive without writing a fixture file.
    """
    if text is None:
        text = REQUIREMENTS.read_text(encoding="utf-8")
    out = set()
    for line in text.splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        name = re.split(r"[<>=!~\[;]", line)[0].strip()
        if name:
            out.add(name.lower().replace("_", "-"))
    return out


def test_every_third_party_import_in_config_is_declared():
    imports = _top_level_imports(CONFIG.read_text(encoding="utf-8"))
    third_party = sorted(n for n in imports if n not in sys.stdlib_module_names)
    assert third_party, "config.py imports nothing third-party — has it moved?"

    mapping = packages_distributions()
    declared = _required_distributions()

    missing = []
    for module in third_party:
        dists = mapping.get(module)
        if not dists:                 # not installed here; nothing to check against
            continue
        if not any(d.lower().replace("_", "-") in declared for d in dists):
            missing.append(f"{module} (provided by {', '.join(dists)})")

    assert not missing, (
        "config.py imports these, and requirements.txt does not list them:\n  "
        + "\n  ".join(missing)
        + "\nEvery module in the package imports config, so a venv built from "
          "requirements.txt alone cannot import anything at all."
    )


def test_dotenv_specifically():
    """The regression, named. `python-dotenv` is easy to lose again: the import is
    spelled `dotenv` and the distribution is not."""
    assert "python-dotenv" in _required_distributions()


@pytest.mark.parametrize("line,expected", [
    ("python-dotenv>=1.0.0", "python-dotenv"),
    ("loguru>=0.7.0", "loguru"),
    ("webdav4>=0.11.0   # a trailing comment", "webdav4"),
    ("uvicorn[standard]==0.30.1", "uvicorn"),
    ("Rapid_Fuzz >= 3.9", "rapid-fuzz"),
    ("# only a comment", None),
    ("", None),
])
def test_the_requirements_parser(line, expected):
    assert _required_distributions(line) == ({expected} if expected else set())

#!/usr/bin/env python3
"""Which directories the bot must be able to write — derived, not guessed (#567).

``hardening.conf`` sets ``ProtectSystem=strict``, which makes the whole file
system read-only except what ``ReadWritePaths=`` lists. A missing path does not
degrade gracefully: the first write fails. The drop-in therefore shipped with a
``CHANGEME`` placeholder for a ``/srv`` checkout — and on a host whose checkout
is under ``/home`` installing it as-is makes ``data/`` unwritable.

A placeholder that has to be edited by hand before it is safe is a step nobody
takes, and S2 item 2 sat unimplemented for exactly that reason. So this reads
the paths out of the live ``config`` instead, on the host, in the venv the bot
runs in — the same values the process itself will use.

Run it on the host::

    /path/to/.venv/bin/python deploy/systemd/writable_paths.py

**Two tiers, and the difference is a decision, not a detail.** Normal operation
writes only to data directories. ``/update`` additionally rewrites the checkout
(``git -C <repo> pull``) and the virtualenv (``pip install -r …``) — that is its
job. Confinement that permits that permits the process to rewrite its own code,
so the two tiers cannot both be had. This prints them apart and says so.
"""

from __future__ import annotations

import sys
from pathlib import Path


def _dedupe(paths: list[Path]) -> list[Path]:
    """Drop a path that another listed path already contains.

    ``ReadWritePaths`` applies to a directory and everything under it, so a
    child next to its parent is noise — and noise in a security unit is what
    stops being read.
    """
    kept: list[Path] = []
    for path in sorted(set(paths), key=lambda p: len(p.parts)):
        if not any(_is_within(path, other) for other in kept):
            kept.append(path)
    return kept


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def data_paths(cfg) -> list[Path]:
    """Everything normal operation writes.

    ``KH_DIR`` is the one that surprises: ``hub.json`` lives under
    ``knowledge_hub/data``, **not** under ``DATA_DIR``, so a unit that lists only
    the latter leaves every knowledge-hub write failing.

    ``ATR_PAGE_CACHE_ARCHIVE`` is deliberately **not** here. It is the cold tier
    of the page cache, and the bot only *reads* it (``utils.images`` brings a
    working copy back, the HF export indexes it); what writes it is
    tei-vm-sanity's nightly ``archive.sh``, as ``dh``, from cron. Listing it
    looked harmless and was not: on tei it is a CIFS mount with ``forceuid``,
    so every file there belongs to ``dh`` whoever created it, ``chown -R`` to
    the bot user fails, and ``switch_to_own_user.sh`` dies in step 4 under
    ``set -e`` — after the user exists and before the drop-ins are installed.
    ``ProtectSystem=strict`` leaves reading alone, so a read-only tier needs no
    entry at all (measured 22.10.2026).
    """
    candidates = [
        getattr(cfg, "DATA_DIR", None),
        getattr(cfg, "KH_DIR", None),
        getattr(cfg, "HOT_FOLDER", None),
        getattr(cfg, "PROCESSED_FOLDER", None),
        getattr(cfg, "NEXTCLOUD_STAGING_DIR", None),
        getattr(cfg, "GT_ROOT", None),
        getattr(cfg, "VLM_TEST_ROOT", None),
        getattr(cfg, "ATR_PAGE_CACHE", None),
    ]
    return _dedupe([Path(p) for p in candidates if p])


def in_virtualenv() -> bool:
    """Whether this interpreter is a virtualenv rather than the system Python."""
    return sys.prefix != sys.base_prefix


def update_paths(cfg) -> list[Path]:
    """What ``/update`` writes, and nothing else needs.

    ``updater`` runs ``git -C <repo>`` and ``pip install``, so the checkout and
    the virtualenv. Listing them is what keeps ``/update`` working; leaving them
    out is what makes the sandbox mean something.

    **The virtualenv is only included when this really is one.** Run with the
    system interpreter, ``sys.prefix`` is ``/usr`` — and emitting
    ``ReadWritePaths=/usr`` into a hardening unit would hand the process write
    access to the whole system, which is worse than installing no hardening at
    all. A path that cannot be measured is left out and named, never guessed.
    """
    base = Path(getattr(cfg, "BASE_DIR", ".")).resolve()
    paths = [base.parent]
    if in_virtualenv():
        paths.append(Path(sys.prefix).resolve())
    return _dedupe(paths)


def render(paths: list[Path]) -> str:
    return "\n".join(f"ReadWritePaths={p}" for p in paths)


def main(argv: list[str] | None = None) -> None:
    """Print the writable paths, or just the data directories with ``--data-only``.

    ``--data-only`` prints one bare path per line and nothing else, because
    ``switch_to_own_user.sh`` reads it: a script that has to grep a commented
    report for paths would break on the next comment, and typing the list into
    the script instead is the drift that put a non-existent ``/srv`` path into
    the unit in the first place (#567).
    """
    argv = sys.argv[1:] if argv is None else argv
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "agentic_historian"))
    import config

    data = data_paths(config)
    update = update_paths(config)

    if "--data-only" in argv:
        for path in data:
            print(path)
        return

    print("# ── Tier 1: normal operation ─────────────────────────────────────")
    print(render(data))
    print()
    print("# ── Additionally required to deploy INTO this checkout ──────────")
    print("# These let a process rewrite the code and the dependencies it runs.")
    print("# They are NOT in the bot's unit any more: `/update` reports and no")
    print("# longer pulls (#567, decided 17.10.2026), so the deploy runs from")
    print("# outside as whoever owns the checkout. Printed for that person.")
    print(render(update))
    if not in_virtualenv():
        print("# WARNUNG: mit dem System-Python gelaufen, nicht im venv des Bots.")
        print(f"#   sys.prefix ist {sys.prefix} — als ReadWritePaths wäre das")
        print("#   fatal, deshalb steht es NICHT oben. Nochmal mit dem venv")
        print("#   laufen lassen, das die Unit in ExecStart benutzt, sonst fehlt")
        print("#   der Pfad, den `pip install` beim /update beschreibt.")
    print()
    overlap = [p for p in data if any(_is_within(p, u) for u in update)]
    if overlap:
        print("# NB: tier 1 lies inside tier 2 here, so tier 2 alone would cover")
        print("#     it — listed separately on purpose, so dropping tier 2 does")
        print("#     not silently drop the data directories with it:")
        for p in overlap:
            print(f"#       {p}")


if __name__ == "__main__":
    main()

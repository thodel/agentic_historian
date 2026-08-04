"""Shared test bootstrap — makes every test file runnable on its own.

Two defects this fixes, both of the same kind: a file that passes in the full
suite only because an alphabetically earlier file ran first and left global state
behind. That is a real cost — the natural debugging move is to run the one file
you are working on, and until now two of them failed when you did.

1. **Import path.** Most test files carry a ``sys.path.insert(0, PKG)`` preamble,
   but ``test_ah_227_reprocess_triggers.py`` does not, so all 18 of its tests died
   with ``ModuleNotFoundError: No module named 'config'`` when run alone.

2. **Event loop.** ``asyncio.run()`` closes its loop and leaves none installed.
   ``bot.py`` builds a ``commands.Bot`` at module scope, which calls
   ``asyncio.get_event_loop()`` — so any file that runs a coroutine *before*
   importing ``bot`` fails on the import rather than on an assertion
   (``test_ah_289_progress_reporter.py``).

Doing this here rather than per-file keeps the fix from having to be remembered
again the next time a test file imports ``bot`` after an ``asyncio.run()``.
"""

import asyncio
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))


@pytest.fixture(autouse=True)
def _event_loop_installed():
    """Guarantee a current event loop for every test.

    Only installs one when the slot is genuinely empty, so a test that manages its
    own loop is untouched. The loop is not closed here: closing a loop this fixture
    did not create would break tests that keep one across cases.
    """
    try:
        asyncio.get_event_loop_policy().get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    yield

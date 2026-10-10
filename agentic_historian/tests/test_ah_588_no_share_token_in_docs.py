"""Tests for SEC-17 (#588): the Nextcloud share token must not sit in public docs.

docs/NEXTCLOUD_MOUNT.md (and two other docs) carried the share token in cleartext
as the WebDAV username. It is half a credential for a password-protected share
and does not belong in public docs; it is replaced by a `<share-token>`
placeholder. This guards against re-introduction anywhere under docs/.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LEAKED_TOKEN = "FaGXMmkkoY23eaA"


def test_the_share_token_is_absent_from_all_docs():
    offenders = []
    for md in (REPO_ROOT / "docs").rglob("*.md"):
        if LEAKED_TOKEN in md.read_text(encoding="utf-8"):
            offenders.append(str(md.relative_to(REPO_ROOT)))
    assert offenders == [], f"share token still present in: {offenders}"


def test_the_mount_doc_uses_a_placeholder():
    text = (REPO_ROOT / "docs" / "NEXTCLOUD_MOUNT.md").read_text(encoding="utf-8")
    assert "<share-token>" in text

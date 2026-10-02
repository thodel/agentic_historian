"""What a document id may be, checked before it becomes a permanent URL (#521).

A document id is not an internal handle. The publisher writes it straight into
``docs/<doc_id>/`` in the outputs repository, where it becomes a public address
that other work cites. Ids here come from the material: ``ingest`` takes the
name of the ingested folder, ``text_recognition`` takes an image's stem. Neither
is checked, so whatever a directory happens to be called becomes a URL.

That is how ``u-17__`` and ``kf-`` reached publication. Both had to be retired
afterwards by hand in the outputs repository, through a ``supersedes`` pointer
that keeps the old address resolving — and that cleanup went wrong in its own
way, leaving a stub canonical and the record holding the text retired
(agentic-historian-outputs#195, #255). The same gap published engineering
fixtures (``saa-0001-test`` and five siblings), which then had to be withdrawn.

The outputs repository already refuses both, in ``scripts/build_outputs.py``
(``slug_violation``, ``validate_no_test_ids``, ``validate_slugs``). But it
refuses at *build* time, after the publisher has committed the directory: the
bad address is in the repository and the site build is broken until a human
retires the id. Checking here moves the same policy to the moment the id would
become a path, which is the only point where refusing costs nothing.

The policy is deliberately a copy rather than an import: the two repositories
are deployed separately and neither can import the other. The tests pin the two
to the same answers, so a change on either side that is not mirrored shows up
as a failure rather than as a document that publishes here and is rejected
there.

Refusing rather than normalising is also deliberate, and the outputs repository
gives the reason: a normalised id can collide with an existing document and
silently move a published URL. A name that is wrong should be fixed where it is
wrong, in the material.
"""

from __future__ import annotations

import re

#: A published id: letters and digits at both ends, with ``.``, ``_`` and ``-``
#: allowed between. Mirrors ``SLUG_PATTERN`` in the outputs repository.
SLUG_PATTERN = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?$")

#: A ``test`` component, delimited so that ``latest`` and ``contest`` are not
#: caught. Mirrors ``is_test_id`` in the outputs repository.
TEST_COMPONENT = re.compile(r"(^|[._-])test([._-]|$)")


def slug_violation(doc_id: str) -> str:
    """Return why *doc_id* breaks the slug policy, or "" if it does not."""
    if SLUG_PATTERN.match(doc_id):
        return ""
    if doc_id and (doc_id[0] in "._-" or doc_id[-1] in "._-"):
        return ("must start and end with a letter or digit "
                "(no leading/trailing '.', '_' or '-')")
    return "may only contain letters, digits, '.', '_' and '-'"


def is_test_id(doc_id: str) -> bool:
    """Whether the id carries a delimited ``test`` component."""
    return bool(TEST_COMPONENT.search(doc_id.casefold()))


def publication_refusal(doc_id: str) -> str:
    """Return why *doc_id* must not be published, or "" if it may be.

    One function because the publisher has one decision to make. The order
    matters only for the message a human reads first.
    """
    if not doc_id or not doc_id.strip():
        return "is empty; a document needs an id before it can have an address"
    if doc_id != doc_id.strip():
        return "has leading or trailing whitespace"
    reason = slug_violation(doc_id)
    if reason:
        return reason
    if is_test_id(doc_id):
        return ("looks like an engineering fixture (a delimited 'test' "
                "component); the outputs repository refuses these, and the "
                "ones that slipped through had to be withdrawn")
    return ""

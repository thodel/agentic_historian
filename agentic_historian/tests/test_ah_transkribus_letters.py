"""One Transkribus document per letter, only where the letter is known (2026-10-23).

The first plan made one document per cache folder, named after the folder —
`agentic_historian-page_cache-27f2…__Aarau__upload__lassberg-letter-0892` — and
that is the cache's structure, not the edition's. The edition's unit is the
letter: a page belongs to one when its path carries `lassberg-letter-NNNN` and
the edition has a file for it under `data/letters`. And the upload endpoint,
left unimplemented for three weeks, is now code that one letter will confirm.
"""

import json
import sys
from pathlib import Path

import pytest

PKG = Path(__file__).resolve().parents[1]
if str(PKG) not in sys.path:
    sys.path.insert(0, str(PKG))

import register as reg                      # noqa: E402
import transkribus as tk                    # noqa: E402

TEI = """<?xml version="1.0" encoding="utf-8"?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="{id}">
 <teiHeader><fileDesc>
  <titleStmt><title>{title}</title></titleStmt>
  <sourceDesc><msDesc><msIdentifier>
   <repository>{repo}</repository><idno type="signature">{sig}</idno>
  </msIdentifier></msDesc></sourceDesc>
 </fileDesc><profileDesc><correspDesc>
  <correspAction type="sent"><persName ref="https://d-nb.info/gnd/{gnd}">{sender}</persName>
   <placeName>Eppishausen</placeName><date when="1834-03-21">1834-03-21</date></correspAction>
  <correspAction type="received"><persName>{recipient}</persName></correspAction>
 </correspDesc></profileDesc></teiHeader><text><body/></text></TEI>
"""


def _letter_file(folder, lid, *, sender="Joseph von Laßberg", gnd="118778862",
                 recipient="Wilhelm Wackernagel", repo="Badische Landesbibliothek", sig="K 2911,49"):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{lid}.xml").write_text(TEI.format(
        id=lid, title=f"Brief von {sender} an {recipient} (21.03.1834).", sender=sender,
        gnd=gnd, recipient=recipient, repo=repo, sig=sig), encoding="utf-8")


@pytest.fixture
def world(tmp_path):
    letters = tmp_path / "lassberg" / "data" / "letters"
    _letter_file(letters, "lassberg-letter-0892")
    _letter_file(letters, "lassberg-letter-1209", sender="Wilhelm Wackernagel", gnd="118805568",
                 recipient="Joseph von Laßberg", repo="Staatsarchiv des Kantons Basel-Stadt", sig="PA 82a B 9")
    src = tmp_path / "archive"
    for name in ("00013-scan_2025-03-21_11-11-12l.jpg", "00012-scan_2025-03-21_11-10-57r.jpg"):
        p = src / "Aarau" / "upload" / "lassberg-letter-0892" / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8 image")
        (p.parent / (name + ".json")).write_text("{}")
    for n in (5, 142, 6):
        p = src / "Basel" / "lassberg-letter-1209" / f"PA 82a B 9_Seite_{n:03d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8")
    p = src / "Aarau" / "upload" / "lassberg-letter-9999" / "x.jpg"       # not in the register
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
    p = src / "blb lassberg" / "K 2911,104" / "page_0001.jpg"            # no letter segment
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
    p = src / "Winterthur" / "101-MsBRH_466-56-071.jpg"                  # loose in the archive
    p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b"x")
    return tmp_path, letters, src


# ── the register carries what a document is named with ──────────────────────

def test_the_register_reads_title_recipient_repository_and_signature(world):
    _, letters, _ = world
    r = reg.read_register(letters)
    l = r.get("lassberg-letter-0892")
    assert l.title.startswith("Brief von Joseph von Laßberg an Wilhelm Wackernagel")
    assert l.recipient == "Wilhelm Wackernagel"
    assert l.repository == "Badische Landesbibliothek" and l.signature == "K 2911,49"
    assert l.by_lassberg


# ── grouping by letter, only where the letter is known ───────────────────────

def test_one_letter_per_id_named_by_the_id_with_pages_in_archive_order(world):
    _, letters, src = world
    found, un = tk.letters_by_register(src, reg.read_register(letters))
    assert [l.title for l in found] == ["lassberg-letter-0892", "lassberg-letter-1209"]
    a = found[0]
    assert [p.name for p in a.pages] == ["00012-scan_2025-03-21_11-10-57r.jpg",
                                         "00013-scan_2025-03-21_11-11-12l.jpg"]
    b = found[1]
    assert [p.name for p in b.pages] == ["PA 82a B 9_Seite_005.jpg", "PA 82a B 9_Seite_006.jpg",
                                         "PA 82a B 9_Seite_142.jpg"], "numeric, not ASCII, order"


def test_pages_without_a_settled_letter_are_counted_by_reason_not_uploaded(world):
    _, letters, src = world
    found, un = tk.letters_by_register(src, reg.read_register(letters))
    assert un.not_in_register == 1 and un.letters_not_in_register == {"lassberg-letter-9999"}
    assert un.no_letter_segment == 2, "the shelfmark folder and the loose Winterthur page"
    assert "lassberg-letter-9999" in tk.format_unassigned(un)
    assert not any("9999" in l.title or "K 2911" in l.title for l in found)


def test_the_metadata_comes_from_the_register(world):
    _, letters, src = world
    found, _ = tk.letters_by_register(src, reg.read_register(letters))
    md = found[0].md
    assert md["title"] == "lassberg-letter-0892"
    assert md["author"] == "Joseph von Laßberg"
    assert "Badische Landesbibliothek, K 2911,49" in md["description"]
    assert "2 page(s)" in md["description"]


def test_sidecars_are_not_pages(world):
    _, letters, src = world
    found, _ = tk.letters_by_register(src, reg.read_register(letters))
    assert all(p.suffix == ".jpg" for l in found for p in l.pages)


# ── the plan against the collection ──────────────────────────────────────────

def test_the_plan_skips_letters_already_there_and_can_be_one_letter(world, monkeypatch):
    _, letters, src = world
    monkeypatch.setattr(tk, "collection_documents",
                        lambda colid, sid, session=None: [{"title": "lassberg-letter-1209", "docId": 7}])
    plan, un = tk.plan_letters(src, "2536466", reg.read_register(letters), "sid")
    assert [l.title for l in plan.letters] == ["lassberg-letter-0892"]
    assert [l.title for l in plan.skipped] == ["lassberg-letter-1209"]
    one, _ = tk.plan_letters(src, "2536466", reg.read_register(letters), "sid", only="lassberg-letter-0892")
    assert [l.title for l in one.letters] == ["lassberg-letter-0892"] and one.skipped == []
    with pytest.raises(tk.TranskribusError):
        tk.plan_letters(src, "2536466", reg.read_register(letters), "sid", only="lassberg-letter-0001")


# ── the upload, request by request ───────────────────────────────────────────

class _Resp:
    def __init__(self, status, text):
        self.status_code, self.text = status, text

    def json(self):
        return json.loads(self.text)


class _Session:
    """Records every request; answers the create with an id and each page with
    the upload's state, the last one carrying a job id."""

    def __init__(self, create_body='{"uploadId": 4711}', pages=2):
        self.calls = []
        self.create_body = create_body
        self.pages = pages

    def post(self, url, params=None, json=None, headers=None, timeout=None):
        self.calls.append(("POST", url, params, json))
        return _Resp(200, self.create_body)

    def put(self, url, params=None, files=None, headers=None, timeout=None):
        self.calls.append(("PUT", url, params, {k: v[0] for k, v in files.items()}))
        done = sum(1 for c in self.calls if c[0] == "PUT")
        body = {"uploadId": 4711, "jobId": "99" if done == self.pages else None}
        return _Resp(200, json.dumps(body))


def test_create_upload_posts_metadata_and_the_page_list_in_order(world):
    _, letters, src = world
    found, _ = tk.letters_by_register(src, reg.read_register(letters))
    s = _Session()
    upload_id = tk.create_upload("2536466", found[0], "SID", session=s)
    assert upload_id == "4711"
    method, url, params, body = s.calls[0]
    assert method == "POST" and url.endswith("/uploads")
    assert params == {"collId": "2536466", "JSESSIONID": "SID"}
    assert body["md"]["title"] == "lassberg-letter-0892"
    assert [p["fileName"] for p in body["pageList"]["pages"]] == [
        "00012-scan_2025-03-21_11-10-57r.jpg", "00013-scan_2025-03-21_11-11-12l.jpg"]
    assert [p["pageNr"] for p in body["pageList"]["pages"]] == [1, 2]


def test_the_upload_id_is_read_from_json_or_xml_and_nothing_else():
    assert tk._upload_id('{"uploadId": 12}') == "12"
    assert tk._upload_id("<trpUpload><uploadId>13</uploadId></trpUpload>") == "13"
    with pytest.raises(tk.TranskribusError) as err:
        tk._upload_id("<html>Tomcat says hello</html>")
    assert "names no upload id" in str(err.value)


def test_upload_letter_puts_every_page_against_the_upload_and_returns_the_job(world):
    _, letters, src = world
    found, _ = tk.letters_by_register(src, reg.read_register(letters))
    s = _Session(pages=2)
    result = tk.upload_letter("2536466", found[0], "SID", session=s)
    puts = [c for c in s.calls if c[0] == "PUT"]
    assert len(puts) == 2
    assert all(c[1].endswith("/uploads/4711") for c in puts)
    assert [c[3]["img"] for c in puts] == ["00012-scan_2025-03-21_11-10-57r.jpg",
                                           "00013-scan_2025-03-21_11-11-12l.jpg"]
    assert "xml" not in puts[0][3], "digitisations only — no text goes up with them"
    assert result["upload_id"] == "4711" and result["pages"] == 2 and result["job_id"] == "99"


def test_a_refused_page_stops_with_the_servers_words(world):
    _, letters, src = world
    found, _ = tk.letters_by_register(src, reg.read_register(letters))

    class _Refusing(_Session):
        def put(self, url, params=None, files=None, headers=None, timeout=None):
            return _Resp(500, "<html>Internal Server Error: disk</html>")

    with pytest.raises(tk.TranskribusError) as err:
        tk.upload_letter("2536466", found[0], "SID", session=_Refusing())
    assert "refused by upload 4711" in str(err.value) and "disk" in str(err.value)


def test_the_cli_takes_one_letter_before_all():
    src = (PKG / "__main__.py").read_text(encoding="utf-8")
    body = src[src.index("def upload_transkribus("):src.index("def compare_runs_cmd(")]
    assert "--letter lassberg-letter-NNNN, then --all" in body
    assert "uploading folder-wise is not offered" in body


# ── one letter in two folders (25.10., lassberg-letter-1209) ─────────────────

def test_an_identical_copy_in_a_second_folder_is_uploaded_once(world):
    _, letters, src = world
    for n in (5, 142, 6):
        p = src / "Basel" / "upload" / "lassberg-letter-1209" / f"PA 82a B 9_Seite_{n:03d}.jpg"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"\xff\xd8")                      # the same bytes as the first copy
    found, un = tk.letters_by_register(src, reg.read_register(letters))
    b = next(l for l in found if l.title == "lassberg-letter-1209")
    assert [p.name for p in b.pages] == ["PA 82a B 9_Seite_005.jpg", "PA 82a B 9_Seite_006.jpg",
                                         "PA 82a B 9_Seite_142.jpg"]
    assert un.duplicate_copies == 3
    assert "uploaded once: 3 page(s)" in tk.format_unassigned(un)


def test_two_different_images_under_one_name_leave_the_letter_out_named(world):
    _, letters, src = world
    p = src / "Basel" / "upload" / "lassberg-letter-1209" / "PA 82a B 9_Seite_005.jpg"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8 a different scan")
    found, un = tk.letters_by_register(src, reg.read_register(letters))
    assert "lassberg-letter-1209" not in [l.title for l in found]
    assert un.conflicting == {"lassberg-letter-1209": ["PA 82a B 9_Seite_005.jpg"]}
    text = tk.format_unassigned(un)
    assert "two different images under one name: 1 letter(s)" in text and "1209" in text
    assert "lassberg-letter-0892" in [l.title for l in found], "the other letters are untouched"

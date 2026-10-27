import pytest

from competition import ingest
from competition.ingest import IngestError, guess_company_name, load_bundle, load_document


def test_pptx_extracts_text_tables_notes_and_flags_image_slide(sample_dir):
    doc = load_document("Cairnly_Seed_Deck.pptx", (sample_dir / "Cairnly_Seed_Deck.pptx").read_bytes(), "D")
    assert len(doc.pages) == 10
    assert doc.pages[0].ref == "D1" and doc.unit == "slide"
    assert "Distributor portals | Free with the distributor account" in doc.pages[5].text
    assert "22 practices" in doc.pages[2].notes
    assert not doc.pages[7].has_text
    assert any("slide 8 has little or no extractable text" in w for w in doc.warnings)


def test_pdf_deck_and_title_guess(sample_dir):
    bundle, errors = load_bundle(("Cairnly_Seed_Deck.pdf", (sample_dir / "Cairnly_Seed_Deck.pdf").read_bytes()), [])
    assert not errors
    assert bundle.primary.unit == "page"
    assert "Office managers spend about 5 hours" in bundle.pages()["D2"][1].text
    assert guess_company_name(bundle) == "Cairnly"


def test_supporting_refs_and_concise_formatting(bundle):
    assert "S1.2" in bundle.pages()
    assert bundle.format_refs(["D7", "D3", "S1.2", "D3", "BOGUS"]) == "Deck s.3, 7; Cairnly customer interviews p.2"


def test_rejects_unsupported_and_empty_files():
    with pytest.raises(IngestError, match="unsupported file type"):
        load_document("notes.docx", b"abc", "D")
    with pytest.raises(IngestError, match="empty"):
        load_document("deck.pdf", b"", "D")
    with pytest.raises(IngestError, match="not a readable PDF"):
        load_document("deck.pdf", b"not a pdf", "D")


def test_bad_supporting_file_is_reported_not_fatal(sample_dir):
    bundle, errors = load_bundle(("Cairnly_Seed_Deck.pptx", (sample_dir / "Cairnly_Seed_Deck.pptx").read_bytes()),
                                 [("broken.pdf", b"garbage")])
    assert len(bundle.documents) == 1
    assert errors and "broken.pdf" in errors[0]


def test_ppt_without_libreoffice_gives_save_as_instruction(monkeypatch):
    monkeypatch.setattr(ingest, "find_libreoffice", lambda: None)
    with pytest.raises(IngestError, match="save it as .pptx or .pdf"):
        load_document("old.ppt", b"\xd0\xcf\x11\xe0fake", "D")


@pytest.mark.skipif(ingest.find_libreoffice() is None, reason="LibreOffice not installed")
def test_legacy_ppt_round_trip_through_libreoffice(sample_dir, tmp_path):
    import subprocess
    soffice = ingest.find_libreoffice()
    profile = (tmp_path / "profile").as_uri()
    subprocess.run([soffice, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "ppt",
                    "--outdir", str(tmp_path), str(sample_dir / "Cairnly_Seed_Deck.pptx")],
                   capture_output=True, timeout=180, check=True)
    ppt = tmp_path / "Cairnly_Seed_Deck.ppt"
    doc = load_document("Cairnly_Seed_Deck.ppt", ppt.read_bytes(), "D")
    assert doc.kind == "ppt"
    assert any("Office managers spend about 5 hours" in p.text for p in doc.pages)
    assert "converted from legacy .ppt" in doc.warnings[0]

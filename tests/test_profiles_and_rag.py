import pytest

from morty import profiles, rag


def test_all_profiles_load():
    names = profiles.names()
    assert {"general", "dev", "reviewer"} <= set(names)
    for n in names:
        p = profiles.load(n)
        assert p.system and p.title
        assert "think" in p.options


def test_unknown_profile():
    with pytest.raises(KeyError):
        profiles.load("nope")


def test_reviewer_pins_model_and_dev_has_tools():
    assert profiles.load("reviewer").model == "qwen3:14b"
    assert profiles.load("dev").tools.get("enabled") is True
    assert profiles.load("general").tools == {}


def test_chunks_split_long_text_with_overlap():
    text = "\n\n".join(f"ย่อหน้า {i} " + "ก" * 300 for i in range(10))
    chunks = rag._chunks(text)
    assert len(chunks) > 1
    assert all(len(c) <= rag.CHUNK_CHARS + rag.OVERLAP_CHARS + 2 for c in chunks)
    assert "ย่อหน้า 0" in chunks[0] and "ย่อหน้า 9" in chunks[-1]


def test_chunks_handle_single_huge_paragraph():
    chunks = rag._chunks("x" * 5000)
    assert len(chunks) >= 4 and all(len(c) <= rag.CHUNK_CHARS for c in chunks)


def test_cosine():
    assert rag._cos([1, 0], [1, 0]) == pytest.approx(1.0)
    assert rag._cos([1, 0], [0, 1]) == pytest.approx(0.0)
    assert rag._cos([0, 0], [1, 1]) == 0.0


def test_augment_without_index_returns_question(tmp_path):
    prof = profiles.load("dev")
    prof2 = profiles.Profile(name="t", title="t", system="s", data_dir=tmp_path, rag={"enabled": True})
    assert rag.augment(prof2, "คำถาม") == ("คำถาม", [])
    assert prof.rag["min_score"] >= 0.5

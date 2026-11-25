from services.retrieval import _merge_texts, rrf
from services.vector_store import VectorStore, chunk_text


def test_chunk_text_size_overlap_and_progress():
    text = " ".join(f"word{i}" for i in range(400))
    chunks = chunk_text(text, size=200, overlap=40)
    assert len(chunks) > 5
    assert all(len(c) <= 200 for c in chunks)
    assert "word399" in chunks[-1]
    # komşu parçalar çakışmalı
    assert set(chunks[0].split()[-3:]) & set(chunks[1].split())


def test_chunk_text_prefers_sentence_boundaries():
    s1 = "First complete sentence about Harry Potter and Hogwarts."
    s2 = "Second complete sentence about Dumbledore and the Elder Wand."
    s3 = "Third complete sentence about Voldemort and the Horcruxes."
    chunks = chunk_text(f"{s1} {s2} {s3}", size=125, overlap=30)
    assert chunks[0].endswith(".")


def test_chunk_text_edge_cases():
    assert chunk_text("", 100, 10) == []
    assert chunk_text("kısa", 100, 10) == []  # 20 karakterden kısa parçalar atılır
    assert len(chunk_text("x" * 500, 100, 10)) >= 5  # boşluksuz metinde sonsuz döngü yok


def test_merge_texts_deduplicates_overlap():
    a = "Alpha beta gamma delta epsilon zeta eta theta."
    b = "delta epsilon zeta eta theta. Iota kappa lambda."
    assert _merge_texts([a, b]) == "Alpha beta gamma delta epsilon zeta eta theta. Iota kappa lambda."


def test_e5_prefix_applied_for_query_and_passage():
    assert VectorStore._prepare_for_embed(["hello"], "query") == ["query: hello"]
    assert VectorStore._prepare_for_embed(["hello"], "passage") == ["passage: hello"]


def test_rrf_prefers_documents_ranked_high_in_several_lists():
    d = lambda i: {"id": i, "content": i, "metadata": {}, "score": 0}  # noqa: E731
    fused = rrf([[d("a"), d("b"), d("c")], [d("b"), d("c"), d("a")], [d("b"), d("d")]])
    assert fused[0]["id"] == "b"
    assert {x["id"] for x in fused} == {"a", "b", "c", "d"}
    assert len(fused) == 4  # tekrar yok

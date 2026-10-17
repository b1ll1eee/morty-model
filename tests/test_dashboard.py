import json

from morty import dashboard


def test_pct():
    assert dashboard.pct([], 0.5) is None
    assert dashboard.pct([1, 2, 3, 4], 0.5) == 2.5
    assert dashboard.pct([5], 0.95) == 5


def test_load_eval_runs_parses_model_with_hyphen(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "RESULTS_DIR", tmp_path)
    rec = {"id": "a", "pass": True, "seconds": 1.0, "model": "qwen2.5-coder:7b", "rag": True}
    (tmp_path / "dev-rag-qwen2.5-coder_7b-20261017-165756.jsonl").write_text(json.dumps(rec) + "\n", encoding="utf-8")
    (tmp_path / "reviewer-rag-pipeline-qwen3_14b-20261017-190000.jsonl").write_text(json.dumps({**rec, "model": "qwen3:14b"}) + "\n", encoding="utf-8")
    (tmp_path / "dev-20261017-161433.jsonl").write_text(json.dumps({**rec, "model": "qwen2.5:7b"}) + "\n", encoding="utf-8")
    runs = {r["file"]: r for r in dashboard.load_eval_runs()}
    assert runs["dev-rag-qwen2.5-coder_7b-20261017-165756.jsonl"]["set"] == "dev"
    assert runs["reviewer-rag-pipeline-qwen3_14b-20261017-190000.jsonl"]["mode"] == "pipeline"
    assert runs["reviewer-rag-pipeline-qwen3_14b-20261017-190000.jsonl"]["set"] == "reviewer"
    assert runs["dev-20261017-161433.jsonl"]["set"] == "dev" and runs["dev-20261017-161433.jsonl"]["mode"] == "single"


def test_build_renders_without_data(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "RESULTS_DIR", tmp_path / "none")
    monkeypatch.setattr(dashboard.log, "LOG_DIR", tmp_path / "nolog")
    monkeypatch.setattr(dashboard, "ollama_status", lambda: {"up": False, "models": [], "loaded": []})
    page = dashboard.build(7)
    assert "<title>Morty Dashboard</title>" in page and "ยังไม่มีผล eval" in page

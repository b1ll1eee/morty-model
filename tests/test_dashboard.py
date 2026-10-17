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


def test_refresh_file_only_when_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "OUT_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "RESULTS_DIR", tmp_path / "none")
    monkeypatch.setattr(dashboard.log, "LOG_DIR", tmp_path / "nolog")
    monkeypatch.setattr(dashboard, "ollama_status", lambda: {"up": False, "models": [], "loaded": []})
    assert dashboard.refresh_file() is None
    assert dashboard.refresh_file(force=True) == tmp_path / "dashboard.html"
    assert dashboard.refresh_file() is not None  # ตอนนี้มีไฟล์แล้ว จึงสร้างใหม่


def test_serve_builds_page_per_request(tmp_path, monkeypatch):
    import http.client
    import threading

    monkeypatch.setattr(dashboard, "RESULTS_DIR", tmp_path / "none")
    monkeypatch.setattr(dashboard.log, "LOG_DIR", tmp_path / "nolog")
    monkeypatch.setattr(dashboard, "ollama_status", lambda: {"up": False, "models": [], "loaded": []})
    server = dashboard.serve(0, 7, 15, open_browser=False)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        conn.request("GET", "/")
        r = conn.getresponse()
        body = r.read().decode("utf-8")
        assert r.status == 200 and 'http-equiv="refresh" content="15"' in body and "Morty Dashboard" in body
        conn.request("GET", "/?refresh=0")
        body = conn.getresponse().read().decode("utf-8")
        assert 'http-equiv="refresh"' not in body
        conn.request("GET", "/nope")
        assert conn.getresponse().status == 404
    finally:
        server.shutdown()
        server.server_close()

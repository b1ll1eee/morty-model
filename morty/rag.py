"""RAG อย่างง่าย: ทำดัชนีเอกสารใน data/<profile>/ แล้วดึงท่อนที่เกี่ยวข้องมาใส่ใน prompt

ใช้งาน:  .venv/bin/python -m morty.rag index dev            # สร้าง/อัปเดตดัชนี
         .venv/bin/python -m morty.rag search dev "คำค้น"    # ลองค้นดูว่าได้ท่อนไหน

ดัชนีเก็บเป็น JSON ไว้ใน data/<profile>/.morty-index.json (ไม่ commit)
ไฟล์ที่เนื้อหาไม่เปลี่ยนจะไม่ถูกคำนวณ embedding ซ้ำ
ใช้ nomic-embed-text ผ่าน llm.embed() และคำนวณ cosine similarity ด้วย Python ล้วน
พอสำหรับเอกสารหลักร้อยถึงหลักพันท่อน ถ้าใหญ่กว่านั้นค่อยย้ายไป vector DB
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path

from . import llm, profiles

INDEX_NAME = ".morty-index.json"
EMBED_MODEL = "nomic-embed-text"
TEXT_SUFFIXES = {
    ".md", ".txt", ".rst", ".py", ".js", ".ts", ".tsx", ".jsx", ".json", ".toml", ".yaml", ".yml",
    ".sh", ".sql", ".go", ".rs", ".java", ".kt", ".swift", ".c", ".h", ".cpp", ".cs", ".rb", ".php",
    ".html", ".css", ".env.example", ".cfg", ".ini",
}
CHUNK_CHARS = 1200   # nomic-embed-text รับได้ราว 2048 token ท่อนละ ~1200 ตัวอักษรปลอดภัยทั้งไทยและโค้ด
OVERLAP_CHARS = 150
DEFAULTS = {"top_k": 4, "min_score": 0.6}


@dataclass
class Hit:
    path: str
    chunk: int
    text: str
    score: float


# ---------- ดัชนี ----------

def _index_path(data_dir: Path) -> Path:
    return data_dir / INDEX_NAME


def _iter_files(data_dir: Path):
    for p in sorted(data_dir.rglob("*")):
        if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in TEXT_SUFFIXES:
            yield p


def _chunks(text: str) -> list[str]:
    """ตัดเป็นท่อนตามย่อหน้า แล้วรวมจนใกล้ CHUNK_CHARS มี overlap เล็กน้อยระหว่างท่อน"""
    paras = [p.strip() for p in text.replace("\r\n", "\n").split("\n\n")]
    paras = [p for p in paras if p]
    out: list[str] = []
    buf = ""
    for p in paras:
        while len(p) > CHUNK_CHARS:  # ย่อหน้าเดียวยาวเกิน (เช่นไฟล์โค้ด) ตัดตรงๆ
            if buf:
                out.append(buf)
                buf = ""
            out.append(p[:CHUNK_CHARS])
            p = p[CHUNK_CHARS - OVERLAP_CHARS:]
        if len(buf) + len(p) + 2 > CHUNK_CHARS and buf:
            out.append(buf)
            buf = buf[-OVERLAP_CHARS:] + "\n\n" + p
        else:
            buf = (buf + "\n\n" + p) if buf else p
    if buf:
        out.append(buf)
    return out


def load_index(data_dir: Path) -> dict | None:
    path = _index_path(data_dir)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def build_index(data_dir: Path, verbose: bool = True) -> dict:
    """สร้างหรืออัปเดตดัชนี คืน dict {"model":..., "files": {relpath: {"sha": ..., "chunks": [{"text","vec"}]}}}"""
    old = load_index(data_dir) or {}
    old_files = old.get("files", {}) if old.get("model") == EMBED_MODEL else {}
    files: dict[str, dict] = {}
    todo: list[tuple[str, list[str]]] = []

    for p in _iter_files(data_dir):
        rel = p.relative_to(data_dir).as_posix()
        raw = p.read_bytes()
        sha = hashlib.sha1(raw).hexdigest()
        if rel in old_files and old_files[rel]["sha"] == sha:
            files[rel] = old_files[rel]
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            if verbose:
                print(f"ข้าม (ไม่ใช่ utf-8): {rel}", file=sys.stderr)
            continue
        chunks = _chunks(text)
        files[rel] = {"sha": sha, "chunks": [{"text": c} for c in chunks]}
        todo.append((rel, chunks))

    for rel, chunks in todo:
        if not chunks:
            continue
        vecs = llm.embed([f"search_document: {c}" for c in chunks], model=EMBED_MODEL)
        for entry, v in zip(files[rel]["chunks"], vecs, strict=False):
            entry["vec"] = [round(x, 6) for x in v]
        if verbose:
            print(f"ทำดัชนี {rel}: {len(chunks)} ท่อน")

    index = {"model": EMBED_MODEL, "files": files}
    _index_path(data_dir).write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    if verbose:
        n = sum(len(f["chunks"]) for f in files.values())
        print(f"ดัชนีพร้อม: {len(files)} ไฟล์ {n} ท่อน (ใหม่ {len(todo)} ไฟล์)")
    return index


# ---------- ค้นหา ----------

def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search(data_dir: Path, query: str, top_k: int = 4, min_score: float = 0.0) -> list[Hit]:
    index = load_index(data_dir)
    if not index:
        return []
    qv = llm.embed([f"search_query: {query}"], model=EMBED_MODEL)[0]
    hits: list[Hit] = []
    for rel, f in index["files"].items():
        for i, c in enumerate(f["chunks"]):
            if "vec" not in c:
                continue
            s = _cos(qv, c["vec"])
            if s >= min_score:
                hits.append(Hit(rel, i, c["text"], s))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:top_k]


# ---------- ประกอบ prompt ----------

def settings(profile: profiles.Profile) -> dict:
    return {**DEFAULTS, **profile.rag}


def available(profile: profiles.Profile) -> bool:
    """profile นี้ใช้ RAG ได้ไหม: เปิดใช้, มี data_dir และมีดัชนีแล้ว"""
    return bool(
        profile.rag.get("enabled", True)
        and profile.data_dir
        and _index_path(profile.data_dir).is_file()
    )


def augment(profile: profiles.Profile, question: str) -> tuple[str, list[Hit]]:
    """คืน (ข้อความ user ที่แนบเอกสารอ้างอิงแล้ว, รายการท่อนที่ใช้) ถ้าไม่เจออะไรคืนคำถามเดิม"""
    if not available(profile):
        return question, []
    cfg = settings(profile)
    hits = search(profile.data_dir, question, top_k=cfg["top_k"], min_score=cfg["min_score"])
    if not hits:
        return question, []
    refs = "\n\n".join(f"[{i + 1}] {h.path} (ท่อน {h.chunk + 1})\n{h.text}" for i, h in enumerate(hits))
    prompt = (
        "ข้อมูลอ้างอิงจากเอกสารในโปรเจกต์ ถ้าเกี่ยวข้องกับคำถามให้ใช้ตอบและอ้างหมายเลข [n] "
        "ถ้าไม่เกี่ยวข้องให้ตอบจากความรู้ทั่วไปตามปกติ โดยไม่ต้องพูดถึงเอกสารเหล่านี้\n\n"
        f"{refs}\n\n---\nคำถาม: {question}"
    )
    return prompt, hits


# ---------- CLI ----------

def main() -> None:
    parser = argparse.ArgumentParser(description="ทำดัชนีและค้นเอกสารของ profile")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_index = sub.add_parser("index", help="สร้าง/อัปเดตดัชนีของ profile")
    p_index.add_argument("profile", choices=profiles.names())
    p_search = sub.add_parser("search", help="ลองค้นดูว่าได้ท่อนไหน")
    p_search.add_argument("profile", choices=profiles.names())
    p_search.add_argument("query")
    p_search.add_argument("--top-k", type=int, default=None)
    args = parser.parse_args()

    prof = profiles.load(args.profile)
    if not prof.data_dir:
        raise SystemExit(f"profile {prof.name} ไม่ได้กำหนด data_dir")
    prof.data_dir.mkdir(parents=True, exist_ok=True)

    if args.cmd == "index":
        build_index(prof.data_dir)
    else:
        cfg = settings(prof)
        hits = search(prof.data_dir, args.query, top_k=args.top_k or cfg["top_k"], min_score=0.0)
        if not hits:
            print("ไม่มีดัชนี หรือไม่พบท่อนที่เกี่ยวข้อง รัน: python -m morty.rag index", prof.name)
        for h in hits:
            flag = "" if h.score >= cfg["min_score"] else "  (ต่ำกว่า min_score จะไม่ถูกใช้)"
            print(f"\n== {h.score:.3f}  {h.path} ท่อน {h.chunk + 1}{flag}\n{h.text[:400]}")


if __name__ == "__main__":
    main()

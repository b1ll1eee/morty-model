"""สร้าง dashboard (HTML ไฟล์เดียว เปิดในเบราว์เซอร์) จาก log การใช้งาน ผล eval และสถานะ Ollama

ใช้งาน:  .venv/bin/python -m morty.dashboard            # เขียน reports/dashboard.html
         .venv/bin/python -m morty.dashboard --open     # แล้วเปิดในเบราว์เซอร์
         .venv/bin/python -m morty.dashboard --days 14  # ดูย้อนหลัง 14 วัน (ค่าเริ่มต้น 30)

ทุกอย่างคำนวณในเครื่อง ไม่ส่งข้อมูลออกไปไหน กราฟเป็น SVG ที่สร้างจาก Python ล้วน ไม่โหลดอะไรจากเน็ต
"""
from __future__ import annotations

import argparse
import html
import json
import re
import statistics
import webbrowser
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from . import config, llm, log

OUT_DIR = config.ROOT / "reports"
RESULTS_DIR = config.ROOT / "evals" / "results"
COLD_LOAD_SECONDS = 120  # เกินนี้ถือว่ารวมเวลาโหลด weight ไม่ใช่เวลาตอบจริง

# สีตามชุดที่ตรวจแล้ว (dataviz reference palette) ลำดับคงที่: slot1 น้ำเงิน slot2 ส้ม slot3 เขียวน้ำทะเล slot4 เหลือง
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"]
SERIES_DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"]
STATUS = {"good": "#0ca30c", "bad": "#d03b3b", "warn": "#fab219"}
MODEL_SLOT = {"qwen2.5:7b": 0, "qwen2.5-coder:7b": 1, "qwen3:14b": 2}
PROFILE_SLOT = {"general": 0, "dev": 1, "reviewer": 2}
SEV_SLOT = {"bug": "#d03b3b", "risk": "#ec835a", "style": "#2a78d6"}


# ---------- โหลดข้อมูล ----------

def load_eval_runs() -> list[dict]:
    """หนึ่งรายการต่อหนึ่งไฟล์ผล: set, mode, model, stamp, records"""
    runs = []
    if not RESULTS_DIR.is_dir():
        return runs
    # ชื่อโมเดลมีขีดได้ (qwen2.5-coder_7b) จึงยึด stamp ท้ายไฟล์เป็นหลัก
    pat = re.compile(r"^(?P<set>.+?)-(?P<tag>rag|norag)(?P<mode>-tools|-pipeline|-thorough)?(?:-x(?P<rep>\d+))?(?:-(?P<model>.+?))?-(?P<stamp>\d{8}-\d{6})\.jsonl$")
    for f in sorted(RESULTS_DIR.glob("*.jsonl")):
        m = pat.match(f.name)
        rows = [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows:
            continue
        if m:
            set_name, mode, stamp = m.group("set"), (m.group("mode") or "").lstrip("-") or "single", m.group("stamp")
        else:  # ไฟล์รุ่นแรก dev-YYYYMMDD-HHMMSS.jsonl
            parts = f.stem.rsplit("-", 2)
            set_name, mode, stamp = parts[0], "single", "-".join(parts[1:])
        model = rows[0].get("model", "?")
        ts = datetime.strptime(stamp, "%Y%m%d-%H%M%S")
        runs.append({"set": set_name, "mode": mode, "model": model, "ts": ts, "file": f.name, "records": rows,
                     "rag": rows[0].get("rag", True), "digest": rows[0].get("model_digest")})
    return runs


def ollama_status() -> dict:
    status = {"up": False, "models": [], "loaded": []}
    try:
        status["models"] = [(m["model"], round(m.get("size", 0) / 1e9, 1)) for m in llm._client.list()["models"]]
        status["up"] = True
        status["loaded"] = [m["model"] for m in llm._client.ps().get("models", [])]
    except Exception:  # noqa: BLE001
        pass
    return status


# ---------- สถิติ ----------

def pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def fmt_s(v: float | None) -> str:
    return "-" if v is None else f"{v:.1f}s"


# ---------- SVG ----------

def esc(s: object) -> str:
    return html.escape(str(s), quote=True)


def bar_chart_h(rows: list[tuple[str, float, str, str]], unit: str = "", width: int = 560, max_value: float | None = None) -> str:
    """แท่งแนวนอน rows = [(label, value, color, tooltip)] ค่ามากไปน้อยตามที่ส่งมา"""
    if not rows:
        return '<p class="empty">ยังไม่มีข้อมูล</p>'
    label_w, pad, bar_h, gap = 170, 8, 18, 8
    vmax = max_value or max(v for _, v, _, _ in rows) or 1
    h = pad * 2 + len(rows) * (bar_h + gap)
    plot_w = width - label_w - 70
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="แผนภูมิแท่ง">']
    for i, (label, v, color, tip) in enumerate(rows):
        y = pad + i * (bar_h + gap)
        w = max(2, plot_w * v / vmax)
        out.append(f'<text x="{label_w - 8}" y="{y + bar_h - 4}" text-anchor="end" class="lbl">{esc(label)}</text>')
        out.append(f'<rect x="{label_w}" y="{y}" width="{w:.1f}" height="{bar_h}" rx="3" fill="{color}"><title>{esc(tip)}</title></rect>')
        out.append(f'<text x="{label_w + w + 6:.1f}" y="{y + bar_h - 4}" class="val">{v:g}{unit}</text>')
    out.append("</svg>")
    return "".join(out)


def stacked_days(days: list[str], series: dict[str, list[int]], colors: dict[str, str], width: int = 560) -> str:
    """แท่งซ้อนรายวัน series = {ชื่อ: [ค่าแต่ละวัน]}"""
    if not days or not any(sum(v) for v in series.values()):
        return '<p class="empty">ยังไม่มีข้อมูล</p>'
    pad_l, pad_b, pad_t, h = 36, 28, 10, 180
    plot_w, plot_h = width - pad_l - 10, h - pad_b - pad_t
    totals = [sum(series[k][i] for k in series) for i in range(len(days))]
    vmax = max(totals) or 1
    slot = plot_w / len(days)
    bw = max(4, min(28, slot * 0.7))
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="จำนวนคำขอรายวันแยกตาม profile">']
    for g in range(0, 5):
        y = pad_t + plot_h - plot_h * g / 4
        out.append(f'<line x1="{pad_l}" x2="{width - 10}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{round(vmax * g / 4)}</text>')
    for i, day in enumerate(days):
        x = pad_l + i * slot + (slot - bw) / 2
        y = pad_t + plot_h
        for name, vals in series.items():
            v = vals[i]
            if not v:
                continue
            seg = plot_h * v / vmax
            y -= seg
            out.append(f'<rect x="{x:.1f}" y="{y + 1:.1f}" width="{bw:.1f}" height="{max(0, seg - 2):.1f}" rx="2" fill="{colors[name]}"><title>{esc(day)} {esc(name)}: {v}</title></rect>')
        if len(days) <= 16 or i % max(1, len(days) // 8) == 0:
            out.append(f'<text x="{x + bw / 2:.1f}" y="{h - 8}" text-anchor="middle" class="tick">{day[5:]}</text>')
    out.append("</svg>")
    return "".join(out)


def score_lines(runs: list[dict], width: int = 560) -> str:
    """คะแนน eval ต่อรอบ จุดต่อเส้น แยกสีตามโมเดล"""
    if not runs:
        return '<p class="empty">ยังไม่มีผล</p>'
    pad_l, pad_r, pad_t, pad_b, h = 40, 12, 12, 30, 170
    plot_w, plot_h = width - pad_l - pad_r, h - pad_t - pad_b
    runs = sorted(runs, key=lambda r: r["ts"])
    n = len(runs)
    xs = [pad_l + (plot_w * i / max(1, n - 1) if n > 1 else plot_w / 2) for i in range(n)]
    out = [f'<svg viewBox="0 0 {width} {h}" role="img" aria-label="คะแนน eval ตามลำดับรอบ">']
    for g in (0, 50, 100):
        y = pad_t + plot_h - plot_h * g / 100
        out.append(f'<line x1="{pad_l}" x2="{width - pad_r}" y1="{y:.1f}" y2="{y:.1f}" class="grid"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y + 4:.1f}" text-anchor="end" class="tick">{g}%</text>')
    by_model: dict[str, list[tuple[float, float, dict]]] = defaultdict(list)
    for i, r in enumerate(runs):
        rec = r["records"]
        score = 100 * sum(x["pass"] for x in rec) / len(rec)
        y = pad_t + plot_h - plot_h * score / 100
        by_model[r["model"]].append((xs[i], y, r))
    for model, pts in by_model.items():
        color = SERIES[MODEL_SLOT.get(model, 3)]
        if len(pts) > 1:
            d = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}" for i, (x, y, _) in enumerate(pts))
            out.append(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2"/>')
        for x, y, r in pts:
            rec = r["records"]
            tip = f"{model} {r['ts']:%d/%m %H:%M} {r['mode']}: {sum(v['pass'] for v in rec)}/{len(rec)}"
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="5" fill="{color}" stroke="var(--surface)" stroke-width="2"><title>{esc(tip)}</title></circle>')
    for i, r in enumerate(runs):
        if n <= 10 or i % max(1, n // 6) == 0 or i == n - 1:
            out.append(f'<text x="{xs[i]:.1f}" y="{h - 10}" text-anchor="middle" class="tick">{r["ts"]:%d/%m %H:%M}</text>')
    out.append("</svg>")
    legend = "".join(f'<span class="key"><i style="background:{SERIES[MODEL_SLOT.get(m, 3)]}"></i>{esc(m)}</span>' for m in by_model)
    return out_join(out) + f'<div class="legend">{legend}</div>'


def out_join(parts: list[str]) -> str:
    return "".join(parts)


# ---------- ประกอบหน้า ----------

def build(days: int) -> str:
    now = datetime.now()
    since = now - timedelta(days=days)
    exchanges = [e for e in log.exchanges() if datetime.fromisoformat(e["ts"]) >= since]
    runs = load_eval_runs()
    status = ollama_status()

    # --- KPI ---
    chat_secs = [e["seconds"] for e in exchanges if e["source"] == "chat" and e["seconds"] < COLD_LOAD_SECONDS]
    rated = [e for e in exchanges if e["rating"]]
    bad = [e for e in exchanges if e["rating"] == "bad"]
    latest_by_set: dict[str, dict] = {}
    for r in sorted(runs, key=lambda r: r["ts"]):
        latest_by_set[f"{r['set']}·{r['mode']}"] = r

    kpis = [
        ("คำขอ", f"{len(exchanges)}", f"{days} วันล่าสุด"),
        ("ให้คะแนนแล้ว", f"{len(rated)}", f"bad {len(bad)} ({(100 * len(bad) / len(rated)) if rated else 0:.0f}%)"),
        ("latency แชท p50", fmt_s(pct(chat_secs, 0.5)), f"p95 {fmt_s(pct(chat_secs, 0.95))} · ตัดช่วงโหลด weight"),
    ]
    for key, r in list(latest_by_set.items())[-4:]:
        rec = r["records"]
        kpis.append((f"eval {key}", f"{sum(x['pass'] for x in rec)}/{len(rec)}", f"{r['model']} · {r['ts']:%d/%m %H:%M}"))

    # --- latency ต่อโมเดล (รวม log และ eval ตัด cold load) ---
    lat: dict[str, list[float]] = defaultdict(list)
    for e in exchanges:
        if e["seconds"] < COLD_LOAD_SECONDS:
            lat[e["model"]].append(e["seconds"])
    for r in runs:
        for x in r["records"]:
            if x["seconds"] < COLD_LOAD_SECONDS:
                lat[x["model"]].append(x["seconds"])
    lat_rows = []
    for model in sorted(lat, key=lambda m: pct(lat[m], 0.5) or 0):
        color = SERIES[MODEL_SLOT.get(model, 3)]
        n = len(lat[model])
        lat_rows.append((f"{model} p50", round(pct(lat[model], 0.5), 1), color, f"{model}: n={n}"))
        lat_rows.append((f"{model} p95", round(pct(lat[model], 0.95), 1), color + "99", f"{model}: n={n}"))

    # --- ใช้งานรายวันตาม profile ---
    day_list = [(since + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days + 1)]
    day_list = [d for d in day_list if d <= now.strftime("%Y-%m-%d")]
    usage: dict[str, list[int]] = {p: [0] * len(day_list) for p in PROFILE_SLOT}
    for e in exchanges:
        d = e["ts"][:10]
        if d in day_list:
            usage.setdefault(e["profile"], [0] * len(day_list))[day_list.index(d)] += 1
    usage_colors = {p: SERIES[PROFILE_SLOT.get(p, 3)] for p in usage}

    # --- rating ต่อ profile ---
    rating_rows = []
    for p in sorted({e["profile"] for e in exchanges}):
        g = sum(1 for e in exchanges if e["profile"] == p and e["rating"] == "good")
        b = sum(1 for e in exchanges if e["profile"] == p and e["rating"] == "bad")
        rating_rows.append((f"{p} ✓ good", g, STATUS["good"], f"{p}: good {g}"))
        rating_rows.append((f"{p} ✗ bad", b, STATUS["bad"], f"{p}: bad {b}"))

    # --- เครื่องมือ ---
    tool_counts = Counter(t for e in exchanges for t in e.get("tools", []))
    tool_rows = [(t, c, SERIES[0], f"{t}: {c} ครั้ง") for t, c in tool_counts.most_common()]
    tool_turns = sum(1 for e in exchanges if e.get("tools"))

    # --- RAG ---
    dev_ex = [e for e in exchanges if e["source"] == "chat" and e["profile"] == "dev"]
    rag_hits = sum(1 for e in dev_ex if e["refs"])
    ref_counts = Counter(r.split("#")[0] for e in dev_ex for r in e["refs"])
    ref_rows = [(doc, c, SERIES[1], f"{doc}: ถูกอ้าง {c} ครั้ง") for doc, c in ref_counts.most_common(8)]

    # --- reviewer ---
    reviews = [e for e in exchanges if e["source"] == "review"]
    sev = Counter()
    dropped = []
    for e in reviews:
        meta = e.get("meta") or {}
        if meta.get("findings"):
            sev.update(meta["findings"])
            dropped.append(meta.get("dropped", 0))
        else:
            for s in ("bug", "risk", "style"):
                sev[s] += len(re.findall(rf"\[{s}\]", e["answer"]))
    sev_rows = [(s, sev[s], SEV_SLOT[s], f"{s}: {sev[s]}") for s in ("bug", "risk", "style")]

    # --- flaky cases: case เดียวกัน โมเดลเดียวกัน set เดียวกัน ผลไม่เท่ากันระหว่างรอบ ---
    outcomes: dict[tuple, list[bool]] = defaultdict(list)
    for r in runs:
        for x in r["records"]:
            outcomes[(r["set"], r["model"], x["id"])].append(bool(x["pass"]))
    flaky = sorted((k, v) for k, v in outcomes.items() if len(v) > 1 and len(set(v)) > 1)
    always_fail = sorted(k for k, v in outcomes.items() if len(v) >= 1 and not any(v))

    # --- ตาราง eval ล่าสุดต่อ set ---
    eval_tables = []
    for key, r in latest_by_set.items():
        rows = "".join(
            f"<tr><td>{esc(x['id'])}</td><td class='{'ok' if x['pass'] else 'ng'}'>{'ผ่าน' if x['pass'] else 'ตก'}</td>"
            f"<td class='num'>{x['seconds']:.1f}s</td><td class='muted'>{esc('; '.join(x.get('fails', [])))}</td></tr>"
            for x in r["records"])
        eval_tables.append(
            f"<details><summary>{esc(key)} · {esc(r['model'])} · {r['ts']:%d/%m %H:%M} · "
            f"{sum(x['pass'] for x in r['records'])}/{len(r['records'])}"
            f"{' · digest ' + esc(r['digest']) if r.get('digest') else ''}</summary>"
            f"<table><thead><tr><th>ข้อ</th><th>ผล</th><th>เวลา</th><th>หมายเหตุ</th></tr></thead><tbody>{rows}</tbody></table></details>")

    # --- bad ล่าสุด ---
    bad_rows = "".join(
        f"<tr><td class='muted'>{esc(e['ts'][5:16])}</td><td>{esc(e['profile'])}</td><td>{esc(e['model'])}</td>"
        f"<td>{esc(e['question'][:90].replace(chr(10), ' '))}</td><td>{esc(e['note'] or '-')}</td></tr>"
        for e in sorted(bad, key=lambda e: e["ts"], reverse=True)[:15])

    # --- สรุปผล eval ทั้งหมดเป็นตาราง (table view ของกราฟ) ---
    run_rows = "".join(
        f"<tr><td class='muted'>{r['ts']:%d/%m %H:%M}</td><td>{esc(r['set'])}</td><td>{esc(r['mode'])}</td><td>{esc(r['model'])}</td>"
        f"<td class='num'>{sum(x['pass'] for x in r['records'])}/{len(r['records'])}</td>"
        f"<td class='num'>{statistics.median(x['seconds'] for x in r['records']):.1f}s</td></tr>"
        for r in sorted(runs, key=lambda r: r["ts"], reverse=True))

    sets = sorted({r["set"] for r in runs})
    score_sections = "".join(
        f"<figure><figcaption>{esc(s)}</figcaption>{score_lines([r for r in runs if r['set'] == s])}</figure>" for s in sets)

    models_html = ", ".join(f"{esc(n)} ({sz} GB)" for n, sz in status["models"]) or "-"
    loaded_html = ", ".join(esc(m) for m in status["loaded"]) or "ไม่มีโมเดลค้างใน RAM"
    kpi_html = "".join(f"<div class='kpi'><div class='k'>{esc(k)}</div><div class='v'>{esc(v)}</div><div class='s'>{esc(s)}</div></div>" for k, v, s in kpis)
    flaky_html = "".join(f"<li><code>{esc(k[0])}</code> · {esc(k[1])} · <code>{esc(k[2])}</code> ผ่าน {sum(v)}/{len(v)} รอบ</li>" for k, v in flaky[:12]) or "<li class='muted'>ยังไม่มี case ที่ผลแกว่ง</li>"
    fail_html = "".join(f"<li><code>{esc(k[0])}</code> · {esc(k[1])} · <code>{esc(k[2])}</code></li>" for k in always_fail[:12]) or "<li class='muted'>ไม่มี</li>"

    dark_series = ";".join(f"--s{i}:{c}" for i, c in enumerate(SERIES_DARK))
    light_series = ";".join(f"--s{i}:{c}" for i, c in enumerate(SERIES))

    return f"""<!doctype html>
<html lang="th"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Morty Dashboard</title>
<style>
:root{{--bg:#f6f7f9;--surface:#ffffff;--fg:#1d2530;--muted:#5d6877;--line:#d7dce3;--grid:#e6eaf0;--accent:#0e7c6b;{light_series}}}
@media (prefers-color-scheme: dark){{:root{{--bg:#151a21;--surface:#1d232c;--fg:#e6eaef;--muted:#9aa6b4;--line:#3a4350;--grid:#2a313c;--accent:#4fc3ad;{dark_series};color-scheme:dark}}}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 "IBM Plex Sans Thai","Noto Sans Thai",system-ui,sans-serif;padding:24px max(16px,env(safe-area-inset-left)) 48px}}
main{{max-width:1180px;margin:0 auto}}
h1{{font-size:1.5rem;margin:0 0 4px}} h2{{font-size:1.05rem;margin:0 0 8px}}
.sub{{color:var(--muted);margin:0 0 20px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-bottom:16px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-bottom:20px}}
.kpi{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:12px 14px}}
.kpi .k{{font-size:.75rem;letter-spacing:.04em;text-transform:uppercase;color:var(--muted)}}
.kpi .v{{font-size:1.6rem;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.2}}
.kpi .s{{font-size:.8rem;color:var(--muted)}}
.card{{background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:16px;min-width:0}}
.card p.note{{color:var(--muted);font-size:.85rem;margin:0 0 8px}}
svg{{width:100%;height:auto;display:block;font-size:12px}}
svg text{{fill:var(--fg)}} svg .lbl{{font-size:12px}} svg .val{{font-size:11px;fill:var(--muted)}} svg .tick{{font-size:10px;fill:var(--muted)}}
svg .grid{{stroke:var(--grid);stroke-width:1}}
figure{{margin:0 0 12px}} figcaption{{font-size:.85rem;color:var(--muted);margin-bottom:4px}}
.legend{{display:flex;flex-wrap:wrap;gap:12px;font-size:.8rem;color:var(--muted);margin-top:6px}}
.key i{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}}
table{{border-collapse:collapse;width:100%;font-size:.85rem}} th,td{{text-align:left;padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}}
thead th{{border-top:0;color:var(--muted);font-weight:500;font-size:.75rem;letter-spacing:.03em}}
td.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}} td.ok{{color:{STATUS['good']}}} td.ng{{color:{STATUS['bad']};font-weight:600}}
.muted{{color:var(--muted)}} .empty{{color:var(--muted);font-size:.9rem}}
details{{margin:6px 0}} summary{{cursor:pointer;color:var(--accent)}}
code{{font-family:"IBM Plex Mono",ui-monospace,Menlo,monospace;font-size:.85em}}
.scroll{{overflow-x:auto}} ul{{margin:0;padding-left:18px}}
.status{{display:inline-block;width:8px;height:8px;border-radius:50%;background:{STATUS['good'] if status['up'] else STATUS['bad']};margin-right:6px}}
</style></head><body><main>
<h1>Morty Dashboard</h1>
<p class="sub">สร้างเมื่อ {now:%d %b %Y %H:%M} · ช่วง {days} วันล่าสุด · <span class="status"></span>Ollama {'ทำงาน' if status['up'] else 'ไม่ตอบ'} · โมเดลในเครื่อง: {models_html} · ใน RAM ตอนนี้: {loaded_html}</p>

<div class="kpis">{kpi_html}</div>

<section class="grid">
  <div class="card"><h2>คะแนน eval ตามรอบ</h2><p class="note">แต่ละ set แยกกราฟ จุด = หนึ่งรอบ สี = โมเดล ชี้เพื่อดูโหมด (single / pipeline / tools)</p>{score_sections or '<p class="empty">ยังไม่มีผล eval</p>'}</div>
  <div class="card"><h2>latency ต่อโมเดล</h2><p class="note">รวมจากแชทและ eval ตัดค่าที่เกิน {COLD_LOAD_SECONDS}s ซึ่งคือตอนโหลด weight ครั้งแรก</p>{bar_chart_h(lat_rows, "s")}</div>
</section>
<section class="grid">
  <div class="card"><h2>คำขอรายวันตาม profile</h2>{stacked_days(day_list, usage, usage_colors)}<div class="legend">{''.join(f'<span class="key"><i style="background:{c}"></i>{esc(p)}</span>' for p, c in usage_colors.items())}</div></div>
  <div class="card"><h2>คะแนนจากผู้ใช้</h2><p class="note">จาก /bad /good ในแชท และ morty.log rate</p>{bar_chart_h(rating_rows)}</div>
</section>
<section class="grid">
  <div class="card"><h2>เครื่องมือที่ dev เรียกใช้</h2><p class="note">{tool_turns} คำตอบจาก {sum(1 for e in exchanges if e['profile'] == 'dev' and e['source'] == 'chat')} ครั้งของ dev ใช้เครื่องมืออย่างน้อยหนึ่งตัว</p>{bar_chart_h(tool_rows)}</div>
  <div class="card"><h2>RAG</h2><p class="note">dev ดึงเอกสารมาใช้ {rag_hits}/{len(dev_ex)} คำตอบ ({(100 * rag_hits / len(dev_ex)) if dev_ex else 0:.0f}%) เอกสารที่ถูกอ้างบ่อย</p>{bar_chart_h(ref_rows)}</div>
</section>
<section class="grid">
  <div class="card"><h2>reviewer: finding ตามระดับ</h2><p class="note">{len(reviews)} การตรวจ · pipeline ถอน finding ที่ยืนยันไม่ได้เฉลี่ย {statistics.mean(dropped) if dropped else 0:.1f} ต่อครั้ง</p>{bar_chart_h(sev_rows)}</div>
  <div class="card"><h2>สุขภาพของ eval</h2>
    <p class="note">case ที่ผลแกว่งระหว่างรอบ (โมเดลเดิม ชุดเดิม) บอกว่า eval นั้นวัดไม่นิ่ง หรือโมเดลไม่เสถียร</p><ul>{flaky_html}</ul>
    <p class="note" style="margin-top:10px">case ที่ไม่เคยผ่านเลย</p><ul>{fail_html}</ul></div>
</section>
<section class="card" style="margin-bottom:16px"><h2>คำตอบที่ติดธง bad ล่าสุด</h2><div class="scroll"><table><thead><tr><th>เวลา</th><th>profile</th><th>โมเดล</th><th>คำถาม</th><th>หมายเหตุ</th></tr></thead><tbody>{bad_rows or '<tr><td colspan="5" class="muted">ยังไม่มี</td></tr>'}</tbody></table></div>
<p class="note" style="margin-top:8px">แปลงเป็น eval: <code>python -m morty.log to-eval &lt;profile&gt;</code></p></section>
<section class="card" style="margin-bottom:16px"><h2>ผล eval รายข้อ (รอบล่าสุดของแต่ละ set)</h2>{''.join(eval_tables) or '<p class="empty">ยังไม่มี</p>'}</section>
<section class="card"><h2>ทุกรอบ eval</h2><div class="scroll"><table><thead><tr><th>เวลา</th><th>set</th><th>โหมด</th><th>โมเดล</th><th>ผ่าน</th><th>เวลากลาง</th></tr></thead><tbody>{run_rows}</tbody></table></div></section>
</main></body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="สร้าง dashboard จาก log และผล eval")
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--open", action="store_true", help="เปิดในเบราว์เซอร์หลังสร้าง")
    parser.add_argument("--out", default=str(OUT_DIR / "dashboard.html"))
    args = parser.parse_args()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(build(args.days), encoding="utf-8")
    print(f"สร้างแล้ว: {out}")
    if args.open:
        webbrowser.open(out.resolve().as_uri())


if __name__ == "__main__":
    main()

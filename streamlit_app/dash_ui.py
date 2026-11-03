"""Visual layer: CSS theme + small HTML/SVG components (cards, KPI with sparkline, rounded bar chart, progress rows,
leaderboards). Everything returns HTML strings; `render()` compacts them so Markdown does not treat indentation as code."""
from __future__ import annotations

import html as _html
import itertools
import math

import streamlit as st

BLUE, BLUE_SOFT = "#2F5BEA", "#E8EEFF"
INK, MUTED, LINE = "#111827", "#6B7280", "#EEF0F4"
GREEN, RED, AMBER = "#22C55E", "#EF4444", "#F59E0B"
_uid = itertools.count(1)

CSS = f"""
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
.stApp, .stApp .stMarkdown, .stApp .stMarkdown *, .stApp label, .stApp input, .stApp button p, .stApp [data-baseweb="select"] *,
.stApp [data-testid="stMetric"] * {{ font-family: 'Inter', -apple-system, 'Segoe UI', sans-serif; }}
.stApp {{ background: #F3F4F6; color: {INK}; }}
header[data-testid="stHeader"], [data-testid="stToolbar"], [data-testid="stDecoration"], footer {{ display: none !important; }}
.block-container {{ padding: 1.8rem 2.2rem 3rem; max-width: 1320px; }}
[data-testid="stSidebar"] {{ background: #fff; border-right: 0; box-shadow: 0 0 40px rgba(16,24,40,.04); }}
[data-testid="stSidebar"] > div:first-child {{ padding-top: .6rem; }}
[data-testid="stPageLink-NavLink"], [data-testid="stSidebarNavLink"] {{ border-radius: 12px; padding: .62rem .85rem; color: #374151; font-weight: 500; margin-bottom: 2px; }}
[data-testid="stPageLink-NavLink"]:hover, [data-testid="stSidebarNavLink"]:hover {{ background: #F5F7FF; }}
[data-testid="stPageLink-NavLink"][aria-current="page"], [data-testid="stSidebarNavLink"][aria-current="page"] {{ background: {BLUE_SOFT}; color: {BLUE}; font-weight: 600; }}
[data-testid="stSidebarNavSeparator"] {{ color: #9CA3AF; font-weight: 500; font-size: 1.02rem; padding-top: 1.3rem; }}
.brand {{ display:flex; align-items:center; gap:.7rem; padding: .9rem .6rem 1rem; }}
.brand .mark {{ width:38px; height:38px; border-radius:12px; background: linear-gradient(135deg,{BLUE},#6C8DFF); display:flex; align-items:center; justify-content:center; color:#fff; font-weight:700; }}
.brand .name {{ font-weight:700; font-size:1.02rem; line-height:1.1; }} .brand .sub {{ color:{MUTED}; font-size:.72rem; }}
.page-title {{ font-size: 1.9rem; font-weight: 700; letter-spacing: -.02em; margin: 0; line-height:1.2; }}
.page-rule {{ border:0; border-top:1px solid #E5E7EB; margin: .9rem 0 1.2rem; }}
.card {{ background:#fff; border-radius: 20px; padding: 20px 22px; box-shadow: 0 1px 2px rgba(16,24,40,.04), 0 6px 20px rgba(16,24,40,.04); height:100%; }}
.card h4 {{ margin:0 0 .9rem; font-size:.95rem; font-weight:500; color:#4B5563; }}
.kpi {{ height: 172px; display:flex; flex-direction:column; }}
.kpi .label {{ color:#4B5563; font-size:.9rem; font-weight:500; }}
.kpi .value {{ font-size:2rem; font-weight:700; letter-spacing:-.02em; margin: .5rem 0 .1rem; }}
.kpi .value small {{ font-size:1.05rem; color:#9CA3AF; font-weight:500; margin-left:2px; }}
.kpi .note {{ color:{MUTED}; font-size:.78rem; }}
.kpi svg {{ margin-top:auto; width:100%; height:46px; }}
.meter {{ height:8px; border-radius:99px; background:#EEF2FF; margin-top:auto; overflow:hidden; }}
.meter > span {{ display:block; height:100%; border-radius:99px; background: linear-gradient(90deg,{BLUE},#6C8DFF); }}
.banner {{ background: linear-gradient(120deg,#2F5BEA 0%,#5B7FF5 100%); color:#fff; border-radius:20px; padding: 22px 26px; margin-bottom: 14px; box-shadow: 0 10px 30px rgba(47,91,234,.25); }}
.banner .eyebrow {{ opacity:.8; font-size:.75rem; letter-spacing:.08em; text-transform:uppercase; font-weight:600; }}
.banner .headline {{ font-size:1.5rem; font-weight:700; letter-spacing:-.01em; margin-top:.3rem; }}
.banner .sim {{ opacity:.85; font-size:.78rem; margin-top:.6rem; }}
.story h5 {{ margin:0 0 .35rem; font-size:.78rem; color:{BLUE}; text-transform:uppercase; letter-spacing:.06em; font-weight:600; }}
.story p {{ margin:0; color:#374151; font-size:.92rem; line-height:1.5; }}
.prow {{ display:flex; align-items:center; gap:14px; margin-bottom:18px; }}
.prow .dot {{ width:38px; height:38px; border-radius:10px; flex:none; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:.8rem; }}
.prow .body {{ flex:1; min-width:0; }} .prow .t {{ font-weight:600; font-size:.92rem; margin-bottom:6px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.bar {{ height:8px; border-radius:99px; overflow:hidden; }} .bar > span {{ display:block; height:100%; border-radius:99px; }}
.prow .pct {{ color:{MUTED}; font-size:.85rem; width:92px; white-space:nowrap; text-align:right; flex:none; }} .prow .pct b {{ color:{INK}; }}
.lrow {{ display:flex; align-items:center; gap:14px; margin-bottom:16px; }}
.lrow .av {{ width:42px; height:42px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center; color:#fff; font-weight:700; font-size:.85rem; }}
.lrow .who {{ flex:1; min-width:0; }} .lrow .n {{ font-weight:600; font-size:.95rem; }} .lrow .s {{ color:{MUTED}; font-size:.8rem; }}
.lrow .rank {{ display:flex; align-items:center; gap:6px; font-weight:600; }} .up {{ color:{GREEN}; font-size:.7rem; }} .down {{ color:{RED}; font-size:.7rem; }} .flat {{ color:#9CA3AF; font-size:.7rem; }}
.flow {{ display:flex; gap:10px; align-items:stretch; flex-wrap:wrap; }} .flow .step {{ flex:1; min-width:150px; background:#F8FAFF; border:1px solid #E3EAFF; border-radius:16px; padding:14px 16px; }}
.flow .step b {{ display:block; font-size:.92rem; }} .flow .step span {{ color:{MUTED}; font-size:.8rem; }} .flow .arrow {{ align-self:center; color:#9CA3AF; font-size:1.3rem; }}
.gloss {{ display:grid; grid-template-columns: 200px 1fr; gap:10px 18px; }} .gloss b {{ font-weight:600; }} .gloss span {{ color:#4B5563; }}
div[data-baseweb="select"] > div {{ background:#fff !important; border:1px solid {LINE} !important; border-radius:16px !important; min-height:48px; box-shadow:0 1px 2px rgba(16,24,40,.03); font-weight:500; }}
.stDownloadButton button, .stButton button {{ border-radius: 12px; border:1px solid #E5E7EB; background:#fff; color:{INK}; font-weight:500; }}
[data-testid="stDataFrame"] {{ border-radius:16px; overflow:hidden; }}
[data-testid="stExpander"] {{ background:#fff; border-radius:16px; border:0; box-shadow:0 1px 2px rgba(16,24,40,.04); }}
"""


def render(html: str) -> None:
    """Render HTML through Markdown safely: strip indentation / newlines so nothing becomes a code block."""
    st.markdown("".join(line.strip() for line in html.splitlines()), unsafe_allow_html=True)


def inject_css() -> None:
    st.markdown(f"<style>{CSS}</style>", unsafe_allow_html=True)


def esc(s) -> str:
    return _html.escape(str(s))


# ---------------------------------------------------------------- SVG
def _smooth_path(pts: list[tuple[float, float]]) -> str:
    d = f"M{pts[0][0]:.1f},{pts[0][1]:.1f}"
    for i in range(1, len(pts) - 1):
        mx, my = (pts[i][0] + pts[i + 1][0]) / 2, (pts[i][1] + pts[i + 1][1]) / 2
        d += f" Q{pts[i][0]:.1f},{pts[i][1]:.1f} {mx:.1f},{my:.1f}"
    d += f" L{pts[-1][0]:.1f},{pts[-1][1]:.1f}"
    return d


def sparkline(values: list[float], color: str = BLUE, w: int = 190, h: int = 46) -> str:
    if len(values) < 2:
        return ""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    pts = [(i * w / (len(values) - 1), h - 5 - (v - lo) / rng * (h - 14)) for i, v in enumerate(values)]
    line = _smooth_path(pts)
    gid = f"g{next(_uid)}"
    return (f'<svg viewBox="0 0 {w} {h}" preserveAspectRatio="none"><defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0" stop-color="{color}" stop-opacity=".25"/><stop offset="1" stop-color="{color}" stop-opacity="0"/>'
            f'</linearGradient></defs><path d="{line} L{w},{h} L0,{h} Z" fill="url(#{gid})"/>'
            f'<path d="{line}" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round"/></svg>')


def nice_max(v: float) -> float:
    if v <= 0:
        return 1.0
    exp = 10 ** math.floor(math.log10(v))
    for m in (1, 2, 2.5, 5, 10):
        if v <= m * exp:
            return m * exp
    return 10 * exp


def bar_chart(labels: list[str], values: list[float], width: int = 560, height: int = 290, fmt=lambda v: f"{v:,.0f}") -> str:
    """Rounded bars on a light track (like the template's Activity chart)."""
    n = len(values)
    if n == 0:
        return ""
    left, bottom, top = 46, 30, 12
    ymax = nice_max(max(values))
    plot_h, plot_w = height - bottom - top, width - left - 20
    slot = plot_w / n
    bw = max(4, min(18, slot * 0.55))
    gid = f"b{next(_uid)}"
    out = [f'<svg viewBox="0 0 {width} {height}" style="width:100%;height:auto"><defs><linearGradient id="{gid}" x1="0" y1="0" x2="0" y2="1">'
           f'<stop offset="0" stop-color="#6C8DFF"/><stop offset="1" stop-color="{BLUE}"/></linearGradient></defs>']
    for i in range(5):
        v = ymax * i / 4
        y = top + plot_h - v / ymax * plot_h
        out.append(f'<text x="{left - 10}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#9CA3AF">{fmt(v)}</text>')
    step = max(1, math.ceil(n / 12))
    for i, (lab, v) in enumerate(zip(labels, values, strict=True)):
        cx = left + slot * i + slot / 2
        x = cx - bw / 2
        hgt = max(0.0, v / ymax * plot_h)
        out.append(f'<rect x="{x:.1f}" y="{top}" width="{bw:.1f}" height="{plot_h}" rx="{bw / 2:.1f}" fill="#EEF3FF"/>')
        out.append(f'<rect x="{x:.1f}" y="{top + plot_h - hgt:.1f}" width="{bw:.1f}" height="{hgt:.1f}" rx="{bw / 2:.1f}" fill="url(#{gid})">'
                   f'<title>{esc(lab)}: {fmt(v)}</title></rect>')
        if i % step == 0:
            out.append(f'<text x="{cx:.1f}" y="{height - 8}" text-anchor="middle" font-size="10.5" fill="#9CA3AF">{esc(lab)}</text>')
    out.append("</svg>")
    return "".join(out)


# ---------------------------------------------------------------- components
def card(title: str, body: str, extra_class: str = "", min_height: int = 0) -> str:
    style = f' style="min-height:{min_height}px"' if min_height else ""
    return f'<div class="card {extra_class}"{style}>' + (f"<h4>{esc(title)}</h4>" if title else "") + body + "</div>"


def kpi(label: str, value: str, unit: str = "", note: str = "", visual: str = "") -> str:
    u = f"<small>{esc(unit)}</small>" if unit else ""
    n = f'<div class="note">{note}</div>' if note else ""
    return f'<div class="card kpi"><div class="label">{esc(label)}</div><div class="value">{esc(value)}{u}</div>{n}{visual}</div>'


def meter(frac: float) -> str:
    return f'<div class="meter"><span style="width:{max(0, min(1, frac)) * 100:.1f}%"></span></div>'


def progress_rows(items: list[tuple[str, float, str]], suffix: str) -> str:
    """items: (title, fraction 0..1, initials). Colour by absolute level: <85% red, <95% amber, otherwise green."""
    rows = []
    for title, frac, ini in items:
        if frac < 0.85:
            grad, track, dotbg, dotfg = "linear-gradient(90deg,#FB923C,#EF4444)", "#FEE2E2", "#FEF2F2", "#DC2626"
        elif frac < 0.95:
            grad, track, dotbg, dotfg = "linear-gradient(90deg,#FCD34D,#F59E0B)", "#FEF3C7", "#FFFBEB", "#B45309"
        else:
            grad, track, dotbg, dotfg = "linear-gradient(90deg,#4ADE80,#22C55E)", "#DCFCE7", "#F0FDF4", "#16A34A"
        rows.append(f'<div class="prow"><div class="dot" style="background:{dotbg};color:{dotfg}">{esc(ini)}</div>'
                    f'<div class="body"><div class="t">{esc(title)}</div><div class="bar" style="background:{track}">'
                    f'<span style="width:{frac * 100:.0f}%;background:{grad}"></span></div></div>'
                    f'<div class="pct"><b>{frac * 100:.0f}%</b> {esc(suffix)}</div></div>')
    return "".join(rows)


def count_rows(items: list[tuple[str, float, str, str]], color: str = "#2F5BEA") -> str:
    """items: (title, count, initials, unit label). Bars are relative to the largest count."""
    top = max((c for _, c, _, _ in items), default=1) or 1
    rows = []
    for title, count, ini, unit in items:
        rows.append(f'<div class="prow"><div class="dot" style="background:{BLUE_SOFT};color:{BLUE}">{esc(ini)}</div>'
                    f'<div class="body"><div class="t">{esc(title)}</div><div class="bar" style="background:#EEF3FF">'
                    f'<span style="width:{count / top * 100:.0f}%;background:linear-gradient(90deg,#6C8DFF,{color})"></span></div></div>'
                    f'<div class="pct"><b>{count:,.0f}</b> {esc(unit)}</div></div>')
    return "".join(rows)


AV_COLORS = ["#2F5BEA", "#12A594", "#F59E0B", "#8E4EC6", "#EF4444", "#0EA5E9"]


def leader_rows(rows: list[dict]) -> str:
    """rows: {rank, name, sub, trend (+1/-1/0)}"""
    out = []
    for r in rows:
        arrow = {1: '<span class="up">▲</span>', -1: '<span class="down">▼</span>'}.get(r.get("trend", 0), '<span class="flat">●</span>')
        ini = r.get("ini") or "".join(w[0] for w in str(r["name"]).replace("_", " ").split()[:2]).upper() or "?"
        col = AV_COLORS[(r["rank"] - 1) % len(AV_COLORS)]
        out.append(f'<div class="lrow"><div class="av" style="background:{col}">{esc(ini)}</div>'
                   f'<div class="who"><div class="n">{esc(r["name"])}</div><div class="s">{r["sub"]}</div></div>'
                   f'<div class="rank">{r["rank"]} {arrow}</div></div>')
    return "".join(out)


def brand(name: str, sub: str) -> str:
    return f'<div class="brand"><div class="mark">Ω</div><div><div class="name">{esc(name)}</div><div class="sub">{esc(sub)}</div></div></div>'


def flow(steps: list[tuple[str, str]]) -> str:
    parts = []
    for i, (t, d) in enumerate(steps):
        parts.append(f'<div class="step"><b>{esc(t)}</b><span>{esc(d)}</span></div>')
        if i < len(steps) - 1:
            parts.append('<div class="arrow">→</div>')
    return f'<div class="flow">{"".join(parts)}</div>'


def glossary(pairs: list[tuple[str, str]]) -> str:
    return '<div class="gloss">' + "".join(f"<b>{esc(a)}</b><span>{esc(b)}</span>" for a, b in pairs) + "</div>"

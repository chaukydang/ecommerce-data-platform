"""Renders the architecture diagrams (HTML -> PNG) into docs/images/arch_*.png.   python docs/diagrams/build.py"""
from pathlib import Path

from playwright.sync_api import sync_playwright

OUT = Path(__file__).resolve().parents[1] / "images"
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{width:1600px;height:900px;background:#fff;font-family:'Inter',sans-serif;color:#111827;position:relative;overflow:hidden}
h1{position:absolute;left:60px;top:38px;font-size:34px;font-weight:700;letter-spacing:-.5px}
.sub{position:absolute;left:60px;top:86px;font-size:17px;color:#6B7280}
.abs{position:absolute}
.lane{position:absolute;top:140px;height:560px;background:#F4F7FF;border:1.5px solid #DCE3F5;border-radius:22px}
.lane h2{font-size:18px;font-weight:700;padding:22px 24px 4px}
.lane .cap{font-size:13.5px;color:#6B7280;padding:0 24px}
.num{display:inline-flex;width:26px;height:26px;border-radius:50%;background:#2F5BEA;color:#fff;font-size:14px;font-weight:700;align-items:center;justify-content:center;margin-right:9px;vertical-align:1px}
.card{position:absolute;background:#fff;border:1.5px solid #DCE3F5;border-radius:14px;box-shadow:0 2px 8px rgba(47,91,234,.06)}
.card b{display:block;font-size:17px;font-weight:600}
.card small{display:block;font-size:13.5px;color:#6B7280;margin-top:4px;line-height:1.35}
.pill{display:inline-block;font-size:13px;font-weight:500;background:#EEF2FF;color:#2F5BEA;border-radius:999px;padding:5px 12px}
.pill.g{background:#E8F7EE;color:#15803D}.pill.a{background:#FEF3E2;color:#B45309}.pill.r{background:#FDECEC;color:#B91C1C}
.dot{display:inline-block;width:12px;height:12px;border-radius:4px;margin-right:12px;vertical-align:-1px}
.bar{position:absolute;left:60px;width:1480px;height:64px;border-radius:16px;background:#2F5BEA;color:#fff;font-size:18px;font-weight:600;display:flex;align-items:center;justify-content:center;gap:14px}
svg.arrows{position:absolute;left:0;top:0;width:1600px;height:900px;pointer-events:none}
"""
ARROW = '<defs><marker id="h" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M1,1 L10,6 L1,11 Z" fill="#2F5BEA"/></marker><marker id="hr" markerUnits="userSpaceOnUse" markerWidth="12" markerHeight="12" refX="10" refY="6" orient="auto"><path d="M1,1 L10,6 L1,11 Z" fill="#DC2626"/></marker></defs>'


def line(x1, y1, x2, y2, c="#2F5BEA", w=3, dash=""):
    m = "hr" if c == "#DC2626" else "h"
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="{w}"{d} marker-end="url(#{m})"/>'


def path(d, c="#2F5BEA", w=3, dash=""):
    m = "hr" if c == "#DC2626" else "h"
    dd = f' stroke-dasharray="{dash}"' if dash else ""
    return f'<path d="{d}" fill="none" stroke="{c}" stroke-width="{w}" stroke-linejoin="round"{dd} marker-end="url(#{m})"/>'


def page(body: str, arrows: str = "") -> str:
    return f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>{body}<svg class='arrows'>{ARROW}{arrows}</svg></body></html>"


# ---------------------------------------------------------------- 1. overview
def overview() -> str:
    lanes = [(60, 270, "1", "Sources", "Where orders come from"), (370, 330, "2", "Ingestion framework", "Python, one run per source"),
             (800, 270, "3", "BigQuery warehouse", "Each layer feeds the next"), (1170, 0, "", "", "")]
    b = "<h1>Omnichannel E-commerce Data Platform</h1><div class='sub'>From many sales channels to one trusted view of orders and money</div>"
    xs = [(60, 270), (365, 330), (730, 330), (1095, 215), (1345, 195)]
    names = [("1", "Sources", "Where orders come from"), ("2", "Ingestion framework", "Python, one run per source"),
             ("3", "BigQuery warehouse", "Each layer feeds the next"), ("4", "Control", "Keeps data trusted"),
             ("5", "Consumers", "Who uses the result")]
    for (x, w), (n, t, c) in zip(xs, names, strict=True):
        b += f"<div class='lane' style='left:{x}px;width:{w}px'><h2><span class='num'>{n}</span>{t}</h2><div class='cap'>{c}</div></div>"
    # lane 1
    ch = [("Shopee", "#EE4D2D"), ("TikTok Shop", "#111827"), ("Lazada", "#2E3BD6"), ("Website", "#0EA5E9"), ("POS (stores)", "#16A34A")]
    for i, (n, col) in enumerate(ch):
        b += f"<div class='card' style='left:84px;top:{222 + i * 62}px;width:222px;height:50px;padding:14px 16px;font-weight:600;font-size:16px'><span class='dot' style='background:{col}'></span>{n}</div>"
    b += ("<div class='card' style='left:84px;top:540px;width:222px;height:86px;padding:14px 16px;border-color:#86EFAC;background:#F0FDF4'><b>Olist</b>"
          "<small>Real marketplace data (Kaggle CSV)</small></div>")
    b += "<div class='abs' style='left:84px;top:640px;width:222px;font-size:12.5px;color:#6B7280;line-height:1.4'>Other channels: simulated demo data</div>"
    # lane 2
    items = ["Retry with backoff", "Pagination", "Watermark checkpoint", "Validation + quarantine", "Idempotent sink"]
    for i, t in enumerate(items):
        b += f"<div class='card' style='left:392px;top:{222 + i * 62}px;width:276px;height:50px;padding:14px 16px;font-weight:500;font-size:15.5px'><span style='color:#16A34A;font-weight:700;margin-right:10px'>✓</span>{t}</div>"
    b += ("<div class='card' style='left:392px;top:540px;width:276px;height:86px;padding:14px 16px'><b>PostgreSQL</b>"
          "<small>Checkpoints, run audit, alert state</small></div>")
    # lane 3 layers
    layers = [("raw_*", "Append-only versions"), ("platform_staging", "Latest version of each record"), ("platform_intermediate", "One unified order"),
              ("platform_marts", "Facts, dimensions, daily sales"), ("platform_reporting", "Views for the dashboard")]
    for i, (t, d) in enumerate(layers):
        b += f"<div class='card' style='left:756px;top:{236 + i * 82}px;width:278px;height:62px;padding:11px 16px'><b style='font-size:16px'>{t}</b><small style='margin-top:1px'>{d}</small></div>"
    # lane 4
    b += ("<div class='card' style='left:1120px;top:222px;width:165px;height:104px;padding:14px'><b>dbt tests</b><small>Unique, not null, valid values</small></div>"
          "<div class='card' style='left:1120px;top:346px;width:165px;height:150px;padding:14px;border-color:#93C5FD'><b>Reconciliation engine</b><small>Do orders, payments and refunds agree? Rules in YAML</small></div>"
          "<div class='card' style='left:1120px;top:516px;width:165px;height:110px;padding:14px'><b>Monitoring</b><small>Is data late? Is data clean?</small></div>")
    # lane 5
    b += ("<div class='card' style='left:1368px;top:222px;width:150px;height:200px;padding:14px;background:#2F5BEA;border:none'><b style='color:#fff'>Streamlit dashboard</b>"
          "<small style='color:#DBE4FF'>Sales, money matching, pipeline health</small><div style='margin-top:18px;font-size:12.5px;color:#DBE4FF'>reads the reporting views</div></div>"
          "<div class='card' style='left:1368px;top:446px;width:150px;height:180px;padding:14px;background:#FFF7ED;border-color:#FDBA74'><b>Lark alerts</b>"
          "<small>Grouped, with cooldown and recovery notices</small><div style='margin-top:14px;font-size:12.5px;color:#9A3412'>from reconciliation and monitoring</div></div>")
    b += "<div class='bar' style='top:730px'>Apache Airflow orchestrates every step <span style='font-weight:400;opacity:.85'>(7 DAGs: ingest, transform, reconcile, monitor)</span></div>"
    a = ""
    for x in (333, 698, 1063, 1318):                      # one arrow per lane gap
        a += line(x - 22, 445, x + 20, 445, w=4)
    # layer arrows in lane 3
    for i in range(4):
        y = 236 + i * 82 + 62
        a += line(895, y + 2, 895, y + 20, w=3)
    return page(b, a)


# ---------------------------------------------------------------- 2. ingestion
def ingestion() -> str:
    b = "<h1>How one ingestion run works</h1><div class='sub'>Every run is safe to repeat: a crash never loses data and a replay never creates duplicates</div>"
    steps = [("1", "Read checkpoint", "Start from the last watermark minus a small overlap, so late updates are re-read", None),
             ("2", "Call the source API", "Bearer auth, timeouts", ("a", "429 / 5xx: retry with backoff + jitter")),
             ("3", "Paginate", "Cursor, page number or Link header, until no more pages", None),
             ("4", "Validate schema", "Required fields, types, allowed values", ("r", "Bad rows: quarantine, never block good rows")),
             ("5", "Write raw rows", "Idempotent: one row per record id + content hash", ("g", "Replays never create duplicates")),
             ("6", "Advance checkpoint", "Only after every page is written and validated", None),
             ("7", "Write audit row", "Rows extracted, loaded, rejected, retries", None),
             ("8", "Alert if failed", "Lark message, grouped, with recovery notice", None)]
    xs = [70, 460, 850, 1240]
    ys = [150, 430]
    for i, (n, t, d, tag) in enumerate(steps):
        x, y = xs[i % 4], ys[i // 4]
        tagh = ""
        if tag:
            tagh = f"<div style='margin-top:12px'><span class='pill {tag[0]}'>{tag[1]}</span></div>"
        col = "#FECACA" if i == 7 else "#DCE3F5"
        b += (f"<div class='card' style='left:{x}px;top:{y}px;width:290px;height:200px;padding:20px;border-color:{col}'>"
              f"<div style='margin-bottom:12px'><span class='num' style='width:34px;height:34px;font-size:17px'>{n}</span></div><b style='font-size:20px'>{t}</b><small style='font-size:15px'>{d}</small>{tagh}</div>")
    b += ("<div class='card' style='left:70px;top:672px;width:700px;height:96px;padding:18px 22px;background:#F4F7FF'><b>PostgreSQL</b>"
          "<small>Checkpoints and run audit live here: many small transactional writes</small></div>"
          "<div class='card' style='left:840px;top:672px;width:690px;height:96px;padding:18px 22px;background:#F4F7FF'><b>BigQuery raw_*</b>"
          "<small>Append-only: every distinct version of a record is kept, staging picks the latest</small></div>")
    a = ""
    for r, y in enumerate((250, 530)):
        for c in range(3):
            x = xs[c] + 290
            a += line(x + 6, y, x + 84, y, w=4)
    a += path("M1385,350 L1385,390 L215,390 L215,428", w=4)      # 4 -> 5 elbow through the gutter
    return page(b, a)


# ---------------------------------------------------------------- 3. warehouse layers
def layers() -> str:
    b = "<h1>Warehouse layers (dbt)</h1><div class='sub'>Raw data is never changed; every layer after it is rebuilt from the one before</div>"
    cols = [("raw", "Exactly what the source sent", ["orders", "payments", "refunds"], "Keeps every version"),
            ("staging", "Cleaned, latest version only", ["stg_marketplace_orders", "stg_payments", "stg_refunds"], "ROW_NUMBER picks the latest"),
            ("intermediate", "One row per order", ["int_unified_orders"], "Order + payments + refunds"),
            ("marts", "Ready for analysis", ["fct_platform_orders", "fct_payments", "fct_refunds", "dim_channel", "dim_date", "mart_daily_sales",
                                             "mart_financial_reconciliation"], ""),
            ("reporting views", "What the dashboard reads", ["v_sales_daily", "v_recon_kpi", "v_recon_findings", "v_pipeline_health"], "")]
    xs = [60, 357, 654, 951, 1248]
    for (x, (t, c, items, note)) in zip(xs, cols, strict=True):
        b += f"<div class='lane' style='left:{x}px;top:135px;width:262px;height:530px'><h2 style='font-size:21px'>{t}</h2><div class='cap'>{c}</div>"
        for i, it in enumerate(items):
            b += f"<div class='card' style='left:18px;top:{92 + i * 58}px;width:226px;height:48px;padding:13px 12px;font-weight:500;font-size:14.5px;position:absolute'>{it}</div>"
        if note:
            b += f"<div class='abs' style='left:24px;top:{92 + len(items) * 58 + 4}px;font-size:13.5px;color:#2F5BEA;font-weight:500'>{note}</div>"
        b += "</div>"
    b += "<div class='bar' style='top:690px;background:#fff;color:#111827;border:2px solid #86EFAC;background:#F0FDF4'>✓&nbsp; dbt tests on every layer: unique, not null, accepted values, relationships</div>"
    a = "".join(line(x + 262 + 4, 400, x + 262 + 30, 400, w=4) for x in xs[:4])
    return page(b, a)


# ---------------------------------------------------------------- 4. reconciliation
def reconciliation() -> str:
    b = "<h1>Financial reconciliation</h1><div class='sub'>Do the orders, payments and refunds agree? A late sync is not a financial error</div>"
    for i, (t, d) in enumerate([("Orders", "What customers bought"), ("Payments", "What was paid"), ("Refunds", "What was returned")]):
        b += f"<div class='card' style='left:60px;top:{170 + i * 118}px;width:230px;height:98px;padding:20px'><b style='font-size:19px'>{t}</b><small>{d}</small></div>"
    b += ("<div class='card' style='left:380px;top:170px;width:360px;height:334px;padding:24px;border-color:#93C5FD'><b style='font-size:21px'>Rule engine</b>"
          "<small style='margin-bottom:14px'>Rules are written in YAML, no code change</small>"
          + "".join(f"<div style='margin:7px 0'><span class='pill'>{r}</span></div>" for r in
                    ["Missing payment", "Amount mismatch", "Duplicate payment", "Currency mismatch", "Refund exceeds paid"]) + "</div>")
    b += ("<div class='card' style='left:830px;top:215px;width:240px;height:244px;padding:22px;text-align:center;background:#FFFBEB;border-color:#FCD34D'>"
          "<b style='font-size:20px;margin-top:34px'>Inside the grace window?</b><small>Orders are given time to sync</small></div>")
    b += ("<div class='card' style='left:1160px;top:170px;width:380px;height:118px;padding:20px;background:#FFFBEB;border-color:#FCD34D'><span class='pill a'>TIMING</span>"
          "<b style='margin-top:10px'>Yes: sync lag</b><small>No alert, checked again next run</small></div>"
          "<div class='card' style='left:1160px;top:386px;width:380px;height:118px;padding:20px;background:#FEF2F2;border-color:#FCA5A5'><span class='pill r'>CONFIRMED</span>"
          "<b style='margin-top:10px'>No: a real finding</b><small>Counts in the unresolved amount and triggers alerts</small></div>")
    # lifecycle
    b += "<div class='abs' style='left:205px;top:556px;font-size:18px;font-weight:700'>What happens to a confirmed finding</div>"
    steps = [("OPEN", "New, not yet looked at", "r"), ("INVESTIGATING", "Someone owns it", "a"), ("RESOLVED", "Fixed at the source", "g"), ("ACCEPTED_EXCEPTION", "Known, no action needed", "")]
    for i, (t, d, c) in enumerate(steps):
        b += f"<div class='card' style='left:{60 + i * 270}px;top:605px;width:230px;height:110px;padding:18px'><span class='pill {c}'>{t}</span><small style='margin-top:12px'>{d}</small></div>"
    b += ("<div class='card' style='left:1150px;top:605px;width:390px;height:110px;padding:18px;background:#FFF7ED;border-color:#FDBA74'><b>Lark alert + dashboard</b>"
          "<small>Grouped alerts with cooldown. The Money matching page shows % of orders that match</small></div>")
    b += "<div class='abs' style='left:60px;top:745px;width:1480px;font-size:15px;color:#6B7280'>Every status change is kept in a history table, so you can always see who changed what and when.</div>"
    a = ""
    for y in (219, 337, 455):
        a += path(f"M290,{y} L335,{y} L335,337 L378,337", w=3)
    a += line(742, 337, 828, 337, w=4)
    a += '<path d="M1070,337 L1115,337 L1115,229 L1115,445" fill="none" stroke="#9CA3AF" stroke-width="3"/>'
    a += path("M1115,229 L1158,229", c="#D97706", w=3)
    a += path("M1115,445 L1158,445", c="#DC2626", w=3)
    a += path("M1345,506 L1345,603", c="#DC2626", w=3)                         # confirmed -> alert
    a += path("M1250,506 L1250,540 L175,540 L175,603", c="#DC2626", w=3)       # confirmed -> lifecycle
    for i in range(3):
        a += line(60 + i * 270 + 230 + 4, 660, 60 + (i + 1) * 270 - 6, 660, w=3)
    return page(b, a)


HEIGHT = {"arch_overview": 830, "arch_ingestion": 800, "arch_layers": 790, "arch_reconciliation": 800}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        br = p.chromium.launch()
        pg = br.new_page(viewport={"width": 1600, "height": 900}, device_scale_factor=2)
        for name, fn in {"arch_overview": overview, "arch_ingestion": ingestion, "arch_layers": layers, "arch_reconciliation": reconciliation}.items():
            pg.set_content(fn())
            pg.wait_for_timeout(300)
            pg.screenshot(path=str(OUT / f"{name}.png"), clip={"x": 0, "y": 0, "width": 1600, "height": HEIGHT[name]})
            print("wrote", name)
        br.close()


if __name__ == "__main__":
    main()

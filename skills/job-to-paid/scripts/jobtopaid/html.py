"""Render a view as a single self-contained HTML file (works in any language,
prints cleanly, and is what the Chrome engine turns into a PDF)."""

from __future__ import annotations

import base64
import html
import mimetypes
from pathlib import Path


def _e(text) -> str:
    return html.escape(str(text or ""), quote=True).replace("\n", "<br>")


def _logo_tag(path: str) -> str:
    if not path:
        return ""
    try:
        data = Path(path).expanduser().read_bytes()
    except OSError:
        return ""
    mime = mimetypes.guess_type(path)[0] or "image/png"
    if path.lower().endswith(".svg"):
        mime = "image/svg+xml"
    return f'<img class="logo" alt="" src="data:{mime};base64,{base64.b64encode(data).decode()}">'


def _rows(rows) -> str:
    out = []
    for row in rows:
        cls = row.get("style", "item")
        out.append(
            f'<tr class="{cls}"><td class="desc">{_e(row["desc"])}</td><td class="num">{_e(row["qty"])}</td>'
            f'<td class="num">{_e(row["unit_price"])}</td><td class="num">{_e(row["amount"])}</td></tr>'
        )
    return "\n".join(out)


def _totals(rows, balance=None) -> str:
    if not rows and not balance:
        return ""
    body = []
    for row in rows:
        cls = "strong" if row.get("strong") else ("muted" if row.get("muted") else "")
        body.append(f'<tr class="{cls}"><td>{_e(row["label"])}</td><td class="num">{_e(row["value"])}</td></tr>')
    if balance:
        body.append(
            f'<tr class="balance"><td>{_e(balance["label"])}</td><td class="num">{_e(balance["value"])}</td></tr>'
        )
    return f'<table class="totals">{"".join(body)}</table>'


def render_html(view: dict) -> str:
    v = view
    labels = v["labels"]
    color = v["business"].get("color") or "#0F766E"
    biz = v["business"]
    paper = "A4" if v.get("paper") == "a4" else "letter"
    parts = []
    parts.append(
        f'<div class="bar"></div><header><div class="from">{_logo_tag(biz.get("logo"))}'
        f'<div class="biz">{_e(biz["name"])}</div>'
    )
    for line in (
        ([biz["legal_name"]] if biz.get("legal_name") else []) + biz["lines"] + biz["contact"] + biz["registration"]
    ):
        parts.append(f'<div class="small">{_e(line)}</div>')
    parts.append('</div><div class="doc">')
    parts.append(f'<div class="title">{_e(v["title"])}</div>')
    number = f"{labels['number']} {v['number']}" + (f" ({v['revision']})" if v.get("revision") else "")
    parts.append(f'<div class="number">{_e(number)}</div><table class="meta">')
    for label, value in v["meta"]:
        parts.append(f"<tr><td>{_e(label)}</td><td>{_e(value)}</td></tr>")
    parts.append("</table></div></header>")
    parts.append('<section class="parties">')
    for block in (v.get("customer"), v.get("site")):
        if block:
            parts.append(f'<div><div class="label">{_e(block["heading"])}</div>')
            for i, line in enumerate(block["lines"]):
                parts.append(f'<div class="{"strong" if i == 0 else ""}">{_e(line)}</div>')
            parts.append("</div>")
    parts.append("</section>")
    if v.get("subject"):
        parts.append(f"<h1>{_e(v['subject'])}</h1>")
    if v.get("intro"):
        parts.append(f'<p class="intro">{_e(v["intro"])}</p>')
    summary = v.get("summary")
    if summary:
        parts.append(f'<div class="label">{_e(summary["heading"])}</div><div class="options">')
        for row in summary["rows"]:
            badge = f' <span class="badge">{_e(row["badge"])}</span>' if row.get("badge") else ""
            parts.append(
                f'<div class="option{" rec" if row.get("badge") else ""}"><span>{_e(row["name"])}{badge}'
                f'</span><span class="num strong">{_e(row["total"])}</span></div>'
            )
        parts.append("</div>")
    head = (
        f'<thead><tr><th>{_e(labels["description"])}</th><th class="num">{_e(labels["qty"])}</th>'
        f'<th class="num">{_e(labels["unit_price"])}</th><th class="num">{_e(labels["amount"])}</th></tr></thead>'
    )
    for section in v["sections"]:
        if section.get("heading"):
            badge = f' <span class="badge">{_e(section["badge"])}</span>' if section.get("badge") else ""
            parts.append(f"<h2>{_e(section['heading'])}{badge}</h2>")
            if section.get("subheading"):
                parts.append(f'<p class="muted">{_e(section["subheading"])}</p>')
        parts.append(f'<table class="lines">{head}<tbody>{_rows(section["rows"])}</tbody></table>')
        if section.get("totals"):
            parts.append(_totals(section["totals"]))
    parts.append(_totals(v.get("totals") or [], v.get("balance")))
    if v.get("deposit"):
        parts.append(f'<p class="strong">{_e(v["deposit"])}</p>')
    payment = v.get("payment")
    if payment and (payment["lines"] or payment.get("link")):
        parts.append(f'<div class="box"><div class="label">{_e(payment["heading"])}</div>')
        for line in payment["lines"]:
            parts.append(f"<div>{_e(line)}</div>")
        if payment.get("link"):
            parts.append(f'<a class="button" href="{_e(payment["link"])}">{_e(payment.get("link_label"))}</a>')
        parts.append("</div>")
    acc = v.get("acceptance")
    if acc:
        parts.append(
            f'<div class="box plain"><div class="label">{_e(acc["heading"])}</div><div>{_e(acc.get("text"))}</div>'
        )
        if acc.get("signature"):
            parts.append(
                f'<div class="sign"><span>{_e(labels["signature"])}</span><span>{_e(labels["name"])}</span>'
                f"<span>{_e(labels['date'])}</span></div>"
            )
        parts.append("</div>")
    for key in ("notes", "terms"):
        if v.get(key):
            parts.append(f'<div class="fine"><div class="label muted">{_e(labels[key])}</div>{_e(v[key])}</div>')
    if v.get("tax_note"):
        parts.append(f'<div class="fine">{_e(v["tax_note"])}</div>')
    if v.get("thank_you"):
        parts.append(f'<p class="thanks">{_e(v["thank_you"])}</p>')
    footer = " - ".join([biz["name"]] + biz["registration"])
    parts.append(f"<footer>{_e(footer)}</footer>")
    stamp_kind = ""
    if v.get("stamp"):
        lower = v["stamp"].lower()
        stamp_kind = (
            "paid"
            if lower == labels["paid_stamp"].lower()
            else "void"
            if lower == labels["void_stamp"].lower()
            else "draft"
        )
    stamp = f'<div class="stamp {stamp_kind}">{_e(v["stamp"])}</div>' if v.get("stamp") else ""
    title = f"{v['title']} {v['number']}"
    return f"""<!doctype html>
<html lang="{_e(v.get("language", "en"))}"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)}</title>
<style>
@page {{ size: {paper}; margin: 14mm 14mm 16mm; }}
:root {{ --brand: {color}; --ink: #1c1f24; --muted: #6b7079; --rule: #dcdee2; --soft: #f6f7f8; }}
* {{ box-sizing: border-box; }}
body {{ font-family: "Helvetica Neue", Helvetica, Arial, "Noto Sans", sans-serif; color: var(--ink);
       font-size: 13px; line-height: 1.45; margin: 0 auto; max-width: 820px; padding: 0 16px 24px; background: #fff; }}
.bar {{ height: 7px; background: var(--brand); margin: 0 -16px 22px; }}
header {{ display: flex; justify-content: space-between; gap: 24px; border-bottom: 1px solid var(--rule);
          padding-bottom: 16px; margin-bottom: 18px; flex-wrap: wrap; }}
.logo {{ max-width: 180px; max-height: 64px; display: block; margin-bottom: 8px; }}
.biz {{ font-size: 20px; font-weight: 700; }}
.small {{ color: var(--muted); font-size: 12px; }}
.doc {{ text-align: right; margin-left: auto; }}
.title {{ font-size: 30px; font-weight: 800; color: var(--brand); text-transform: uppercase; letter-spacing: .5px; }}
.number {{ font-weight: 700; }}
.meta {{ margin-left: auto; border-collapse: collapse; }}
.meta td {{ padding: 1px 0 1px 14px; }} .meta td:first-child {{ color: var(--muted); }}
.meta td:last-child {{ font-weight: 600; }}
.parties {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 20px; margin-bottom: 18px; }}
.label {{ font-size: 10.5px; font-weight: 700; text-transform: uppercase; letter-spacing: .6px; color: var(--brand);
          margin-bottom: 4px; }}
.label.muted {{ color: var(--muted); }}
.strong {{ font-weight: 700; }} .muted {{ color: var(--muted); }}
h1 {{ font-size: 18px; margin: 4px 0 6px; }} h2 {{ font-size: 15px; color: var(--brand); margin: 22px 0 4px; }}
.intro {{ color: #3d4148; }}
.options {{ margin: 6px 0 18px; }}
.option {{ display: flex; justify-content: space-between; padding: 8px 12px; background: var(--soft);
           border-radius: 6px; margin-bottom: 6px; font-weight: 700; }}
.option.rec {{ background: color-mix(in srgb, var(--brand) 12%, white); }}
.badge {{ background: var(--brand); color: #fff; font-size: 9.5px; border-radius: 10px; padding: 2px 8px;
          text-transform: uppercase; vertical-align: middle; margin-left: 6px; }}
table.lines {{ width: 100%; border-collapse: collapse; margin-top: 6px; }}
table.lines th {{ background: color-mix(in srgb, var(--brand) 10%, white); color: var(--muted); font-size: 10.5px;
                  text-transform: uppercase; text-align: left; padding: 6px 8px; }}
table.lines td {{ border-bottom: 1px solid var(--rule); padding: 7px 8px; vertical-align: top; }}
.num {{ text-align: right !important; white-space: nowrap; }}
tr.text td {{ color: var(--muted); font-style: italic; }}
table.totals {{ margin: 10px 0 10px auto; border-collapse: collapse; min-width: 300px; }}
table.totals td {{ padding: 3px 8px; }}
table.totals tr.strong td {{ font-weight: 700; font-size: 15px; border-top: 1px solid #bfc2c8; padding-top: 6px; }}
table.totals tr.muted td {{ color: var(--muted); }}
table.totals tr.balance td {{ background: color-mix(in srgb, var(--brand) 14%, white); font-weight: 800; font-size: 16px;
                              padding: 8px 10px; }}
.box {{ background: var(--soft); border: 1px solid var(--rule); border-radius: 8px; padding: 12px 14px; margin: 16px 0; }}
.box.plain {{ background: #fff; }}
.button {{ display: inline-block; margin-top: 10px; background: var(--brand); color: #fff; text-decoration: none;
           padding: 8px 16px; border-radius: 6px; font-weight: 700; }}
.sign {{ display: grid; grid-template-columns: 2fr 1.4fr 1fr; gap: 16px; margin-top: 34px; }}
.sign span {{ border-top: 1px solid #8a8e96; font-size: 11px; color: var(--muted); padding-top: 3px; }}
.fine {{ font-size: 11px; color: var(--muted); margin-top: 12px; }}
.thanks {{ color: var(--brand); font-weight: 700; margin-top: 18px; break-before: avoid-page; }}
footer {{ border-top: 1px solid var(--rule); margin-top: 22px; padding-top: 8px; font-size: 11px; color: var(--muted); }}
.stamp {{ position: fixed; top: 40%; left: 50%; transform: translate(-50%, -50%) rotate(-28deg); font-size: 120px;
          font-weight: 900; color: rgba(90, 96, 110, .12); pointer-events: none; z-index: 0; }}
.stamp.paid {{ color: rgba(16, 140, 70, .16); }} .stamp.void {{ color: rgba(190, 40, 40, .14); }}
@media print {{ body {{ max-width: none; padding: 0; }} .bar {{ margin: 0 0 22px; }} }}
</style></head>
<body>{stamp}
{"".join(parts)}
</body></html>
"""

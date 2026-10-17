"""Builds the PM-SIMPLIFY mock (documentation/plans/projects/mockups/po-schedule-simplify-v*.html).

Synthetic data only (the seeded "Taman Contoh Residences" sandbox project). Run:
    python3 build.py <base.css> <out.html> <before_after.json> <data.json>
"""
import json
import sys

base_css, out_path, counts_path, data_path = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
css = open(base_css).read()
counts = json.load(open(counts_path))

EXTRA_CSS = """
.grid tr.sub td{background:#fbfcfe;border-bottom:1px solid var(--line-soft);font-size:12px}
.grid tr.sub td:first-child{padding-left:34px}
.grid tr.exp td{background:var(--primary-soft)}
.caret{display:inline-block;width:14px;color:var(--muted)}
.grip{color:#c0c4cc;letter-spacing:-2px;margin-right:4px}
.bar{display:inline-block;width:70px;height:6px;border-radius:3px;background:var(--grey-soft);vertical-align:middle;overflow:hidden;margin-right:6px}
.bar i{display:block;height:100%;background:var(--green)}
.out{font-weight:600}
.dp{display:inline-flex;align-items:center;gap:6px;border:1px solid var(--primary);border-radius:6px;padding:2px 8px;background:#fff;box-shadow:0 0 0 3px var(--primary-soft)}
.was{color:var(--muted);text-decoration:line-through;margin-right:6px}
.ctx{position:absolute;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 8px 24px rgba(0,0,0,.12);font-size:13px;padding:4px;min-width:210px;z-index:4}
.ctx div{padding:6px 10px;border-radius:5px}
.ctx div.on{background:var(--grey-soft)}
.ctx hr{border:0;border-top:1px solid var(--line);margin:4px 0}
.dialog{width:520px;background:#fff;border:1px solid var(--line);border-radius:12px;box-shadow:0 12px 40px rgba(0,0,0,.12);padding:20px;position:relative}
.dialog h3{margin:0 0 4px;font-size:16px}
.drop{border:1.5px dashed #cbd5e1;border-radius:10px;padding:28px;text-align:center;color:var(--muted);background:#fafafa;margin:14px 0}
.field{margin:10px 0}
.field .k{font-size:12px;color:var(--muted);margin-bottom:4px}
.field .in{border:1px solid var(--line);border-radius:6px;padding:6px 10px;color:#374151}
.foot{display:flex;justify-content:flex-end;gap:8px;margin-top:14px}
.cmp{border-collapse:collapse;width:100%;font-size:13px;background:#fff;border:1px solid var(--line);border-radius:8px;overflow:hidden}
.cmp th,.cmp td{padding:8px 10px;border-bottom:1px solid var(--line-soft);text-align:left;vertical-align:top}
.cmp th{background:#fafafa;color:var(--muted);font-weight:500}
.cmp td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.cmp td.good{color:var(--green);font-weight:600}
.map{border-collapse:collapse;width:100%;font-size:12px;background:#fff}
.map th,.map td{padding:6px 8px;border-bottom:1px solid var(--line-soft);text-align:left;vertical-align:top}
.map th{background:#fafafa;color:var(--muted);font-weight:500}
.q{background:#fff;border:1px solid var(--line);border-left:3px solid var(--callout);border-radius:8px;padding:12px 14px;margin-bottom:10px}
.q b{display:block;margin-bottom:4px}
.q .rec{margin-top:6px;color:var(--green);font-weight:500}
"""

def co(n, style=""):
    return f'<b class="pin co" style="{style}">{n}</b>'


def bar(done, total):
    pct = 0 if not total else round(100 * done / total)
    return f'<span class="bar"><i style="width:{pct}%"></i></span>'


def shell(content, crumbs, active="Projects"):
    return f"""<div class="shell">
  <div class="side">
    <div class="brand">Sorento</div>
    <div class="grp">Sales</div>
    <div class="it">Project Sales</div>
    <div class="it sub{' on' if active == "Projects" else ''}">Projects</div>
    <div class="it sub">My Tasks</div>
    <div class="it sub">AutoCount Differences</div>
  </div>
  <div class="main">
    <div class="topbar">Search menu... &nbsp; Sorento SRT</div>
    <div class="content">
      <div class="crumbs" style="margin:0 0 4px">{crumbs}</div>
      {content}
    </div>
  </div>
</div>"""


def notes(items):
    lis = "".join(
        f'<li><span class="co">{n}</span><div><b>{t}</b>{body}<span class="ref">{ref}</span></div></li>'
        for n, t, body, ref in items
    )
    return f'<div class="notes"><ol>{lis}</ol></div>'


# ---- data: data.json from extract.py (masked real case, or the synthetic sandbox project) ----
D = json.load(open(data_path))
VERSION = D.get("version", "v1")
SOURCE = D.get("source", "the synthetic sandbox project, no real rows")
P, PO, SV = D["project"], D["po"], D["schedule"]
PO["issuer"] = PO["issuer"] or "Issuer not recorded"
LINES = D["lines"]
PHASES = D["phases"]
SOS = D["sales_orders"]
LINE_CAP = 10  # rows drawn in the 1280 Lines frame; the pager says the rest

STATUS = {
    "published": ("green", "Published"), "amended": ("green", "Amended"), "adopted": ("green", "In AutoCount"),
    "awaiting_costing": ("amber", "Awaiting costing"), "ready": ("amber", "Ready"), "draft": ("amber", "Draft"),
    "blocked": ("red", "Blocked"),
}


def pill(status):
    first = (status or "").split(",")[0]
    c, label = STATUS.get(first, ("", first.replace("_", " ").capitalize() or "No sales order"))
    return f'<span class="pill {c}">{label}</span>'


def fmt(x):
    return f"{x:,}" if isinstance(x, int) else f"{x:,.2f}"


def area_name(a):
    return a.title() if a and a.isupper() else a


tot_o = sum(l["ordered"] for l in LINES)
tot_s = sum(l["scheduled"] for l in LINES)
tot_d = sum(l["delivered"] for l in LINES)
short = [l for l in LINES if l["scheduled"] < l["ordered"]]
over = [l for l in LINES if l["scheduled"] > l["ordered"]]
unpublished = [s for s in SOS if s["status"] not in ("published", "amended", "adopted")]
published = len(SOS) - len(unpublished)
cta = f"Publish {len(unpublished)} sales orders" if unpublished else ("Build sales orders" if not SOS else "")
sched_name = f"{SV['label'] or 'Schedule'} (v{SV['version']})"
dates = [p["date"] for p in PHASES if p["date"] != "No date"]
next_delivery = next((l["next"] for l in LINES if l["next"]), dates[0] if dates else "")
crumb = f"Project Sales &rsaquo; Projects &rsaquo; {P['name']}"
so_range = f"{SOS[0]['ref']} to {SOS[-1]['ref']}" if len(SOS) > 1 else (SOS[0]["ref"] if SOS else "none yet")
gap_pill = (f'<span class="pill amber">{short[0]["code"]}: {fmt(short[0]["ordered"] - short[0]["scheduled"])} not scheduled</span>'
            + (f' <span class="muted small">+{len(short) - 1} more</span>' if len(short) > 1 else "")) if short else ""

# ---------------- Frame 1: project page, one Orders tab ----------------
other_pos = PO["po_count"] - 1
f1_content = f"""
<div class="phrow">
  <div class="ph"><h1>{P['name']} &nbsp;<span class="pill green">PO received</span></h1>
  <div class="meta">{P['code']}{' &middot; Developer ' + P['developer'] if P['developer'] else ''}</div></div>
  <div class="actions"><span class="btn">&lsaquo;</span><span class="btn">&rsaquo;</span><span class="btn primary">Upload PO</span></div>
</div>
<div class="tabs" style="position:relative">
  <span>Overview</span><span>Stakeholders</span><span>Tasks</span><span>Quotations</span><span>Sample requests</span><span>Samples</span><span>Sponsorships</span>
  <span class="on">Orders</span><span>Activity</span><span>Documents</span>
  {co(1, "left:872px;top:6px")}
</div>
<div class="toolbar" style="justify-content:space-between">
  <div class="actions"><span class="chip">Search POs</span><span class="chip">Status</span></div>
  <div class="actions" style="position:relative"><span class="btn sm">Columns</span>{co(3, "right:-26px;top:0")}</div>
</div>
<div class="grid" style="position:relative">
<div class="scroller"><table>
<colgroup><col style="width:180px"><col style="width:170px"><col style="width:110px"><col style="width:170px"><col style="width:110px"><col style="width:190px"></colgroup>
<thead><tr><th>Purchase order</th><th>Issued by</th><th class="num">Value (RM)</th><th>Delivered</th><th>Next delivery</th><th>Next step</th></tr></thead>
<tbody>
<tr class="exp"><td><span class="caret">&#9662;</span><b>{PO['number']}</b><span class="second">Dated {PO['dated']} &middot; {len(LINES)} lines</span></td><td>{PO['issuer']}</td><td class="num">{PO['amount']:,.2f}</td>
<td>{bar(tot_d, tot_o)}{fmt(tot_d)} of {fmt(tot_o)}</td><td>{next_delivery}</td><td><span class="pill amber">{cta or 'Nothing open'}</span></td></tr>
<tr class="sub"><td colspan="2">Delivery schedule {sched_name} &middot; dated {SV['dated']}</td><td class="num"></td><td>{len(PHASES)} deliveries{', ' + dates[0] + ' to ' + dates[-1] if dates else ''}</td><td></td><td>{gap_pill}</td></tr>
<tr class="sub"><td colspan="2">Sales orders {so_range}</td><td class="num"></td><td>{published} published, {len(unpublished)} not yet</td><td></td><td></td></tr>
{'' if other_pos < 1 else f'<tr><td><span class="caret">&#9656;</span><span class="muted">{other_pos} more purchase order(s)</span></td><td colspan="5"></td></tr>'}
</tbody></table></div>
<div class="pager"><span>1 - {PO['po_count']} of {PO['po_count']}</span><span>Rows per page 10</span></div>
{co(2, "left:2px;top:30px")}
{co(4, "right:8px;top:44px")}
</div>
<div class="ctx" style="left:330px;top:452px">
  <div class="on">Open purchase order</div><div>Upload delivery schedule</div><div>Upload a new PO version</div><hr><div>Copy PO number</div>
</div>
{co(5, "left:550px;top:448px")}
<div style="height:120px"></div>
"""
f1 = shell(f1_content, crumb)
f1_notes = notes([
    (1, "One Orders tab replaces three.",
     " POs, Delivery schedules and Sales orders are one job on one PO, so they become one tab (12 tabs to 10). The grid lists POs; everything about a PO hangs under its row.",
     "Today: ProjectDetailClient.tsx:77-90 (12 tabs). Reuses PanelDataGrid (components/common/PanelDataGrid.tsx), the same grid PurchaseOrdersPanel.tsx already renders."),
    (2, "Rows expand, they do not navigate away.",
     " The caret shows the PO's schedule and its sales orders inline: version, date range, what is still open. Click the PO number to open the PO page.",
     "TanStack getExpandedRowModel, already wired in PanelDataGrid (expanded prop) and SpoPlannerTable.tsx."),
    (3, "Columns toggle.",
     " Same Columns control every listing has; preferences keyed per listing.",
     "components/ui/data-grid-column-visibility.tsx, listing key projects.purchase_orders.view::orders."),
    (4, "One next step per PO, computed, not a separate card.",
     " Replaces the POToSalesOrderStep card whose links landed on a list and made the user pick the PO again (defect D4).",
     "POToSalesOrderStep.tsx:76-81 links ?po= and ?build= that nothing reads (ProjectDetailClient.tsx:98 reads only tab)."),
    (5, "Right-click on a row.",
     " The row's actions without a column of buttons: open, upload its schedule (PO already known, no picker), upload a new version.",
     "components/ui/context-menu.tsx, as QuotationLinesGrid.tsx uses it."),
])

# ---------------- Frame 2: PO page, Lines tab ----------------
exp_no = D["expanded_no"]
drawn = [l for l in LINES][:LINE_CAP]
if exp_no and all(l["no"] != exp_no for l in drawn):
    drawn = drawn[:LINE_CAP - 1] + [l for l in LINES if l["no"] == exp_no]
rows = []
for l in drawn:
    outst = max(l["ordered"] - l["delivered"], 0)
    cls, flag = "", ""
    if l["no"] == exp_no:
        cls = "exp"
    if l["scheduled"] < l["ordered"]:
        cls = "hlamber"
        flag = f'<span class="second" style="color:var(--amber)">{fmt(l["ordered"] - l["scheduled"])} not scheduled</span>'
    elif l["scheduled"] > l["ordered"]:
        cls = "hlamber"
        flag = f'<span class="second" style="color:var(--amber)">{fmt(l["scheduled"] - l["ordered"])} more than ordered</span>'
    caret = "&#9662;" if l["no"] == exp_no else "&#9656;"
    rows.append(
        f'<tr class="{cls}"><td><span class="grip">&#8942;&#8942;</span><span class="caret">{caret}</span>{l["no"]}</td>'
        f'<td title="{l["desc"]}"><b>{l["code"]}</b><span class="second">{l["desc"]}</span></td>'
        f'<td class="num">{fmt(l["ordered"])}</td><td class="num">{fmt(l["scheduled"])}{flag}</td><td class="num">{bar(l["delivered"], l["ordered"])}{fmt(l["delivered"])}</td>'
        f'<td class="num out">{fmt(outst)}</td><td>{l["next"]}</td></tr>'
    )
    if l["no"] == exp_no:
        for dl in D["expanded_deliveries"]:
            where = area_name(dl["area"]) + (", " + dl["levels"] if dl["levels"] else "")
            rows.append(
                f'<tr class="sub"><td colspan="2">{dl["date"]} &middot; {where}</td><td class="num"></td><td class="num">{fmt(dl["units"])}</td>'
                f'<td class="num">{fmt(dl["delivered"])}</td><td class="num">{fmt(max(dl["units"] - dl["delivered"], 0))}</td><td>{dl["so"]} {pill(dl["status"])}</td></tr>'
            )
exp_line = next((l for l in LINES if l["no"] == exp_no), None)
part = next((dl for dl in D["expanded_deliveries"] if 0 < dl["delivered"] < dl["units"]), None)
part_txt = (f" A partial delivery reads as {fmt(part['delivered'])} of {fmt(part['units'])} on its own delivery row." if part
            else " Nothing on this PO is delivered yet, so Delivered reads 0; it fills from AutoCount as delivery orders post.")

po_header = f"""
<div class="phrow">
  <div class="ph" style="position:relative"><h1>{PO['number']} &nbsp;<span class="pill green">{'Confirmed' if PO['confirmed'] else 'To confirm'}</span></h1>
  <div class="meta">{P['name']} &middot; {PO['issuer']} &middot; Dated {PO['dated']} &middot; RM {PO['amount']:,.2f} &middot; Schedule {sched_name}</div>
  {co(6, "left:-28px;top:0")}</div>
  <div class="actions" style="position:relative"><span class="btn">&lsaquo;</span><span class="btn">&rsaquo;</span><span class="btn">&#8943;</span>{f'<span class="btn primary">{cta}</span>' if cta else ''}{co(7, "right:-10px;top:-14px")}</div>
</div>"""


def po_tabs(on):
    names = [f"Lines ({len(LINES)})", f"Deliveries ({len(PHASES)})", f"Sales orders ({len(SOS)})", f"Documents ({1 + SV['versions']})"]
    spans = "".join(f'<span class="{"on" if n.startswith(on) else ""}">{n}</span>' for n in names)
    return f'<div class="tabs" style="position:relative">{spans}{co(8, "left:440px;top:6px")}</div>'


f2_content = po_header + po_tabs("Lines") + f"""
<div class="toolbar" style="justify-content:space-between">
  <div class="actions"><span class="chip">Search lines</span><span class="chip on">Still to deliver</span><span class="chip">Schedule differs ({len(short) + len(over)})</span></div>
  <div class="actions"><span class="btn sm">Columns</span></div>
</div>
<div class="grid" style="position:relative">
<div class="scroller"><table>
<colgroup><col style="width:70px"><col style="width:230px"><col style="width:80px"><col style="width:110px"><col style="width:130px"><col style="width:100px"><col style="width:200px"></colgroup>
<thead><tr><th>#</th><th>Product</th><th class="num">Ordered</th><th class="num">Scheduled</th><th class="num">Delivered</th><th class="num">Outstanding</th><th>Next delivery</th></tr></thead>
<tbody>{''.join(rows)}</tbody>
<tfoot><tr><td></td><td>{len(LINES)} lines</td><td class="num">{fmt(tot_o)}</td><td class="num">{fmt(tot_s)}</td><td class="num">{fmt(tot_d)}</td><td class="num">{fmt(max(tot_o - tot_d, 0))}</td><td></td></tr></tfoot>
</table></div>
<div class="pager"><span>1 - {len(drawn)} of {len(LINES)}</span><span>Rows per page 25</span></div>
{co(9, "left:560px;top:6px")}
{co(10, "left:2px;top:72px")}
</div>
"""
f2 = shell(f2_content, crumb + " &rsaquo; Orders")
f2_notes = notes([
    (6, "One page per PO.",
     " The PO record page, the PO document review page and the schedule review page become one page, pos/[poId]. Read-only facts sit under the header, as the design language asks.",
     "Today 3 pages: pos/[poId] (PurchaseOrderDetailClient.tsx), purchase-orders/[versionId] (POIntakeConfirmClient.tsx), delivery-schedules/[versionId] (DeliveryScheduleReviewClient.tsx). PageHeader + RecordNavigation (components/common/)."),
    (7, "One CTA: whatever is next.",
     " Confirm PO, Confirm schedule, Build sales orders or Publish N sales orders, one at a time. Approve and Countersign leave the screen (question 1).",
     "POIntakeConfirmClient.tsx:215-233 shows Confirm, Approve, Countersign; none of the last two is read by build or publish (defect D2)."),
    (8, "Four line tabs, same order in view and edit.",
     " Lines, Deliveries, Sales orders, Documents. The file and its PDF live only on Documents.",
     "components/ui/tabs.tsx (line variant). PdfViewer (components/common/PdfViewer.tsx) moves into Documents."),
    (9, "Ordered, Scheduled, Delivered, Outstanding on every line.",
     " The question the owner's staff could not answer anywhere on the project: how much of each item is still to go. Delivered comes from AutoCount's delivered quantity on the linked sales order lines, read-only (answered: AutoCount is the source of truth).",
     "No migration: projects.sales_order_lines.source_po_line_id + core_sales_order_line_id -> public.sales_order_lines.qty_delivered, via delivered_qty_of (project_supply_service.py:532)."),
    (10, "Drag to sort, expand to see the deliveries. A gap is a row colour plus a filter.",
     f" Expanding line {exp_no} lists its deliveries: date, area, units, delivered, sales order.{part_txt}"
     + (f" {len(short)} line(s) are short on the schedule and {len(over)} carry more than ordered: amber row and the 'Schedule differs' chip, not a separate findings list." if short or over else ""),
     "DataGridTableDndRows (components/ui/data-grid-table-dnd-rows.tsx) as SalesOrderLinesTable.tsx and QuotationLinesGrid.tsx use it. The amber row reuses the schedule_short finding the build already raises."),
])

# 375 frame of Lines
cards = []
for l in LINES[:4]:
    amber = ' style="border-color:var(--amber-line);background:var(--amber-soft)"' if l["scheduled"] < l["ordered"] else ""
    cards.append(f"""<div class="rcard"{amber}><div class="row1"><b>{l['code']}</b><span class="muted small">#{l['no']}</span></div>
<div class="small muted" style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis">{l['desc']}</div>
<div class="kv"><div><div class="k">Ordered</div>{fmt(l['ordered'])}</div><div><div class="k">Delivered</div>{fmt(l['delivered'])}</div><div><div class="k">Outstanding</div><b>{fmt(max(l['ordered'] - l['delivered'], 0))}</b></div></div></div>""")
f2_phone = f"""<div class="phone"><div class="top"><span>&#9776;</span><span>{PO['number']}</span><span>&#8943;</span></div>
<div class="content"><div class="meta" style="margin:0 0 8px"><span class="pill green">Confirmed</span> RM {PO['amount']:,.2f}</div>
{f'<span class="btn primary" style="width:100%;justify-content:center;margin-bottom:10px">{cta}</span>' if cta else ''}
<div class="tabs"><span class="on">Lines ({len(LINES)})</span><span>Deliveries ({len(PHASES)})</span><span>Sales orders</span></div>
{''.join(cards)}<div class="muted small" style="text-align:center">{len(LINES) - 4} more lines</div></div></div>"""

# ---------------- Frame 3: Deliveries tab, date edited in place ----------------
# The edited row: the latest-dated area whose sales orders are not published, so "draft lines follow" is true.
editable = [i for i, p in enumerate(PHASES) if p["date"] != "No date" and not any(
    s in (p["status"] or "") for s in ("published", "amended", "adopted"))]
edit_i = editable[-1] if editable else None
new_date = "two weeks later"
if edit_i is not None:
    from datetime import datetime, timedelta
    new_date = (datetime.strptime(PHASES[edit_i]["date"], "%d %b %Y") + timedelta(days=14)).strftime("%-d %b %Y")
prow = []
for i, p in enumerate(PHASES[:16]):
    dcell, cls = p["date"], ""
    if i == edit_i:
        dcell, cls = f'<span class="dp" style="position:relative"><span class="was">{p["date"]}</span>{new_date} &#128197;{co(12, "right:-30px;top:0")}</span>', "exp"
    prow.append(f'<tr class="{cls}"><td><span class="grip">&#8942;&#8942;</span><span class="caret">&#9656;</span>{area_name(p["area"])}</td>'
                f'<td title="{p["levels"]}">{p["levels"]}</td><td>{dcell}</td>'
                f'<td class="num">{fmt(p["units"])}</td><td>{fmt(p["delivered"])} of {fmt(p["units"])}</td><td>{p["so"] or "-"} {pill(p["status"]) if p["so"] else ""}</td></tr>')
edit_row_top = 36 + 34 * (edit_i if edit_i is not None else 0) + 4
edit_msg = ""
if edit_i is not None:
    ep = PHASES[edit_i]
    edit_msg = (f"<b>{area_name(ep['area'])}{', ' + ep['levels'] if ep['levels'] else ''} moves to {new_date}.</b> "
                f"<span class=\"muted\">{ep['so']} is not published, so its lines take the new date. Nothing published changes.</span>")
f3_content = po_header.replace(co(6, "left:-28px;top:0"), "").replace(co(7, "right:-10px;top:-14px"), "") + po_tabs("Deliveries").replace(co(8, "left:440px;top:6px"), "") + f"""
<div class="toolbar" style="justify-content:space-between">
  <div class="actions"><span class="toggle"><span class="on">By delivery</span><span>By product</span></span><span class="chip">Search</span></div>
  <div class="actions" style="position:relative"><span class="btn sm">Columns</span><span class="btn sm">Upload revised schedule</span>{co(14, "right:-26px;top:0")}</div>
</div>
<div class="grid" style="position:relative">
<div class="scroller"><table>
<colgroup><col style="width:150px"><col style="width:170px"><col style="width:220px"><col style="width:80px"><col style="width:120px"><col style="width:190px"></colgroup>
<thead><tr><th>Area</th><th>Levels</th><th>Delivery date</th><th class="num">Units</th><th>Delivered</th><th>Sales order</th></tr></thead>
<tbody>{''.join(prow)}</tbody></table></div>
<div class="pager"><span>1 - {min(len(PHASES), 16)} of {len(PHASES)}</span><span>Schedule {sched_name}, confirmed {SV['confirmed']}</span></div>
</div>
<div class="alert" style="margin-top:10px;border:1px solid var(--line);border-radius:8px;padding:10px 12px;background:#fff;position:relative">
  {edit_msg}
  <span style="float:right"><span class="btn sm">Undo</span></span>
  {co(13, "left:-28px;top:8px")}
</div>
"""
f3 = shell(f3_content, crumb + " &rsaquo; Orders")
f3_notes = notes([
    (12, "Change a date where it is shown.",
     f" Click the date, pick the new one, done. It writes that area's date and the draft sales order lines for that area follow. A published sales order gets the existing amendment flow, started for you (question 2). On this project today, {D.get('so_lines_old_date', 0)} of {D.get('so_lines', 0)} sales order lines still carry the dates of the schedule they were built from, not the confirmed {sched_name}: nothing carried the new dates over.",
     "Today: dates are read-only (DeliveryScheduleMatrix.tsx:233-239); three different routes write three different stores (delivery_phases.delivery_date, delivery_schedule_cells.delivery_date_override, sales_order_lines.delivery_date). DatePicker: components/ui/date-picker.tsx."),
    (13, "The result says what moved, with Undo.",
     " No confirm dialog; the change is reversible, so an Undo toast instead of a question.",
     "Toast + undo as the deferred-action standard (PRINCIPLES.md design mandates)."),
    (14, "A revised schedule is one button and one file.",
     " The PO is known from the page, the schedule is the PO's schedule, so 'Revision of', the PO picker and the project picker go. Issued by defaults to the PO's issuer; the revision label is read from the document. The uploaded version opens in 'By product' (today's matrix) with its flags until confirmed.",
     "DeliveryScheduleUploadDialog.tsx:160-262 asks 4 to 6 fields today. The matrix (DeliveryScheduleMatrix.tsx) is kept as the By product view."),
])

# ---------------- Frame 4: upload dialogs, before -> after ----------------
f4 = f"""<div class="layout" style="gap:32px">
<div class="dialog"><h3>Upload a delivery schedule <span class="muted small">(today)</span></h3>
<div class="field"><div class="k">Purchase order *</div><div class="in">{PO['number']}</div></div>
<div class="field"><div class="k">Revision of</div><div class="in">A schedule we have not seen before</div></div>
<div class="field"><div class="k">Issued by</div><div class="in">The company whose letterhead it carries</div></div>
<div class="field"><div class="k">Revision label</div><div class="in">&nbsp;</div></div>
<div class="drop">Drop the schedule PDF or Excel here</div>
<div class="foot"><span class="btn">Cancel</span><span class="btn primary">Upload</span></div></div>
<div class="dialog" style="position:relative"><h3>Upload revised schedule</h3><div class="muted small">For {PO['number']}</div>
<div class="drop">Drop the schedule PDF or Excel here</div>
<div class="foot"><span class="btn">Cancel</span><span class="btn primary">Upload</span></div>
{co(15, "right:-10px;top:-10px")}</div>
</div>"""
f4_notes = notes([
    (15, "Same dialog component, fewer fields.",
     " Opened from the PO page or the PO row, it already knows the PO and the schedule. Opened from Pipeline's Start menu it still asks for the project and PO, nothing else.",
     "DeliveryScheduleUploadDialog.tsx and POIntakeUploadDialog.tsx kept; their context props already exist (the PO is pre-filled today, the other fields are not)."),
])

# ---------------- before/after table ----------------
ba_rows = "".join(
    f"<tr><td>{r['job']}</td><td>{r['before']}</td><td class='good'>{r['after']}</td><td>{r['why']}</td></tr>"
    for r in counts["rows"]
)

# ---------------- element -> component map ----------------
MAP = [
    ("Orders tab grid", "components/common/PanelDataGrid.tsx", "PurchaseOrdersPanel.tsx, DeliverySchedulesPanel.tsx, SalesOrdersPanel.tsx fold into one panel"),
    ("Row expand", "TanStack getExpandedRowModel via PanelDataGrid `expanded`", "SpoPlannerTable.tsx precedent"),
    ("Drag sort rows", "components/ui/data-grid-table-dnd-rows.tsx", "SalesOrderLinesTable.tsx, QuotationLinesGrid.tsx"),
    ("Right-click menu", "components/ui/context-menu.tsx", "QuotationLinesGrid.tsx"),
    ("Columns toggle", "components/ui/data-grid-column-visibility.tsx", "QuotationLinesGrid.tsx"),
    ("Page header, prev/next", "components/common/PageHeader.tsx, components/common/RecordNavigation.tsx", "every detail page"),
    ("Status pills", "components/ui/badge.tsx", ""),
    ("Line tabs", "components/ui/tabs.tsx", ""),
    ("Delivered bar", "components/ui/progress.tsx", ""),
    ("Inline date", "components/ui/date-picker.tsx", ""),
    ("By product view", "delivery-schedules/components/DeliveryScheduleMatrix.tsx", "kept as is"),
    ("PO lines while unconfirmed", "components/POIntakeLinesGrid.tsx", "kept, shown on the Lines tab until Confirm"),
    ("Document viewer", "components/common/PdfViewer.tsx", "moves to the Documents tab"),
    ("Upload dialogs", "components/POIntakeUploadDialog.tsx, components/DeliveryScheduleUploadDialog.tsx", "fields hidden when context is known"),
    ("Build", "components/SalesOrderBuildDialog.tsx", "one dialog, not two; PO and schedule known from the page"),
]
map_rows = "".join(f"<tr><td>{a}</td><td class='mono'>{b}</td><td class='muted'>{c}</td></tr>" for a, b, c in MAP)

QUESTIONS = [
    ("1. Approve and Countersign: drop them, or make them mean something?",
     "Today a PO needs Confirm, then Approve, then Countersign by a second person, but the build and publish never check the last two. (a) Drop them: Confirm is the only sign-off. (b) Keep them and make Build sales orders wait for Countersign.",
     "Recommendation: (a). They stop nothing today, so nobody relies on them; if a second pair of eyes is wanted, (b) is a small follow-up."),
    ("2. Changing a delivery date: edit in place, or re-upload only?",
     "(a) Click the date on the Deliveries tab and change it; draft sales orders follow, a published one gets an amendment started for you. (b) Dates change only by uploading a revised schedule.",
     "Recommendation: (a). A date usually moves by phone or WhatsApp; making staff wait for a revised document to move one date is the heavy part today. The next uploaded revision still wins."),
    ("3. One page per PO?",
     "Merge the PO record, the PO document review and the schedule review into one PO page with Lines, Deliveries, Sales orders and Documents tabs. Sales order detail pages stay as they are.",
     "Recommendation: yes. One job, one place; today one PO visits 3 project tabs and 4 pages."),
    ("4. Deliveries: read from AutoCount only?",
     "(a) Delivered quantities come only from AutoCount (as today, shown on the PO). (b) Staff also record deliveries in the CRM.",
     "Answered (a): AutoCount is the source of truth."),
    ("5. Build sales orders: keep the 'split by' choice?",
     "(a) Keep the choice (area, delivery date, delivery month) in one dialog. (b) Always split by delivery date, no dialog: one sales order per delivery.",
     "Answered (a): keep the choice, in one dialog instead of two."),
]
qs = "".join(f'<div class="q"><b>{t}</b>{b}<div class="rec">{r}</div></div>' for t, b, r in QUESTIONS)

html = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PO and schedule, simplified</title>
<!-- PM-SIMPLIFY mock {VERSION}, 17 Oct 2026. Annotated mockup, not shipped code. Plain inline CSS so it opens from a file.
     Plan: documentation/plans/projects/PLAN-pm-simplify-17oct.md. Data: {SOURCE} -->
<style>
{css}
{EXTRA_CSS}
</style>
</head>
<body>
<div class="doc">
<h1>PO and delivery schedule, simplified (mock {VERSION})</h1>
<p class="sub">Owner, 15 Oct: "the PO part and the delivery schedule part are badly designed, too complicated."
Proposal: one Orders tab on the project, one page per PO, and every line showing ordered, scheduled,
delivered and still to deliver. Dates change where they are shown. One sign-off instead of three.
Existing components only (map at the bottom). Data: {SOURCE}: {len(LINES)} PO lines,
{len(PHASES)} deliveries, {len(SOS)} sales orders, {published} published.</p>

<h2 class="frame-title">Before and after, measured</h2>
<table class="cmp" style="max-width:1100px;table-layout:fixed"><colgroup><col style="width:22%"><col style="width:32%"><col style="width:20%"><col style="width:26%"></colgroup><thead><tr><th>Job</th><th>Today</th><th>Proposed</th><th>What removes the steps</th></tr></thead><tbody>{ba_rows}</tbody></table>
<p class="muted small">{counts['note']}</p>

<h2 class="frame-title">1. Project page: one Orders tab, 1280 wide</h2>
<div class="layout"><div class="scroll"><div class="frame w1280">{f1}</div></div>{f1_notes}</div>

<h2 class="frame-title">2. PO page, Lines tab: what is still to deliver, 1280 and 375 wide</h2>
<div class="layout"><div class="scroll"><div class="frame w1280">{f2}</div></div>{f2_notes}</div>
<div class="layout" style="margin-top:16px"><div class="frame w375">{f2_phone}</div></div>

<h2 class="frame-title">3. PO page, Deliveries tab: change a date in place, 1280 wide</h2>
<div class="layout"><div class="scroll"><div class="frame w1280">{f3}</div></div>{f3_notes}</div>

<h2 class="frame-title">4. Upload a revised schedule: today vs proposed</h2>
<div class="layout">{f4}{f4_notes}</div>

<h2 class="frame-title">Fixed on the way (defects found in discovery, independent of the redesign)</h2>
<ul class="small" style="max-width:1100px">
<li><b>D1</b> Two places edit a scanned PO's lines and only one reaches the sales orders; confirming a new version wipes the other. Proposed: the PO's lines are edited in one place.</li>
<li><b>D2</b> Approve and Countersign gate nothing (question 1).</li>
<li><b>D3</b> Sales orders can be built from a schedule nobody confirmed. Proposed: Build waits for a confirmed schedule.</li>
<li><b>D4</b> The next-step card's links land on a list and the PO is picked again.</li>
<li><b>D5</b> The PO is re-picked in the schedule upload, the build dialog and the amendment picker.</li>
</ul>

<h2 class="frame-title">Questions for the owner (Q1 to Q3 open; Q4 and Q5 answered)</h2>
<div style="max-width:1100px">{qs}</div>

<h2 class="frame-title">Element to component map (all existing)</h2>
<table class="map" style="max-width:1100px"><thead><tr><th>Element</th><th>Component</th><th>Used today in</th></tr></thead><tbody>{map_rows}</tbody></table>
</div>
</body>
</html>
"""
open(out_path, "w").write(html)
print("wrote", out_path, len(html))

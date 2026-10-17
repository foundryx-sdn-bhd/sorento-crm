"""Pull one project's PO / schedule / SO shape into data.json for the mock generator.

    python3 extract.py <psql dsn> <project_id> <out.json> [po_id]

Read-only. Picks the project's PO that has a delivery schedule (or the one given).
"""
import json
import subprocess
import sys
from datetime import date

dsn, project_id, out = sys.argv[1], sys.argv[2], sys.argv[3]
po_arg = sys.argv[4] if len(sys.argv) > 4 else None


def q(sql):
    r = subprocess.run(["psql", dsn, "-At", "-F", "\x1f", "-R", "\x1e", "-c", sql], capture_output=True, text=True, check=True)
    return [line.split("\x1f") for line in r.stdout.rstrip("\n").split("\x1e") if line]


def d(s):
    if not s:
        return None
    y, m, dd = s[:10].split("-")
    return date(int(y), int(m), int(dd)).strftime("%-d %b %Y")


def n(s):
    if s in (None, ""):
        return 0
    f = float(s)
    return int(f) if f == int(f) else round(f, 2)


proj = q(f"""select p.title, p.project_code, coalesce(dev.name,'')
from projects.projects p left join projects.parties dev on dev.id=p.developer_party_id where p.id='{project_id}'""")[0]

po_filter = f"po.id='{po_arg}'" if po_arg else (
    "exists(select 1 from projects.delivery_schedules s where s.purchase_order_id=po.id)")
po = q(f"""select po.id, po.po_number, po.po_date, coalesce(po.po_amount,0), coalesce(pt.name,''), po.status,
  (select count(*) from projects.purchase_orders x where x.project_id=po.project_id)
from projects.purchase_orders po left join projects.parties pt on pt.id=po.issuing_party_id
where po.project_id='{project_id}' and {po_filter} order by po.created_at limit 1""")[0]
po_id = po[0]

pov = q(f"""select id, version_no, confirmed_at is not null from projects.po_versions
where purchase_order_id='{po_id}' order by (confirmed_at is not null) desc, version_no desc limit 1""")
po_version_id = pov[0][0] if pov else None

sv = q(f"""select v.id, v.version_no, coalesce(v.revision_label,''), v.schedule_date, v.confirmed_at,
  coalesce(v.reconciled_columns,0), coalesce(v.total_columns,0),
  (select count(*) from projects.delivery_schedule_versions w where w.delivery_schedule_id=v.delivery_schedule_id)
from projects.delivery_schedule_versions v join projects.delivery_schedules s on s.id=v.delivery_schedule_id
where s.purchase_order_id='{po_id}' order by (v.confirmed_at is not null) desc, v.version_no desc limit 1""")[0]
sv_id = sv[0]

# Units per (product) on the schedule version, matched to PO lines by product, else by customer code.
lines = q(f"""
with pl as (select * from projects.po_lines where po_version_id='{po_version_id}' and not is_cancelled),
cells as (select c.*, coalesce(c.delivery_date_override, ph.delivery_date) dd from projects.delivery_schedule_cells c
          join projects.delivery_phases ph on ph.id=c.phase_id where c.version_id='{sv_id}'),
so as (select sl.source_po_line_id pid,
          sum(greatest(coalesce(cl.qty_delivered,0)-coalesce(cl.qty_cancelled,0),0)) delivered
       from projects.sales_order_lines sl join projects.sales_orders so on so.id=sl.project_sales_order_id
       left join public.sales_order_lines cl on cl.id=sl.core_sales_order_line_id
       where so.purchase_order_id='{po_id}' group by 1)
select pl.line_no, coalesce(pl.stock_code_raw,''), coalesce(pl.description_raw,''), coalesce(pl.qty,0),
  0,
  coalesce(so.delivered,0),
  (select min(c.dd) from cells c where c.product_id=pl.resolved_product_id and c.dd >= current_date),
  pl.id, pl.resolved_product_id
from pl left join so on so.pid=pl.id order by pl.line_no""")

line_rows = [
    {"no": int(r[0]), "code": r[1], "desc": r[2], "ordered": n(r[3]), "scheduled": n(r[4]),
     "delivered": n(r[5]), "next": d(r[6]) or "", "id": r[7], "product": r[8],
     "desc_full": r[2]}
    for r in lines
]
for lr in line_rows:
    lr["desc"] = lr["desc"].split("\n")[0].strip()

# Schedule units per product, shared out over the PO lines carrying that product in line order
# (the same product sits on several PO lines in real data; summing per line double-counts).
per_product = {r[0]: float(r[1]) for r in q(f"""select product_id, sum(qty) from projects.delivery_schedule_cells
  where version_id='{sv_id}' and product_id is not null group by 1""")}
for lr in line_rows:
    left = per_product.get(lr["product"], 0.0)
    take = min(left, float(lr["ordered"]))
    lr["scheduled"] = n(str(take))
    if lr["product"] in per_product:
        per_product[lr["product"]] = left - take
for lr in reversed(line_rows):  # whatever is left over sits on the product's last line
    if per_product.get(lr["product"], 0) > 0:
        lr["scheduled"] = n(str(float(lr["scheduled"]) + per_product[lr["product"]]))
        per_product[lr["product"]] = 0
unmatched = q(f"""select count(distinct customer_code_raw), coalesce(sum(qty),0) from projects.delivery_schedule_cells
  where version_id='{sv_id}' and product_id is null""")[0]
mismatch = q(f"""select count(*), count(*) filter (where sl.delivery_date is distinct from coalesce(ph.delivery_date, sl.delivery_date))
  from projects.sales_order_lines sl join projects.sales_orders so on so.id=sl.project_sales_order_id
  left join projects.delivery_phases ph on ph.id=sl.phase_id where so.purchase_order_id='{po_id}'""")[0]


def deliveries_of(po_line_id):
    rows = q(f"""select coalesce(ph.delivery_date, sl.delivery_date), ph.area_group, coalesce(ph.label, 'Part '||ph.sequence), sl.qty,
      coalesce(so.provisional_ref, so.autocount_doc_no, ''), so.status,
      greatest(coalesce(cl.qty_delivered,0)-coalesce(cl.qty_cancelled,0),0), sl.delivery_date
    from projects.sales_order_lines sl join projects.sales_orders so on so.id=sl.project_sales_order_id
    left join projects.delivery_phases ph on ph.id=sl.phase_id
    left join public.sales_order_lines cl on cl.id=sl.core_sales_order_line_id
    where sl.source_po_line_id='{po_line_id}' order by coalesce(ph.delivery_date, sl.delivery_date) nulls last, ph.area_group desc""")
    return [{"date": d(r[0]) or "No date", "area": r[1] or "Not scheduled", "levels": r[2], "units": n(r[3]),
             "so": r[4], "status": r[5], "delivered": n(r[6]), "so_date": d(r[7]) or ""} for r in rows]


# Expanded line: the one with a partial delivery and the most deliveries.
best = None
for lr in line_rows:
    dl = deliveries_of(lr["id"])
    score = (1 if 0 < lr["delivered"] < lr["ordered"] else 0, len(dl))
    if best is None or score > best[0]:
        best = (score, lr["no"], dl)
expanded_no, expanded_deliveries = (best[1], best[2]) if best else (None, [])

phases = q(f"""
with cells as (select c.phase_id, sum(c.qty) units from projects.delivery_schedule_cells c where c.version_id='{sv_id}' group by 1),
sol as (select sl.phase_id, string_agg(distinct coalesce(so.provisional_ref,''), ', ') refs,
          string_agg(distinct so.status, ',') sts,
          sum(greatest(coalesce(cl.qty_delivered,0)-coalesce(cl.qty_cancelled,0),0)) delivered
        from projects.sales_order_lines sl join projects.sales_orders so on so.id=sl.project_sales_order_id
        left join public.sales_order_lines cl on cl.id=sl.core_sales_order_line_id
        where so.purchase_order_id='{po_id}' group by 1)
select ph.area_group, coalesce(ph.label, case when (select count(*) from projects.delivery_phases x where x.project_id=ph.project_id and x.area_group=ph.area_group)>1 then 'Part '||ph.sequence else '' end), ph.delivery_date, cells.units, coalesce(sol.refs,''), coalesce(sol.sts,''), coalesce(sol.delivered,0)
from projects.delivery_phases ph join cells on cells.phase_id=ph.id left join sol on sol.phase_id=ph.id
order by ph.delivery_date nulls last, ph.area_group, ph.sequence""")
phase_rows = [{"area": r[0], "levels": r[1], "date": d(r[2]) or "No date", "units": n(r[3]), "so": r[4],
               "status": r[5], "delivered": n(r[6])} for r in phases]

sos = q(f"""select coalesce(provisional_ref,''), status from projects.sales_orders
where purchase_order_id='{po_id}' order by provisional_ref""")

data = {
    "project": {"name": proj[0], "code": proj[1] or "", "developer": proj[2]},
    "po": {"number": po[1], "dated": d(po[2]) or "", "amount": float(po[3]), "issuer": po[4], "status": po[5],
           "po_count": int(po[6]), "confirmed": bool(pov and pov[0][2] == "t")},
    "schedule": {"version": int(sv[1]), "label": sv[2], "dated": d(sv[3]) or "", "confirmed": d(sv[4]) or "",
                 "reconciled": int(sv[5]), "columns": int(sv[6]), "versions": int(sv[7])},
    "lines": line_rows,
    "expanded_no": expanded_no,
    "expanded_deliveries": expanded_deliveries,
    "phases": phase_rows,
    "sales_orders": [{"ref": r[0], "status": r[1]} for r in sos],
    "unmatched_columns": int(unmatched[0]), "unmatched_units": n(unmatched[1]),
    "so_lines": int(mismatch[0]), "so_lines_old_date": int(mismatch[1]),
}
for lr in data["lines"]:
    lr.pop("desc_full", None)
json.dump(data, open(out, "w"), indent=1)
print(f"{len(line_rows)} lines, {len(phase_rows)} areas, {len(sos)} SOs, expanded line {expanded_no}")

-- PL-STATUS-ROLLUP: read-only queries for the owner (prod). SELECT only, writes nothing.
-- Run inside a read-only transaction (BEGIN READ ONLY, then ROLLBACK).

-- Q1. Stale headers, as asked: every stored line says received, header does not.
--     (The Packing Lists page already shows these as Fully Received because the list
--     re-derives the header on read, but the stored column feeds coverage and the
--     container-request "not arrived" figure.)
SELECT count(*) AS stale_headers_all_lines_received
FROM inbound_shipments s
WHERE s.shipment_status NOT IN ('fully_received', 'closed', 'draft')
  AND EXISTS (SELECT 1 FROM inbound_shipment_lines l WHERE l.shipment_id = s.id)
  AND NOT EXISTS (
        SELECT 1 FROM inbound_shipment_lines l
        WHERE l.shipment_id = s.id AND coalesce(l.line_status, '') <> 'received');

-- Q2. The TCLU8176397 shape: the page shows every line Received, the stored lines do not
--     (a retired, never-received SPO allocation inflates the stored allocated figure).
--     Per product: visible allocation > 0 and stored received >= visible allocation,
--     on every product of the container, while some stored line is not 'received'.
--     A close approximation of the fixed rule (it compares per product, the fix
--     apportions per line). The backfill dry run is the exact answer.
WITH vis AS (
    SELECT a.inbound_shipment_id AS shipment_id, a.product_id,
           sum(a.allocated_quantity) AS visible_alloc
    FROM spo_allocations a
    WHERE a.inbound_shipment_id IS NOT NULL
      AND (a.retired_at IS NULL OR coalesce(a.quantity_received, 0) > 0)
    GROUP BY 1, 2
), per_product AS (
    SELECT l.shipment_id, l.product_id,
           sum(coalesce(l.quantity_received, 0)) AS stored_received,
           coalesce(max(v.visible_alloc), 0) AS visible_alloc,
           bool_and(coalesce(l.line_status, '') = 'received') AS stored_all_received
    FROM inbound_shipment_lines l
    LEFT JOIN vis v ON v.shipment_id = l.shipment_id AND v.product_id = l.product_id
    GROUP BY 1, 2
)
SELECT s.shipping_container_number, s.shipment_number, s.shipment_status
FROM inbound_shipments s
JOIN per_product p ON p.shipment_id = s.id
WHERE s.shipment_status NOT IN ('fully_received', 'closed', 'draft')
GROUP BY s.id, s.shipping_container_number, s.shipment_number, s.shipment_status
HAVING bool_and(p.visible_alloc > 0 AND p.stored_received >= p.visible_alloc)
   AND NOT bool_and(p.stored_all_received)
ORDER BY s.shipping_container_number;

-- Q3. Confirm the root cause on TCLU8176397: every SPO allocation on the container,
--     retired ones included, beside the stored line figures.
SELECT pr.product_code, a.spo_number, a.allocated_quantity, a.quantity_received,
       a.retired_at, a.receipt_status,
       l.quantity_shipped, l.spo_allocated_quantity AS stored_alloc,
       l.quantity_received AS stored_received, l.line_status AS stored_line_status,
       s.shipment_status AS stored_header
FROM inbound_shipments s
JOIN inbound_shipment_lines l ON l.shipment_id = s.id
JOIN products pr ON pr.id = l.product_id
LEFT JOIN spo_allocations a ON a.inbound_shipment_id = s.id AND a.product_id = l.product_id
WHERE upper(trim(s.shipping_container_number)) = 'TCLU8176397'
ORDER BY pr.product_code, a.retired_at NULLS FIRST, a.spo_number;

-- Q4. Scope of the line-status change (reviewer finding 2): stored shipment lines whose
--     product carries a retired, never-received allocation on the same container, so the
--     visible total (what the fix judges the status on) is below the unfiltered one.
--     lines_in_scope may change status. over_shipped_in_scope is the subset shipped above
--     the visible allocation: once that allocation is received such a line reads
--     'received' (the existing rule: received = the allocated part arrived), and the
--     unallocated rest stops counting as incoming. The exact per-line flips are the
--     backfill dry run (--stale-headers).
WITH alloc AS (
    SELECT a.inbound_shipment_id AS shipment_id, a.product_id,
           sum(a.allocated_quantity) AS all_alloc,
           sum(a.allocated_quantity) FILTER (
               WHERE a.retired_at IS NULL OR coalesce(a.quantity_received, 0) > 0) AS visible_alloc
    FROM spo_allocations a
    WHERE a.inbound_shipment_id IS NOT NULL
    GROUP BY 1, 2
)
SELECT count(*) AS lines_in_scope,
       count(*) FILTER (WHERE coalesce(l.quantity_shipped, 0) > coalesce(al.visible_alloc, 0))
           AS over_shipped_in_scope,
       count(DISTINCT l.shipment_id) AS containers_in_scope
FROM inbound_shipment_lines l
JOIN alloc al ON al.shipment_id = l.shipment_id AND al.product_id = l.product_id
JOIN inbound_shipments s ON s.id = l.shipment_id
WHERE coalesce(al.visible_alloc, 0) < al.all_alloc
  AND s.shipment_status NOT IN ('closed', 'draft');

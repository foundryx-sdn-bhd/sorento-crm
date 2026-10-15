-- ACCESS-MODEL (2 Oct 2026): read-only mapping of every contact's TODAY access to the proposed role.
-- Run with: PGOPTIONS='-c default_transaction_read_only=on' psql "$DATABASE_URL" -f this-file
-- Part A: one row per contact, today's agents + reveal keys + incoming field overrides -> proposed role + overrides.
-- Part B: summary per proposed role (must account for every contact).

WITH agents AS (
    SELECT c.respond_contact_id AS cid,
           array_agg(a.code ORDER BY a.code) AS codes
    FROM contact_agent_access c
    JOIN access_agents a ON a.id = c.agent_id
    WHERE c.is_allowed AND a.is_active
    GROUP BY 1
), reveals AS (
    SELECT respond_contact_id AS cid, array_agg(field_key ORDER BY field_key) AS keys
    FROM contact_field_reveals WHERE granted GROUP BY 1
), incoming_ovr AS (
    SELECT contact_id AS cid, count(*) AS n
    FROM agent_field_access WHERE contact_id IS NOT NULL GROUP BY 1
), today AS (
    SELECT r.id, r.name,
           coalesce(a.codes, '{}') AS agents,
           coalesce(v.keys, '{}') AS keys,
           coalesce(i.n, 0) AS incoming_field_overrides
    FROM respond_contacts r
    LEFT JOIN agents a ON a.cid = r.id
    LEFT JOIN reveals v ON v.cid = r.id
    LEFT JOIN incoming_ovr i ON i.cid = r.id
), mapped AS (
    SELECT t.*,
        CASE
            WHEN cardinality(t.agents) = 0 THEN '(none - refused today, refused after)'
            WHEN t.keys @> '{sales_orders.sales_report,scm.low_stock_report,purchase_orders.cost}' THEN 'Management'
            WHEN t.keys @> '{purchase_orders.cost,purchase_orders.supplier}' THEN 'Purchasing'
            ELSE 'Sales office'
        END AS proposed_role,
        concat_ws(' ',
            CASE WHEN cardinality(t.agents) > 0 AND NOT t.keys @> '{inventory.sellable}' THEN '-stock.sellable' END,
            CASE WHEN cardinality(t.agents) > 0 AND NOT t.keys @> '{purchase_orders.placed}' THEN '-stock.on_order -purchase_order' END,
            CASE WHEN t.keys @> '{sales_orders.outstanding}'
                  AND NOT t.keys @> '{sales_orders.sales_report,scm.low_stock_report,purchase_orders.cost}' THEN '+outstanding' END,
            CASE WHEN 'ideation' = ANY(t.agents) AND NOT t.keys @> '{sales_orders.sales_report}' THEN '+ideate' END,
            CASE WHEN t.incoming_field_overrides > 0 THEN '+incoming field overrides x' || t.incoming_field_overrides END
        ) AS overrides
    FROM today t
)
SELECT name, proposed_role, overrides, array_to_string(agents, ',') AS agents_today,
       array_to_string(keys, ',') AS reveal_keys_today
FROM mapped
ORDER BY proposed_role, name;

-- Part B
WITH agents AS (
    SELECT c.respond_contact_id AS cid, array_agg(a.code) AS codes
    FROM contact_agent_access c JOIN access_agents a ON a.id = c.agent_id
    WHERE c.is_allowed AND a.is_active GROUP BY 1
), reveals AS (
    SELECT respond_contact_id AS cid, array_agg(field_key) AS keys
    FROM contact_field_reveals WHERE granted GROUP BY 1
)
SELECT CASE
           WHEN a.codes IS NULL THEN '(none)'
           WHEN coalesce(v.keys,'{}') @> '{sales_orders.sales_report,scm.low_stock_report,purchase_orders.cost}' THEN 'Management'
           WHEN coalesce(v.keys,'{}') @> '{purchase_orders.cost,purchase_orders.supplier}' THEN 'Purchasing'
           ELSE 'Sales office'
       END AS proposed_role,
       count(*) AS contacts
FROM respond_contacts r
LEFT JOIN agents a ON a.cid = r.id
LEFT JOIN reveals v ON v.cid = r.id
GROUP BY 1 ORDER BY 1;

-- Part C: the 31 Dec lapse (every agent grant's valid_to)
SELECT date_trunc('day', valid_to) AS valid_to_day, count(*) AS allowed_grants
FROM contact_agent_access WHERE is_allowed GROUP BY 1 ORDER BY 1;

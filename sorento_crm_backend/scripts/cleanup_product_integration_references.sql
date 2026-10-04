-- PRODUCT-REF-COLLISION cleanup: remove every product row from integration_references.
--
-- Owner ruling 2, 3 Oct 2026: the item code IS the product's identity, so product refs are not
-- used for matching any more. Plan: documentation/plans/autocount/PLAN-product-ref-collision-03oct.md
--
-- RUN ONLY AFTER the PRODUCT-REF-COLLISION fix is deployed. Before it, line ingest re-creates a
-- product ref on the next SO/PO sync and the product feed mints one on its next push, so the rows
-- come straight back (and keep colliding).
--
-- Not a migration. Run by hand in psql, one step at a time. Nothing here touches any other
-- entity type (customers, suppliers, warehouses, agents keep their refs).

-- Step 1: dry run. Read-only counts; compare with what you expect before going on.
SELECT c.name AS company, split_part(ir.source_ref, ':', 1) AS book, count(*) AS product_refs
FROM integration_references ir
LEFT JOIN companies c ON c.id = ir.company_id
WHERE ir.entity_type = 'products'
GROUP BY 1, 2
ORDER BY 1, 2;

SELECT count(*) AS total_product_refs FROM integration_references WHERE entity_type = 'products';

-- Step 2: backup + delete in one transaction. The backup table keeps every column, so any row
-- can be restored with an INSERT ... SELECT from it.
BEGIN;

CREATE TABLE integration_references_products_bak_20261003 AS
SELECT * FROM integration_references WHERE entity_type = 'products';

-- Must equal total_product_refs from step 1.
SELECT count(*) AS backed_up FROM integration_references_products_bak_20261003;

DELETE FROM integration_references WHERE entity_type = 'products';

-- Must be 0.
SELECT count(*) AS remaining_product_refs FROM integration_references WHERE entity_type = 'products';

-- If both checks above are right: COMMIT;  otherwise: ROLLBACK;
-- (left for the operator to type on purpose)

-- Restore, if ever needed:
-- INSERT INTO integration_references SELECT * FROM integration_references_products_bak_20261003;
-- Drop the backup once you are sure (e.g. after a month):
-- DROP TABLE integration_references_products_bak_20261003;

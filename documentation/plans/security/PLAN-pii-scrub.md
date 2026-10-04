# PLAN: PII scrub of the public repo (PII-SCRUB)

**Status:** in review, PR #1457 (track: L, security; no migration, no UI change). History
rewrite is a separate owner decision, planned outside the repo.

UAC: [pii-scrub-acceptance-criteria.md](pii-scrub-acceptance-criteria.md)

## Journey

The repo is public. A 3 Oct audit found real personal data copied from the dev/prod copy into
fixtures, tests, evidence docs and tracked captures. Anyone browsing `main` could read customer
phone numbers, Respond.io contact ids, lorry plates, driver and contact names. After this lane,
`main` HEAD holds only fake values, and CI fails a PR that brings real ones back.

## Rulings (owner, 3 Oct 2026)

- In scope: phones, plates, Respond.io contact ids, WhatsApp per-user ids, personal emails,
  customer-contact and driver full names, and deleting tracked raw captures.
- Out of scope: company names (business data) and staff first names.
- No history rewrite or force-push in this PR.

## Design

1. **Inventory outside the repo.** Values found by pattern (MY mobiles, labelled plates, emails)
   and by matching dev-DB rows (contacts, customers, orders, users) read-only. Never committed.
2. **One stable fake per real value**, applied across every file so replay fixtures stay
   self-consistent:
   - phone: `+601x0000NNN` (operator code, length and separators kept);
   - Respond.io contact id: `9000000NN`; files named after an id are renamed; a fixture phone
     built from the id (`+60` + id, see `scripts/chatbot_record_turn.py`) follows the new id;
   - WhatsApp `MY.<digits>` user id: a stable fake of the same length;
   - plate: `PLATE-N`; driver: `DRIVER X`; contact: `CONTACT X`; email: `personN@example.com`.
   Names are replaced only inside their field (`*Driver:*`, `driver_name`, `firstName`, ...),
   plus vetted full names and names written next to a phone. Spreadsheet fixtures are edited
   cell by cell (driver and plate columns, phone strings).
3. **Delete raw captures**: `.playwright-mcp/`, `*.har`, `*.rdb`, root page snapshots,
   `.cursor/debug.log`; `.gitignore` covers them. (`sorento_crm_backend/.env.browse` stays tracked: dummy values only, and `tests/test_dev_login.py` reads it.)
4. **Guard**: `scripts/pii_guard.py` (stdlib) in CI (`pii-guard` fast gate) and in the pre-push
   hook. It reads every tracked text file and the XML inside Office files, and fails on a real
   looking MY mobile (several prefix and separator shapes), a value under a phone-like key, a
   labelled plate, or a tracked raw-capture path. Only documented fake shapes pass. Findings
   print path, line and kind, never the value.

## Not done here (named triggers)

- Unlabelled plates and names in free prose are not detectable by pattern; revisit if a new
  leak shape is found.
- The recorder still writes the real contact id into new recordings; a recording must be
  scrubbed before commit (the guard does not know contact ids).
- Credential rotation and the history rewrite: owner decisions, tracked outside the repo.

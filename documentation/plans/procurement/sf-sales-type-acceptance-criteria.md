# UAC: Sponsorship forms keep a sales type (SF-SALES-TYPE)

Plan: `PLAN-sf-sales-type-16oct.md`.

- ST1 Staff create SF: picking a customer whose debtor type is Project fills Sales Type = Project; Project cash
  or Cash sales fills Cash Sales. The value stays editable and is saved on the SF.
- ST2 Staff edit SF: same field, same fill on a new pick; saving keeps the chosen value. Changing the customer's
  debtor type later does NOT change a saved SF (frozen, like PR).
- ST3 Portal SF: same field (Sales type), same fill on pick, saved with the draft and the submission.
- ST4 Sales type is NOT required on an SF (an SF with free-text customer saves with it blank). PR stays required.
- ST5 SF list shows a Sales Type column; SF detail, Excel and PDF show Sales Type after Sponsor Subject;
  the SF revision viewer shows it with its label.
- ST6 CS routing code unchanged.
- ST7 Existing SFs: no data change by the PR. `scripts/backfill_sf_sales_type.py` dry-run reports counts and
  writes nothing; `--apply` fills only SFs with a customer link and blank sales type, from the customer's current
  debtor type; unlinked SFs, PRs and SFs that already have a value are untouched; a customer with no debtor type
  is skipped and counted.

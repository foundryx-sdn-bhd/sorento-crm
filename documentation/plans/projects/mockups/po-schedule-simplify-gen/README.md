# Generator for `po-schedule-simplify-v*.html`

Builds the PM-SIMPLIFY mock from one project's rows. Read-only against a local database.

    python3 extract.py postgresql://... <project_id> data.json   # never commit data.json
    python3 build.py base.css ../po-schedule-simplify-vN.html counts.json data.json

`base.css` is the style block of `../delivery-schedule-review.html`. `counts.json` holds the
measured before/after table. `data.json` may hold masked customer rows: it stays out of the repo.

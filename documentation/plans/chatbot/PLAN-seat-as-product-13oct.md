# PLAN: seat cover = product resolution (SEAT-AS-PRODUCT)

Status: Review (lane SEAT-AS-PRODUCT, PR #1613). Track: standard (chatbot turn pipeline; no
migration, no FE change, no auth/RBAC change). Card: `CARD-seat-as-product-13oct.md` (crew
answered Q2 b, Q3 a, Q4 yes, Q5 yes; Q1 (a), wording `No seat cover is on record for <code>.` approved by the owner 13 Oct).

## Owner rule (13 Oct)

"Resolving seat cover should just be like resolving product. The domain behind should depend on
what's resolved by the parser, as usual. No new lanes in answering. Stock representation should
reuse our existing function, no new function. Same for ETA."

## Failures on main (#1601, parser v88) and their causes

1. "check stock seat cover for SRTWC8122" got a new `Code: / Stock:` format, and
2. "eta seat cover for SRTWC8383-RL-MBL" got stock: `run_fetch` swapped the domain's tool for
   `crm_find` whenever the parse carried `find`, whatever the domain, and `fetch._find_output`
   printed the find presenter's own lines.
3. "seat cover for SRTWC8383-RL-MBL", two answers by contact: the v52 text's BARE ENTITY
   CONTINUATION rule continues the previous turn's domain for a message that names only a code,
   while #1601's FIND_RULE pinned master_products. A contact whose last turn was a Technical
   Specifications file ask got that file ask again; the other got the find reply.

## Design

- `lanes/business/find.as_linked_products(db, verdict)`, called by the engine after grounding and
  before APPLY's first pass: when the parse carries `find` with `fits`, every product code the
  message names that sits on a `product_compatibility` link (either end; the data decides the
  direction) is replaced by its linked codes, as product entities typed this message. Every
  other key and entity is the parser's. The turn then runs on as if those codes were typed.
  The code lookup compares normalised codes (`product_alternative_service._norm_sql`, the
  indexed expression) on the turn's company-scoped session.
- Q2 (b): a code on no link answers as typed. Q3 (a): a code that is no product's exact code goes
  on as typed (the resolver's picker or miss). Q1 (a): when no named code has a link, the reply
  is one line per code (`find.NO_SEAT_COVER_LINE`, the #1601 wording with its ms / zh), said
  through the engine's existing one-line no-fetch seam (`customer_scope_refused` +
  `account_question`).
- Removed: the `crm_find` override in `run_fetch`, `fetch._find_output`, `named_this_turn`,
  `route_args`, and `crm_find` from the chatbot allow-list and domain map. The MCP tool, the
  route and the registry stay (a generic read; `fits` still uses them).
- Parser: `product_find.FIND_RULE` (the live `{{find_filters}}` variable) now says the relation
  picks WHICH products only and every other key is what it would be with the related code
  typed; with nothing asked, BARE ENTITY CONTINUATION decides. Prod (v52 line) gets it through
  a NEW unlabelled version from `scripts.publish_parser_find_block --from-version 52`; no label
  moves. Dev v88 carries the token, so it reads the new text on deploy.

## Tests

`tests/chatbot/test_seat_as_product.py` (red first): parity of tool calls and reply against the
cover codes typed directly for stock, ETA, no domain, photo, mixed and reverse; the Q1 line with
no fetch; the resolution unit tests; kill tests (no `crm_find` in the lane, no `_find_output`,
rule text, allow-list). Live: `tests/chatbot/test_find_live_parser.py` golden set, each fit ask
parsed beside its typed-code control (opt-in, `CHATBOT_LIVE_LLM=1`).

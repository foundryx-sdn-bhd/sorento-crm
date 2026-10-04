# PLAN: attribute-first asks, "which products have X", across every product and domain

Status: REVIVED 26 Sep 2026 on PR #833 - origin/main merged, revive repairs R35 to R38 and owner ruling R39 (no paging) built, reviewer pass fix round done; owner hand test round 2 (W1 to W6: brand binding, plain-words header, readable rows, paging keeps the set, chatbot default brand, water basin) built tester first; owner hand test of round 2 (round 3, W1 to W6: vertical rows, a count after a page continues, a new class word starts a new set, no "(default)", brand scope proven) built red first; owner console test of round 3 (round 4, R1 to R7: brand weights, line-by-line header, two-line rows, a miss says what it searched, the clarify answer reruns the ask, unknown values said back, plain words) built red first; reviewer pass at d6fa2b31 (round 5: B1 R6 fires only on a value-position word, S1 list values in the near miss, S2 one display helper, S3 serial_ddl, N1 to N4) fixed red first; reviewer pass at 34cb4697 (round 6: B1-r5 whole-word product name match, N-r5-1 to N-r5-3) fixed red first; owner hand test of rounds 4 to 6 (round 7, items 1 to 7: ETA words and ETA row shape, a code's descriptor picks its variants, code then descriptor, the normal stock header, a brand from the offer narrows the set, certificates only in the attachment shape, plain words) fixed red first; main 11bf373ec merged 27 Sep 2026 (511_attribute_first_lookup_sets re-parented onto merge_27sep_three_heads); owner retest of round 7 (round 8, F1 the parser bounded by the specification registry with a deterministic grounding step, F2 one answer shape per domain) built red first; main fcbfa379 merged 28 Sep 2026 (511 re-parented onto fin_0001_billing_documents); owner hand test of 28 Sep (round 9, one reply structure for every attribute ask, AC-1389 to AC-1395) built red first, single alembic head spk_0002_colour_word_spec; owner retest of round 10 (round 12, attribute-first by structure, AC-1396 to AC-1398) built; awaiting the orchestrator's CI label, an owner console pass on the prod copy and merge go. Track: full. Lane `feat/chatbot-attribute-first-asks`.
Test report: `attribute-first-asks-test-report.md`.
UAC: `attribute-first-asks-acceptance-criteria.md`.
Supersedes: `documentation/plans/_archive/chatbot/PLAN-spec-backward-search.md` (its backend half
shipped as `product_predicate_service.py`; its n8n contract is obsolete, the Python lane owns
every business decision and n8n only relays the message and posts the reply).
Review surface that produced the decisions: `.lavish/reverse-asking-gap.html` (session ended by
the owner 10 Sep 2026).

## Journey

A contact asks for a SET, not a record: "which tap has cert", "which basin got stock", "any
shower set on promo", "which sink has incoming", "which item has PPS cert", "basin ada gambar".
Today every one of these is read as a record lookup with a missing product and answers "no
certificate matched these" or a product picker. The contact should get the same structured block
they get for one product, for the first five qualifying products, files attached, with a header
that says how many qualify, and "more" to page. A miss goes through today's did-you-mean flow
with the set named. An unknown word clarifies and never says "none".

## Current state, measured (prod copy restored 2026-09-10)

- `resolve_product_set()` in `app/services/product_predicate_service.py` computes described set
  ∩ predicate legs (certificate, stock, attachment_type, promotion) over the full company-scoped
  catalogue with an honest family count. It runs only when the resolve body carries `require`.
- `resolve_entity_body` in `app/services/chatbot/lanes/business/resolve_gate.py` never sends
  `require`. The parser (registry prompt, business_query) has no shape-B key. So HAS is
  unreachable from a turn.
- Direct calls on the copy: tap + certificate 1,256 families; water closet 460; wash basin +
  stock 586; scheme PPS 940; any certificate 2,704; products with open incoming lines 624 (no
  leg exists for it).
- Silent zero: "water tap", "water basin", "bidet" give `clause=None` and
  `unrecognized_terms=[]` from `filter_specs`, so the caller cannot tell "none qualify" from "not
  understood".
- The registry already binds "bidet" as `product_type=bidet`, "sorento" as `brand=SORENTO`,
  "shower set" as `product_type=shower_set` (via `derive_search_inputs`), but `filter_specs`
  membership is class-only by the old plan's decision, so those bindings are dropped.
- LOOKUP already matches "bidet" to 3 products by name (the screenshot picker). Those ids are a
  perfectly good described set and are thrown away today.
- The certificate register covers 3,002 products; the legacy Certification attachment type
  covers 2,928, all of them also in the register (2,894 both, 74 register-only, 0
  attachment-only). Register wins (owner decision D1).
- The register has 11 scheme spellings; WEPLS / WELPS / WELPLS are one scheme spelled three
  ways. No scheme is spelled "watermark".
- Lookup sets (`lookup_sets` / `lookup_options` / `lookup_option_keywords`,
  `LookupResolverService.resolve(set_key, raw)`) exist with 9 sets today and a System page to
  edit them. They are the alias mechanism (owner's suggestion, confirmed a fit: exact value,
  label, then keyword, 404 on miss).
- No "more" paging exists in the lane today. AC-1317 is the only genuinely new mechanic.

## Decisions (owner, 10 Sep 2026)

| # | Decision |
|---|----------|
| D1 | "has cert" = certificate register (`certificate` leg). The Certification attachment type is never the truth source for the predicate. |
| D2 | Dissolved: "bidet" is already an entity; LOOKUP's name matches join the described set. No name-fallback design. |
| D3 | A brand entity scopes the set (Product.brand). |
| D4 | All five predicates (certificate, stock, attachment_type, incoming, promotion) in one lane. |
| D5 | A set answer shows 5 products WITH files; header carries the full count; "more" pages by 5. |
| D6 | Default validity = any active certificate, expired rows flagged per row as today. "watermark" maps to no scheme and clarifies, listing schemes on file. |
| D7 | No parser prompt change, no registry version, no new MCP tool, no new reply format. Predicate derived mechanically from `intent_hint` + entity hints, like `derive_routing()`. |

## Shape

```
message -> parser (unchanged) -> lane derive_require() -> resolve body (+require, +predicate_words)
       -> resolver: exact code resolved?  yes -> LOOKUP, bytes identical to today
                                          no  -> HAS: described set ∩ legs -> ids into resolutions[].matches
       -> existing fetch (TYPE_TO_PARAM product -> product_ids, limit 5) -> existing MCP list tool
       -> existing render + one header line          | zero -> existing miss / DYM with the set named
```

Described set = UNION of (a) product ids LOOKUP matched for the caller's product tokens, and
(b) products whose spec row matches any `class` / `product_type` / `brand` binding derived from
`query` minus `predicate_words`. Then ∩ every leg in `require`. Brand from an entity or a
binding is applied as a filter on the whole set.

## Work items

| # | Item | Where | AC |
|---|------|-------|----|
| A | Honest zero: a free term whose content words are known but bind no class / product_type / brand goes to `unrecognized_terms` | `app/services/product_spec_search.py` `filter_specs` | 1301, 1302 |
| A2 | Question words join `_PHRASE_STOPWORDS` ("which", "what", "who", "where", "when", "how", "many"). Measured: `_PHRASE_STOPWORDS` (line 521) has "any / some / all / has / got" but no question words, so "which basin got stock" reports "which" as unrecognised today | `product_spec_search.py` | 1320 |
| B1 | `derive_require(parser_output)` pure map; reuse `_CERT_RE` from `head/output_exchange.py` | new `app/services/chatbot/lanes/business/predicate.py` | 1303 |
| B2 | `resolve_entity_body` adds `require` + `predicate_words` when B1 returns one; every other key untouched | `lanes/business/resolve_gate.py` | 1304 |
| C1 | `ResolveReferenceRequest` gains `predicate_words: list[str]`; the require branch runs only when NO CODE-SHAPED product token resolved to any product match (`_is_code_shaped` in answer.py is the shape test: letters and digits mixed; tiers exact / head_code / prefix / substring all count). A word token ("bidet", "sorento", "tap") never blocks HAS, whatever tier it resolved at. Console finding 11 Sep: "check stock srtwc286" (prefix, 7 variants) turned into a set answer with a header, and "which sorento bidet has cert" stayed a picker because "sorento" resolved `exact` to a product row by name. Earlier grill finding: `_every_caller_token_resolved` is the WRONG gate (True for "bidet"). Otherwise fall through to today's code path unchanged, byte-identical | `app/api/v1/system/references.py` resolve POST | 1305 |
| C4 | Gate bypass: when the resolver result carries a `predicate` block at all (any `qualifying_total`, zero included; tester finding 10 Sep: the zero case of AC-1319 hits the same picker), `gate.py` skips BOTH the `REQUIRE_SPECIFIC_DOMAINS` ambiguity block (product_attachment, incoming) and the product_attachment "subject product did not resolve; refusing to scope on carried entities" block (gate.py about line 342, which fires when a product raw such as "water tap" is unresolved), and passes every qualifying match on as entities. Grill finding 10 Sep: without this the HAS ids (ambiguous=True, spec_search tier) fall into the "needs to be more specific" picker, which is the screenshot B defect in a new coat | `lanes/business/gate.py` | 1326 |
| C2 | Described-set inputs: collect LOOKUP product ids from `result["resolutions"]`; call `derive_search_inputs` on `query` minus `predicate_words` for class / product_type / brand bindings; free_terms = the raw of every `category` entity (the parser emits hint "category" for a class noun; the head retypes it to an unresolved `product` entity, console finding 11 Sep) and of every product entity token that did NOT resolve, else the remainder of `query` after removing `predicate_words` (each attachment_type entity's raw AND canonical_code, plus every word matching `_CERT_RE`, plus the intent word) and the phrase stopwords, as ONE term; never `_content_words(query)` on its own (that produced the scope term "tap cert") (S3 tester finding: the S1 build dropped free_terms, so a bare class word never scoped the set and "which tap has cert" counted every certified product); pass `product_ids`, `specs`, `free_terms`, brand to the service | `references.py` (veneer, zero SQL) | 1306, 1309, 1320 |
| C3 | `filter_specs` membership accepts `product_type` and `brand` entries; `resolve_product_set` accepts `product_ids` and `brand`; union-then-intersect semantics; count still by variant family | `product_spec_search.py`, `product_predicate_service.py` | 1307, 1308, 1310 |
| D1 | `incoming` leg (ORM, joined through `inbound_shipments` for scope) + `REQUIRE_LEGS` entry | `product_predicate_service.py` | 1311 |
| D2 | `_leg_attachment_type`: exact match, then `LookupResolverService.resolve("attachment_type_alias", label)`; `_leg_certificate`: scheme through `certificate_scheme` set; unknown scheme returns `schemes_on_file` in the echo | `product_predicate_service.py` | 1312, 1313 |
| D3 | Migration creates the two lookup SETS only (`certificate_scheme`, `attachment_type_alias`, company-shared, no options). The owner enters options and keywords on System > Lookup Sets (owner decision 10 Sep: "don't need to seed for scheme, I will enter"). A leg whose set has no matching option treats the word as unrecognised; a missing set is the same, never a 500. Register spelling clean-up (WEPLS / WELPS / WELPLS) is register data, not this lane | `alembic/versions/5xx_attribute_first_lookup_sets.py` | 1314 |
| E1 | Fetch: a HAS turn passes the FIRST FIVE qualifying `product_ids` through the existing `TYPE_TO_PARAM` path and leaves the tool's row `limit` at its default; the page is five PRODUCTS, never five rows (console finding 11 Sep: `limit=5` on the stock tool cut the answer to 5 warehouse rows spanning 4 products under a header saying "Showing 5"; the cert tool has 3 to 4 files per product). No tool change | `lanes/business/fetch.py` | 1315 |
| E2 | Header line from `predicate.qualifying_total` prepended to the existing render. Seams: `answer.build_set_header(qualifying_total, shown, set_noun, require) -> str` ("1,256 taps have certificates. Showing 5."; "3 taps have certificates." when all fit; n == 1 reads "1 tap has certificates."; thousands separator; two keys read "have certificates and stock") and `answer.set_noun_for(class_labels, fallback="products") -> str` ("Tap" → "taps", "Wash Basin" → "wash basins", [] → "products"). Predicate nouns per leg: certificate "certificates", stock "stock", attachment_type the resolved type name lower-cased ("product photos"), incoming "incoming stock", promotion "a promotion" | `lanes/business/answer.py` + `tail/compile_state.py` header | 1316, 1318 |
| E3 | "more" paging. After a set answer the tail writes `variables.selection_context = "set_page"` and `variables.last_result_set = {"kind": "set_page", "qualifying_ids": [...capped at 200], "offset": 5, "qualifying_total": N, "require": {...}, "set_noun": "taps", "domain": "product_attachment", "tool": "crm_master_product_attachments_list"}`; the existing offer-carry lifetime applies (a domain change clears it). On the next turn a bare reply of four words or fewer containing "more", "next" or "lagi" under `selection_context == "set_page"` is answered from the carried ids with NO resolver call: the same tool with `product_ids` = ids[offset:offset+5], header "N taps have certificates. Showing 6 to 10.", offset advanced; past the end the reply is "That was all N taps." and the carry is cleared; past id 200 the reply says to narrow the ask. Any other message leaves the carry to the normal ladder. Two measured constraints (S4 tester, 11 Sep): `head/route.py` `decide()` classifies a short message with no domain, intent or entity as `branch_kind = "low_signal"` (casual lane) before the business lane ever runs, so the bare-word arm must fire at or before routing (when the prior session carries `selection_context == "set_page"`, route the turn to the business lane with the paging marker); and `compile_state._offer_carry` gates every carry on `jsc.is_array(prev_set)`, so the dict-shaped `set_page` carry needs its own arm there. `output_exchange.py` bans new text-sniffing sites for parity reasons; this arm is plan-approved and reads a fixed word list under a specific carried context only, the reviewer should read E3 before flagging it | `head/route.py`, `tail/compile_state.py` `_offer_carry`, `lanes/business/answer.py`, `lanes/business/fetch.py` | 1317 |
| F1 | Zero + empty unrecognised → existing `DOMAIN_PROBE` miss flow; "Couldn't find" sentence names set + predicate + the checked codes when the set came from LOOKUP ids (5 max) | `lanes/business/miss_suggest.py`, `answer.py` not_found copy | 1319 |
| F2 | Zero + unrecognised term → "I don't know '<term>' as a product type. Did you mean <a, b, c>?" Nearest names = class labels and registry `product_type` values that share a content word with the term ("water tap" → "tap", "shower tap"), else the top 3 `difflib.get_close_matches`; with NO candidate at all the sentence is "I don't know '<term>' as a product type. Try a product type such as tap, wash basin, water closet." (the three most common class labels), never the empty "Did you mean the product types I know?" (console finding 11 Sep); never "Couldn't find" for this case | `answer.py` | 1320 |
| F3 | Scheme miss copy names `schemes_on_file` | `answer.py` | 1321 |
| G | Invariant tests: byte-identical body without a leg intent; byte-identical resolver response with an exact code; field-reveal unchanged | `tests/test_resolve_predicate.py`, `tests/chatbot/`, new `tests/test_chatbot_lane_require.py` | 1322 to 1324 |
| H | Console verification on the prod copy, nine utterances, traces recorded in the test report | `attribute-first-asks-test-report.md` | 1325 |

## Slices (tracer bullets, in order; one branch, one PR)

| Slice | Delivers | Work items | ACs |
|-------|----------|------------|-----|
| S1 | The predicate reaches the resolver and a set answer renders through the existing block. Both screenshot utterances change. | A, A2, B1, B2, C1, C2, C3, C4, E1, F1 (zero → existing miss, set named) | 1301 to 1309, 1310 (four existing legs), 1315, 1319, 1322, 1324, 1326 |
| S2 | Incoming leg; scheme and label aliases through lookup sets; the two sets created empty; scheme-miss copy names the schemes on file | D1, D2, D3, F3 | 1310 (incoming leg), 1311 to 1314, 1321 |
| S3 | Count header, validity default, clarify copy for unknown terms, field-reveal parity | E2, F2 | 1316, 1318, 1320, 1323 |
| S4 | "more" paging by 5 through the offer carry | E3 | 1317 |
| H | Console verification on the prod copy, test report | H | 1325 |

## Console fix round 2 (11 Sep 2026, replayed through the real parser output)

Measured with the stored `chatbot.turns` parser output replayed through `resolve_entity_body` and the resolver (script kept in the session scratchpad as `replay_resolve.py`; its output is the evidence for every line below).

| # | Observed | Cause | Rule |
|---|----------|-------|------|
| R1 | "check stock srtwc286" got a set header ("7 water closets have stock.") | the lane sends `match_mode: "and"`, so the forward result lives in `result["intersection"]`, and the code-shape gate only read `resolutions`; the 7 prefix matches never counted as "a code-shaped token resolved" | C1 reads both shapes: HAS is skipped when any code-shaped token has a product match in `intersection` OR in its own resolution |
| R2 | "which tap has cert" answered "Found: <200 codes>" plus a picker of two products named "...COLD TAP" | HAS ran (908 taps) but the forward substring matches for the word token "tap" stayed in `resolutions`, the gate built its picker from them, and the found line enumerated the 200 spec_search ids | when HAS ran, product matches of WORD tokens are removed from `resolutions` and `intersection` (only the spec_search resolution carries products; attachment_type and other non-product resolutions stay); a HAS reply prints the set header in place of the "Found:" enumeration, never a code list |
| R3 | "which sorento bidet has cert" kept the 3-bidet picker although HAS found 1 (SRTWT5875, a Sorento product with product_type bidet and a cert) | same as R2 | same as R2; the shipped reply is "1 tap has certificates." plus its block (class Tap is the catalogue's own word; REV-S6 accepted) |
| R4 | "which item has PPS cert" answered "I don't know 'PPS' as a product type" | the parser emitted ONE attachment_type entity, raw "PPS", and no cert-word entity; `derive_require` saw no `_CERT_RE` raw and produced `{"attachment_type": "PPS"}` | `derive_require` mirrors `derive_routing`'s `is_cert`: certificate when any attachment_type raw matches `_CERT_RE` OR the intent is check_product_attachment and `_CERTIFICATE_RE` matches `user_goal` or the message text; every attachment_type raw that does NOT match `_CERT_RE` becomes the scheme word |
| R5 | a scheme word that IS a register spelling would still miss while the lookup set is empty | the leg consulted only the set | `_leg_certificate` first tries case-insensitive equality against the register's distinct active schemes (company-scoped), then the lookup set; only then unrecognised with `schemes_on_file` |
| R6 | "which basin has photo" answered "I don't know 'photo' as a product type" | the clarify copy assumed the unrecognised word was a set word; it was the attachment label (alias set empty, as designed) | F2 branches on WHICH require key went unrecognised: attachment label → "I don't know 'photo' as a document type. Types I know: Product Photos, Technical Specifications, Certification, Product Videos." (product-facing AttachmentType names, company-scoped); scheme → the AC-1321 sentence; set word → the product-type sentence |
| R7 | "which sink has incoming" counted all 620 incoming products with nine class labels | "sink" was a product entity that "resolved" to customers and promotions, so C2 treated it as resolved and derived no scope term | C2: for every WORD token (not code-shaped) the raw ALWAYS goes to free_terms, whatever it resolved to; its LOOKUP product matches (if any) are unioned in as before; code-shaped tokens never reach HAS (R1) |
| R8 | "which basin got stock" said "Showing 11." over five products | `shown` counted tool rows (warehouse x location), not products | E2: `shown` = distinct product codes rendered |
| R9 | `unrecognized_terms: ["check"]` on the stock turn | "check" is not a phrase stopword | A2 adds "check", "checking", "list", "tell" |
| R10 | "which zzqx has cert" fell back to "Try a product type such as a class or product type I know." | `_common_class_labels` returned nothing under the live contact scope | the query runs against `product_specifications` under the request's scope and falls back to the registry's known class names when the scoped query is empty; never an empty list in the sentence |
| R12 | "which sorento bidet has cert" (parser variant: ONE product entity "Sorento bidet") answered "Here's what you want: ... Couldn't find: 'Sorento bidet' (product). But no certificate matched these." | the entity folded to the token "Sorentobidet" (0 matches); HAS found SRTWT5875 (qualifying 1) but the answer half still listed the unresolved token as not found and took the miss copy | when `predicate.qualifying_total > 0`, an unresolved WORD token is the set's description, never a "Couldn't find" item; the set answer renders |
| R13 | "which item has PPS cert" (parser variant: attachment_type raw "PPS cert") answered "I don't know 'item pps' as a product type" live, while the deterministic replay of the same stored parser output returned a clean predicate (940, no unrecognised terms) | the live resolve body carries `understand_phrase: true`, so the HAS branch let the MODEL phrase reader (`derive_search_inputs(allow_model=True)`) contribute free terms, and it produced "item pps"; the replay had no model key and read deterministically | the HAS branch reads bindings with `allow_model=False` only (the model read stays on the spec_fallback path where it was built); and predicate_words are stripped word by word, so a phrase such as "PPS cert" removes both words from the remainder |
| R11 | "any shower set on promo" → "You have no access levels configured to get promotions." | console contact 900000105 has no access levels; a data prerequisite of the promotion domain | verification uses a contact with access levels for the promotion case; not a lane change |

## Security review findings (11 Sep 2026), rules adopted

| # | Finding | Rule |
|---|---------|------|
| SEC-B1 | A "more" page runs before the access_check entry, stamps `tier_gate: None`, and the fetch copies the parser's empty `access_levels` into the tool args; on a promotion set that removes the tier filter entirely (empty list → no filter in `marketing_service`). The carry is armed off `gate.predicate` alone, so a tier-ask turn also arms it | the set_page carry is armed ONLY when the fetch rendered a set answer (result-bearing arm), never off the gate; the carry stores the recomposed `access_levels` and the page turn re-injects them into the fetch args; a page turn on a promotion carry still runs the tier gate |
| SEC-S1 | `_leg_promotion` ignores the caller's `access_levels`, so `qualifying_total` and the named products can disclose tier-restricted promotions | `resolve_product_set` accepts `access_levels`; `_leg_promotion` intersects `Promotion.access_levels` with the same name → code translation `_apply_promotion_access_levels_filter` uses; the resolver threads `payload.access_levels` |
| SEC-S2 | `_common_class_labels` reads `ProductSpecifications` (not company-scoped) and `_nearest_class_labels` uses `stored_class_labels` (raw text() SQL), both customer-visible, both cross-company | both join `Product` (scoped) so the labels come from the caller's own catalogue; no raw SQL |
| SEC-N1 | carry has no lifetime beyond a domain change; `carry["tool"]` is dead state | the carry clears on any business answer that is not a page (a new set answer re-arms fresh); `tool` removed |

## Correctness review findings (11 Sep 2026), rules adopted

| # | Finding | Rule |
|---|---------|------|
| REV-B1 | The `do_orm_execute` company filter is `with_loader_criteria` on ORM entities and does NOT reach a bare `exists().where(...)` subquery, so every leg (stock, attachment_type, incoming, promotion) counts a company-B child row on a company-A product. The AC-1310 tests seeded product AND child in company B, so the outer Product filter produced the zero and the legs proved nothing (kill test: an unscoped raw-SQL incoming leg stayed green) | every leg's EXISTS carries an explicit same-company predicate: `<child>.company_id == Product.company_id` for owned children (Stock, ProductAttachment, InboundShipmentLine via InboundShipment, PromotionProduct via Promotion); Certificate is `__company_shared__` and keeps its join through the shared side. AC-1310 tests reseed: product in the caller's company, child row in company B, expect 0; and the reverse (child in the caller's company on a company-B product) also 0. The old comment "the line's own company scope already isolates it" is deleted |
| REV-S1 | a fresh set answer that fits on one page (or a zero miss) leaves the PREVIOUS carry armed | AC-1336 (already contract): a set answer always replaces the carry, small answers and misses clear it |
| REV-S2 | `_PREDICATE_WORD_RE_CACHE` is an unbounded dict keyed by customer-controlled strings | deleted; compile inline |
| REV-S3 | `_CERT_WORD_RE` and `_SET_PAGE_ID_CAP` are hand-synced copies across the module boundary | one test pins `answer.SET_PAGE_ID_CAP == references._SET_PAGE_ID_CAP` and `_CERT_WORD_RE.pattern == _CERT_RE.pattern` |
| REV-S4 | predicate_words strip as one contiguous phrase, not word by word | split each predicate_words entry on whitespace and strip every word (AC-1332 as written) |
| REV-S5 | `specs` parameter shadowed inside `resolve_product_set` | rename the loop variable |
| REV-S6 | header noun for "which sorento bidet has cert" is "1 tap has certificates." in prod (class Tap wins over product_type bidet), while the plan said "1 Sorento bidet" | ACCEPTED as shipped: the class label is the catalogue's own word for the product; plan R3 and AC-1327 amended to the shipped string |
| REV-N1 | `is_more_reply` matches any short message containing more/next/lagi ("no more", "next week?") | the message, lower-cased and stripped of punctuation, must equal one of: more, next, lagi, more please, show more, next 5, next five, lagi 5, or "more" followed by a single number; anything else is not a page |
| REV-N2 | pluralisation by bare "+s" and singularisation by stripping "s" ("accessoriess") | `set_noun_for` uses a tiny irregular map (accessory → accessories, jacuzzi → jacuzzis) then "+s"; the singular is the class label itself, never a stripped plural |
| REV-N3 | latency of the 200-candidate HAS resolve unmeasured | measured on the prod copy before the PR and recorded in the test report |

## Console fix round 3 + review re-check (11 Sep 2026)

Console pass 5 on the fixed code (no reload) left two wrong turns; the reviewer and security-reviewer re-check closed B1/S2/S4 and raised the items below. Everything measured on the prod copy (`psql` on `product_specifications`, `contact_access_types`; the reviewers' probes are quoted in the test report).

| # | Observed | Cause | Rule |
|---|----------|-------|------|
| R14 | "which item has PPS cert" (head variant: attachment raw normalised to "certificate", "PPS" dropped before the lane) answered "I don't know 'item pps' as a product type" | the lane derived a bare `{"certificate": true}` with predicate_words ["certificate"], so "pps" stayed in the remainder and reached `filter_specs` as a set word | in the resolver's HAS branch, when `require.certificate` is bare `true`, every remainder word that equals (case-insensitive) a register scheme spelling (`schemes_on_file`, company-scoped) or a `certificate_scheme` lookup keyword becomes the scheme: `require` is promoted to `{"certificate": {"scheme": <register spelling>}}` and the word leaves the remainder before `_has_turn_free_terms`. Data-driven off the register and the set, no scheme list in code. Helper lives in `product_predicate_service` (`recover_certificate_scheme`), the branch calls it once |
| R15 | "which bathroom accessory has stock" answered qualifying 0 while SQL says 2,040 products carry class Bathroom Accessory (999 with stock) | S1's `filter_specs` class membership added `provenance.class.source IS DISTINCT FROM 'category'`; every one of the 2,040 rows is category-sourced. Two labels exist ONLY through category filing: Bathroom Accessory (2,040) and Bathtub and Jacuzzi (93); Tap has 1,819 such rows beside 2,788 derived ones | the class membership filter drops the provenance clause: a product filed under the class by its own category IS a member of the described set (the company's own filing is the strongest statement of what the product is). Counts in the report move accordingly (taps rise above 908); the header stays honest either way |
| R16 | the same turn's copy read "Couldn't find a a match with stock." | F1's zero copy builds its subject from brand + product-hint raws only; a category or product_type entity leaves the subject empty and the fallback literal "a match" is glued after "a" | the subject is brand raw + product raws + category / product_type raws, then the predicate's `class_labels`; when all are empty the sentence is "Couldn't find any product with <predicate>." Never two articles |
| R17 (REV-N1, SEC blocker) | `resolve_product_set(require={"certificate": true})` under company A counts a certificate stamped company B and linked to A's product (measured: qualifying_total 1); the scheme form is safe only because `_schemes_on_file` is an ORM read | `_leg_certificate` was the one leg REV-B1 left without a same-company predicate (`certificate_products` carries no company) | the leg's EXISTS carries `or_(Certificate.company_id.is_(None), Certificate.company_id == Product.company_id)`: the NULL arm keeps deliberately shared certificates (`Certificate.__company_shared__`) counting, strict equality would undercount them. A fifth "both directions" test alongside the four that landed |
| R18 (SEC-S1 re-check) | the lane sends `parse_output["access_levels"]`, which the head has already collapsed to tier TOKENS (`dealer`, `office`, `end_user`); `_access_level_codes` translates NAMES only, so `['dealer']` becomes an empty set (false "0 shower sets have a promotion") and `[]` stays tier-blind | the resolve body never carries the recomposed names; those exist only in the fetch step's `tier_gate` | `_access_level_codes` also accepts a value equal to a `contact_access_types.code`, and a bare tier token selects every code that IS the token or ends with `_<token>` (`dealer` -> dealer, cabana_dealer, mocha_dealer, nl_dealer; `office` -> sorento_office, cabana_office, mocha_office; `end_user` -> end_user), read off the table, no list in code. The count is then an upper bound across the tier's brands; the rendered rows are still filtered by the recomposed names at the tool (SEC-B1). The unstated-tier case stays `None` by design: the tier ask fires before anything renders, and the resolve re-runs with the stated token on the answer turn |
| R19 (REV-S1 re-check) | after "which tap has cert", a same-domain clarify ("which water tap has cert", AC-1320, nothing rendered) leaves the tap carry armed, so "more" pages taps 6 to 10 under the old header | `_set_page_carry` clears on `answered or topic.changed`, and `answered` needs rendered rows or a roster; a clarify has neither. The AC-1336 test switched domain, so it never exercised the same-domain gap (the `answered` clause can be reverted with the suite green) | the carry survives ONLY a page continuation; every other business-lane turn clears it (same domain or not, answered or not) and a rendered set answer re-arms it fresh. Two tests: same-domain non-page answer clears; same-domain zero-qualifying clarify clears, so "more" afterwards gets the no-set copy |
| R20 | `product_predicate_service.py` carries ten em-dashes in comments and docstrings | written by the coder; the pre-push dash guard fails the push | replace with " - "; the guard (`scripts/git-hooks/pre-push` step e) is the check |

REV-N2 (a red test committed in `2ceb76df4`, self-corrected in `7c4bdf40b`) is a process note: the coder runs the touched test file before every commit.

Console pass 6 (after R14 to R20, backend without reload) added two more, both measured from the stored trace and `psql`:

| # | Observed | Cause | Rule |
|---|----------|-------|------|
| R21 | "which tap has cert" -> "which water tap has cert" (clarify) -> "more" answered "The register has no Certification certificates. Schemes on file: ..." | the head's `entity_op: reuse` re-used the previous turn's attachment entity, canonicalised to the AttachmentType NAME (`raw: "Certification"`); `_cert_scheme_from_raw` knows only cert / certificate / sijil as bare words, so "Certification" survived as the scheme | `_BARE_CERT_WORDS` covers every inflection the head or the parser can hand over: cert, certs, certificate, certificates, certification, certifications, sijil. A raw made only of those words is the bare leg. The reuse itself (a carry-less "more" re-asking the previous certificate question) is the head's existing behaviour, outside this lane |
| R22 | "which bathroom accessory has stock" answered "964 bathroom accessories have stock. Showing 4." with five ids sent | ACC-SRT9012's only stock row sits in warehouse SPARE/P, `is_active = false`; `_leg_stock` counts any on-hand row while the stock list tool filters `Stock.warehouse.has(Warehouse.is_active)` (inventory_service.py:743), so the header counted a product the answer could never show | `_leg_stock` mirrors the tool's visibility: the EXISTS joins `Warehouse` on `Stock.warehouse_id` with `Warehouse.is_active IS TRUE` (same company). The header then counts exactly the products the tool can render |

| R23 | after R21, the same carry-less "more" answered "I don't know 'more' as a product type. Try a product type such as tap, bathroom accessory, wash basin." | the head's reuse gave the turn a bare certificate leg; the remainder word "more" is not a phrase stopword, so it reached `filter_specs` as a set word | `_PHRASE_STOPWORDS` gains the paging words the lane itself recognises (more, next, lagi, please, show); the remainder is then empty and the reused question answers unscoped ("N products have certificates. Showing 5."), the honest reading of "more" once the set it referred to is gone |

Round 3 re-check (reviewer + security-reviewer, 11 Sep 2026): B1, N1, S1 (both halves), S2, S4, S5 and the certificate blocker are closed with kill tests. Adopted from the re-check:

| # | Finding | Rule |
|---|---------|------|
| R24 (both reviewers, should-fix) | `_access_level_codes` interpolates the tier token into a LIKE pattern unescaped: `['%']` selects seven of eight codes including `end_user`, a different tier | escape `\`, `%` and `_` in the token and pass `escape="\\"` explicitly; a token with a wildcard character matches only its literal spelling |
| R25 (security, should-fix) | `needs_tier_ask` fires only for a contact entitled to MORE than one tier; a single-tier contact (every plain End User) states no tier, so the resolve body sends `access_levels: []`, the promotion leg runs unrestricted and the header counts promotions the contact cannot see (count and product names, never content: SEC-B1 filters the rows at the tool) | `resolve_entity_body` sends the tier gate's recomposed names (`tier_gate_out["access_levels_recomposed"]`) whenever the tier gate ran and produced any; the parser tokens remain the fallback when it did not. A stated tier then translates exactly (`['Sorento Dealer'] -> {'dealer'}`), which also removes R18's brand-axis over-count |
| R26 (reviewer, nits) | the page arm's `fetch_rendered_result` guard has no test (disabling it leaves the file green); R19 also clears the carry on a CASUAL turn ("which tap has cert" -> "thanks" -> "more" stops paging) because the canned lane reaches `compile_current_state` with `gate_ran` False | one test for the page guard (a page turn whose fetch never reached the tool leaves the offset unchanged); the casual clear is ACCEPTED as behaviour and the rule now reads "any later turn that is not a page clears the carry" (AC-1343 amended). A reparent also changes the migration's `down_revision`, so the migration test asserts the chain (single head, parent exists) rather than a spelled parent id |

Owner test on the local stack (11 Sep 2026, PR #833 open) found a REGRESSION, fixed before merge:

| # | Observed | Cause | Rule |
|---|----------|-------|------|
| R27 | "check stock water closet with s trap 250mm" (worked before this lane through the forward spec path) now clarifies "I don't know 'water closet trap' as a product type"; same for "any incoming for water closet with p trap" | `check_stock` / `check_incoming` always derive a bare leg, so the turn takes the HAS path. There the deterministic reader binds `trap_type = s_trap` and `trap_length = 250` correctly (measured, `Understanding.bound_phrases = {"trap_type": ["s trap"]}`), but the remainder keeps the bound word "trap" and glues the remainder into ONE scope term "water closet trap", which the class vocabulary rejects. And even with the term fixed, only class / product_type / brand are membership filters, so the count would cover every water closet with stock, not the S-trap ones | (a) before the scope term is built, every word of every `bound_phrases` value is removed from the remainder (digits already are), so the term is "water closet" and binds class Water Closet. (b) a spec binding with a STRING value (trap_type, colour, finish, ...) is a membership filter of the described set alongside class / product_type / brand; numeric bindings (trap_length 250) stay ranking boosts inside the set, so the five shown are the 250 mm ones first. Header stays the class noun: "N water closets have stock. Showing 5." (c) the console cases gain both utterances |

| R28 | "which item has PPS cert" (console run 10) answered the forward ask "Please provide the attachment type for the requested product" | parser variant: ONE attachment_type entity, raw "PPS", `canonical_code: null`; the head's attachment normalisation dropped the entity (derived entities `[]`), so the lane saw no attachment raw, `derive_require` returned None, and the gate took its "no entities and product_attachment requires a scoping entity" arm | `derive_require`: intent `check_product_attachment` with NO attachment raw is still the bare certificate leg when `_CERTIFICATE_RE` matches `user_goal` or the message text (the same fallback R4 uses for a scheme-only raw); R14 then recovers "PPS" from the remainder into the scheme. `derive_predicate_words` adds the cert word found in the message so it leaves the remainder. Without a cert word the turn stays forward (None), unchanged |

| R29 | "any tap has PPS cert" answered the right set (srtwc7614) but listed all seven of its certificate files: WCM, SPAN, WEPLS, IKRAM beside the two PPS ones | the fetch calls `crm_master_product_attachments_list` with `product_ids` only; the tool returns every Certification attachment of those products. The scheme is a string column on the register (`certificates.scheme`), not an id, and the MCP forwards only catalogued parameters, so a `certificate_scheme` parameter would need a backend param, a catalogue line and an MCP redeploy; `certificate_ids` already exists end to end (`TOOL_REQUIRED_NARROWING_FILTERS`, product_attachments.py:173) | for a scheme-narrowed certificate leg the resolver emits the qualifying certificates' ids per product in the predicate block (`predicate.certificate_ids`, the active certificates of that scheme linked to the qualifying products, capped like the page ids), and the fetch passes `certificate_ids` alongside the page's `product_ids`, on the first answer and on every "more" page (the carry stores them). Only that scheme's files render. A bare certificate leg passes nothing extra (all certificate files, as today). `certificate_scheme` as a tool parameter is noted for the next MCP release, not built here (owner decision, 11 Sep) |
| R30 | the set answer for "check stock water closet with s trap 250mm" shows five correct products but no longer carries the forward path's Match line ("Match: trap type: S-trap, trap length: 250 mm, class: Water Closet"), which the owner needs for clarity | the Match line is rendered by `compile_state` from each shown row's `display.matched_specs` intersected with `spec_asked` (compile_state.py:1041), and only when every shown row is a spec row; the set path's candidates carry `matched_specs: []` on the `product_ids`-only arm and the HAS turn's `spec_asked` / shown-row bookkeeping does not line up with the tool rows | the set path renders the SAME Match line the forward path does, through the same renderer: candidates carry `matched_specs` from the ranker (string bindings and numeric boosts alike, class included), the HAS turn's `spec_asked` is the bindings read off the query, and the line names the set's bindings once for the whole answer when every shown product matches them. No new renderer, no new copy |

| R31 | after R30 the Match line still did not render live for "check stock sorento water closet with s trap 250mm" although the resolver returned `spec_asked` and `matched_specs` on all 106 rows (replayed) | measured on the stored turn: the tail stores `last_result_set` as the set_page carry DICT (`kind, domain, require, qualifying_ids, offset, ...`) on a set answer, and `_matched_on_line`'s `answered` check requires a non-empty LIST, so the renderer returns before it reads anything. Secondary: the brand and category tokens also matched 15 promotions each on the forward pass; the gate's ALLOWED matrix filters them by domain today, but the renderer's "every shown row is a spec row" check would count them if they ever passed | `_matched_on_line` treats a set_page carry dict with `qualifying_ids` as an answered set (the rendered products are its first page), and its shown set counts PRODUCT entities only. R2's product-only strip stays as is. Also noted: the looked_up trace payload for this turn was omitted (818 KB over the 32 KB cap) because 106 spec_search rows carry full specifications; not a defect, but the trace drawer cannot show the resolver output for large sets |
| R32 | the five shown for the same turn were SRTWC192, 193, 200-S-150, 201, 202 (S-trap, none 250 mm) while SRTWC286-SH (trap_length 250 matched) sat seventh | the require-only arm emits qualifying products ordered by code; numeric bindings are boosts but nothing sorted by them | the require-only arm orders candidates by the number of matched bindings (descending), then code, so products matching the numeric binding too come first; the ranked arm keeps the ranker's score order. Header unchanged |

| R33 (reviewer round 4, should-fix) | R28's fallback reads `_CERTIFICATE_RE` (`cert|certificate`, substring): "certainly, send me the drawing for the basin", "concert hall basin photo" minted a certificate leg where the turn stayed forward before (the negated "no cert needed" case is a real cert word and stays out of scope) | substring match at the first call site that turns the regex into a LEG (a different answer, not a team split) | the R28 fallback and `derive_predicate_words` use a word-anchored pattern over the bare cert inflections (cert, certs, certificate, certificates, certification, certifications, sijil); the predicate word appended from the message is the bare word without punctuation. `derive_routing`'s own regex is untouched (team split only) |

Reviewer notes accepted without code: (a) R27(b) makes string bindings AND filters, which undercounts when a product's value for that key was never derived (the original `_MEMBERSHIP_KEYS` comment's argument); accepted because the header states what was counted and a described set that ignores the customer's spec word was the worse failure measured live. (b) `certificate_ids` is capped at 200 by UUID sort, inert on this data (largest scheme: 38 distinct active certificates).

| R34 | "any tap has PPS cert" (parser: category "tap" + attachment_type "PPS cert"; the head dropped BOTH entities) answered "Please provide the attachment type for the requested product" although the resolve ran with `require {certificate: {scheme: PPS}}`, qualifying 1, `certificate_ids` filled | the answer lane's product_attachment arm asks for the attachment type whenever the turn carries no attachment_type entity; it never consulted the predicate | with a predicate whose require carries the `certificate` leg (any qualifying count), the attachment-type ask never fires: the leg IS the document type. Zero qualifying takes the AC-1319 miss copy, above zero the set answer. Same present-predicate bypass principle as AC-1326 |

Parser-side events seen in the same round, outside this lane: an OpenAI 429 rate limit failed one parser call (turn status failed, the console then showed the previous turn's portal link); "any tap has PPS cert" was labelled `get_portal_link` by the parser on one run of three (user_goal still said "check whether any tap has a PPS certificate") and answered with the portal link; the other two runs answered "1 tap has PPS certificates." Recorded here, not fixed in code (AC-1324).

Also verified live in pass 6: the promotion pick turn armed the set_page carry and the "more" page's tool args carried `access_levels: ["Sorento Dealer", "Mocha Dealer", "Cabana Dealer"]` (SEC-B1 / AC-1333). Promotion sets page by PRODUCT, so a promotion file attached to several products can appear on two pages; accepted, the promotion render is the existing one.

## Leg semantics

| key | payload | predicate (EXISTS on Product.id) |
|-----|---------|----------------------------------|
| `certificate` | `true` or `{scheme}` | `certificate_products` JOIN `certificates` (scoped, status active); scheme through `certificate_scheme` lookup set then equality on the normalised value; validity NOT filtered by default (D6) |
| `stock` | `true` | `stock.quantity_on_hand > 0` (unchanged) |
| `attachment_type` | customer label | exact code / type_name, else `attachment_type_alias` set; EXISTS `product_attachments` JOIN `attachments` on the resolved type |
| `promotion` | `true` | active promotion inside its window (unchanged) |
| `incoming` | `true` | `inbound_shipment_lines` L JOIN `inbound_shipments` S (scoped): shipped minus received > 0 AND S.actual_arrival_date IS NULL |

Multiple keys AND. Unknown key stays a 422.

## Phases (PRINCIPLES.md order)

- Phase 1 (frontend-first mock): none. No UI surface changes; the reply is the existing WhatsApp
  block. The lavish page stands as the journey artefact.
- Phase 2 (test-first): `tester` writes red pytest for AC-1301 to 1324 in the files named above,
  then `coder` (one instance, whole lane) makes them green in the order A, B, C, D, E, F.
  Migration D3 re-parented on main's head at PR time (`scripts/alembic-reparent.sh`).
- Phase 3: `reviewer` + `security-reviewer` (company scope on every leg, the lookup-set read
  path, no raw SQL) + console verification (AC-1325) in parallel; `guide-writer` adds "ask for a
  set" examples to the chatbot user guide; DoD gate; one PR.

## Risks and open questions

- Predicate-word subtraction relies on the parser tagging the predicate word as an
  attachment_type entity (cert, photo) or the intent carrying it (stock, incoming, promo). A
  turn where the parser tags nothing and the intent is null gets no `require` and behaves as
  today, which is the safe failure.
- `derive_search_inputs` on a whole sentence returns the sentence as one free term. "any",
  "got", "has" are already stopwords; question words are not (A2 closes that). Predicate
  words (cert, stock, photo, incoming, promo and their Malay / Chinese forms the parser
  tagged) are removed by C2 from `predicate_words`, never by a hard-coded list in the
  resolver.
- "more" carry: the qualifying id list can be 2,704 long. Carry the ids, not the query, capped
  at 200 with the true count in the header; beyond 200 the "more" says to narrow.
- Scheme options are owner-entered data, so on a fresh install "PPS" resolves only once the
  owner has typed it into the set. Until then every scheme word clarifies and lists
  `schemes_on_file` from the register, which is still a correct answer.
- The parser version label in the console reads v6 while the lane plan for broaden-domain
  names v7; irrelevant here since the prompt is untouched, noted so nobody "fixes" it in this
  lane.
- Test-fixture friction, not a lane defect (tester, fix round 2): `resolve_classes_for_term` resolves a
  bare class word ("basin") only through a category's `search_synonyms`, which are seeded only when
  the category code follows the real `<BRAND>-<SUFFIX>` convention (e.g. `ZZT-WB`); a generic test
  category never gets them. Lane tests seed categories with that convention. Worth a note in
  LESSONS-LEARNT when the lane merges.
- 220 cert-covered products have no spec row; they are reachable only through LOOKUP ids or a
  bare "anything with cert". Out of scope; belongs to spec derivation coverage.

## Revive round, 26 Sep 2026

Main's turn engine re-architecture (#952) had already ported most of this lane's code
(`predicate.derive_require`, the resolver's require branch, the five legs, the set header,
paging through `focus.set_page`) and retired `head/route.py` and `tail/compile_state.py`,
which carried the lane's old "more" carry and Match line. The merge takes main's version of
every app file; the lane keeps migration 511, its tests, the console cases and these docs.

Owner, 26 Sep 2026: "I want to push for this reverse asking feature ... which water tap got
stock which water tap got certificate which water basin blah blah blah got stock or got
incoming all this reverse asking need to be able to cater to that".

- R35 (repair): main's AC-1703 did-you-mean guard read a class word's substring matches
  ("bidet" in `ACC-BIDET`) as a mistyped code and silenced AC-1319's zero-qualifying answer.
  The guard counts only code-shaped tokens now.
- R36 (repair): under the v3 verdict a cert PHRASE in `requested_attributes` ("PPS cert") fell
  to the bare leg and lost its scheme; it now splits like an attachment_type raw.
- R37 (owner): "water tap" is a Tap and "water basin" a Wash Basin (`CLASS_SYNONYMS`, and
  migration 511 appends both to the categories already on file). AC-1301's "phrase that names
  no set" example moves to "water tub"; the console clarify case to "aqua tap".
- R38 (dealer check): the stock leg counts only the locations the asking contact's stock
  visibility policy allows (the same `warehouse_criterion` the stock tool uses, stock ask v2
  R3), and an availability row (no fields, by design) prints its product code instead of a
  bare "1. ". No quantity reaches an availability-only contact.
- R39 (owner ruling, 26 Sep 2026 01:55Z): "drop paging. No 'Showing 5', no more / next /
  lagi carry. A counted set that fits one WhatsApp message (about 50 rows) is listed in full
  under its count header; a longer one states the count and asks how many to show, or offers a
  narrower filter. Apply to every leg." Built as `answer.SET_LIST_MAX = 50`; a longer set lists
  nothing and asks; the answer to that question (the parser's own `top_n`, no new subject) lists
  that many from the start of the set, recounted under the same tier and stock policy, and
  clears the carry. A count in the ask itself works the same way. `continuation` pages nothing.
  A recount that finds nothing refuses the tool call rather than sending it with no product
  filter.

## Review fix round, 26 Sep 2026 (reviewer pass on f2375f402)

Every fix red first, then green, then kill-tested; tests in
`tests/chatbot/test_counted_set_review_fixes.py`.

- B1: a cert PROPERTY word ("valid", "validity", "expiry", "no", "number", ...) is removed with
  the bare cert words before the remainder is read as a scheme (`predicate._CERT_PROPERTY_WORDS`),
  so "valid cert" is the bare leg, never scheme "valid".
- B2: a described set that qualifies nothing never fetches (`lanes/business.run_fetch` refuses
  it as `not_found`), and the miss lane offers no did-you-mean over a class word's own code
  matches, so the set's own sentence reaches the customer: "The register has no zzq
  certificates. Schemes on file: ..." (AC-1321) and "Couldn't find a tap with a certificate"
  (AC-1319).
- B3: a listed set asks each leg's tool for its maximum rows (`fetch.SET_ROW_LIMIT`), and the
  header counts the products the rows actually show (R8/AC-1330 restored), saying "Here are
  the first N." when a tool still cut the list.
- B4: the recount's stock visibility (K4) and the availability row's product code (K5) each
  have a guarding test.
- S1: while "how many should I show?" is open, a message that is only a count is that count,
  whatever the parser made of it (`turn_runtime.with_set_count_from_text`). The live-parser
  console pass still runs locally (the cloud lane has no parser key).
- S2: the question offers only what is wired: "How many should I show (up to 50)? Or ask
  again naming a brand or size." A full re-ask ("which sorento tap has cert") already answers
  the narrower set (AC-1327); a bare "grohe" reply is not carried against the set. Trigger to
  wire a bare narrowing reply: the owner asks for it, with the carry gaining the brand and the
  recount a brand filter.
- S3: the UAC marks AC-1316, AC-1317, AC-1333, AC-1337, AC-1347, AC-1350, AC-1354 retired or
  amended under R39, AC-1355/AC-1356 retired with main's Match line, AC-1319/AC-1321 amended
  for B1/B2, and adds AC-1316c, AC-1317c, AC-1317d.
- N1 documented in 511's docstring; N2 one `contracts.named_count` for the engine, apply and
  fetch; N3 comment restored; N4 stale "more" wording removed; N5 rewrapped; N6 the dead
  `spec_asked` write removed.

## Owner hand test round 2, 26 Sep 2026 (contact 900000039, W1 to W6)

Turns 5, 9, 10 of the owner's run: "whici sorento wash basin has stock" answered "2,306
products" (every Sorento product), "which sorento wall hung basin has stock?" answered "144
products", and the "10" after it answered from the earlier 334 set. Every fix red first,
then green, then kill-tested; tests in `tests/chatbot/test_attribute_asks_round2.py`.

- W1 (AC-1360): `resolve_product_set` takes every brands-table name out of the parser's class
  word and scopes the set by `Product.brand_id`; a term whose tail is a class and whose head
  binds a spec ("wall hung basin") is split into the class and the spec. A head that binds
  nothing ("water tap" with no synonym) stays whole, so AC-1301/AC-1320's clarify holds.
- W2 (AC-1361): the header leads with what was identified, labelled from the spec registry
  (`describe_set`): "Brand: Sorento, Product type: Wash basin, Mounting: Wall hung. 2 wash
  basins have stock." A word the set reader could not use is said, never dropped.
- W3 (AC-1362): a set row is one line led by the product name and its top two spec values by
  registry `rank_weight` (`row_labels`), then the code, then the tool's fields.
- W4 (AC-1363): the resolver's predicate carries its exact description (`set_key`: specs,
  brand, LOOKUP ids); the carry keeps it with the count asked over and how many were shown;
  a page replays it. "another N" / "N more" after a list continues ("Here are 11 to 50."); a
  bare number after a listed page is still a row pick; a count that moved is said ("It was 5
  when you asked."). A page turn runs no resolver.
- W5 (AC-1364): `brands.is_chatbot_default` (migration `bcd_0001_brand_chatbot_default`,
  seeded to Sorento, one per company) is edited on Master Data > Brands (record page Edit,
  and the create/edit dialog). Chosen over Product Specifications because the preference is a
  property of a brand, and the Brands master already carries the per-brand knobs
  (`access_levels`, `flows_to_purchasing`); the spec registry is keyed by spec key and has no
  per-brand row. No brand named: the default brand's set first, "Brand: Sorento (default)",
  plus "Other brands: Mocha 300, Cabana 121, name one to see them."; a default the set does
  not reach leaves the set whole. A single default rather than per-brand weights: one
  preference, one column (PRINCIPLES "simplest thing"); trigger for weights is the owner
  asking to rank a second brand above the rest. SUPERSEDED 27 Sep 2026 by round 4 R1: the
  owner asked for weights, so the trigger fired and the switch is replaced.
- W6 (AC-1365): `resolve_classes_for_term` also reads the shipped `CLASS_SYNONYMS` for a class
  the catalogue has, so "water basin" is Wash Basin on a database that never got 511's
  synonym; a section header names at most five subjects, past that it counts them.

## Owner hand test of round 2, 26 Sep 2026 13:07Z (round 3, W1 to W6)

The owner's run on console :3084 (contact 900000039), verbatim on the PR: "the message too
long already, i prefer it to be line by line ... it needs to be vertical, don't use |, and
why when i say 10, it gives some other answer, and ... when i ask which basin has cert, it
gives weird answer, what does sorento (default) mean". Tests in
`tests/chatbot/test_attribute_asks_round3.py`, each red first, then kill-tested.

- W1 (AC-1366): `row_labels` returns `{name, specs[]}` per code; `fetch._set_item_block`
  renders the vertical block. The attachments presenter's unkeyed "Product Code" is read as
  the code. The tool's intro is dropped under a set header.
- W2 (AC-1367): `turn_runtime.with_set_count_from_text` reads a bare count after a listed
  page as a continuation; `page_the_set` keeps the carry after the last page and marks it
  `exhausted`, and the runner answers "That is all N." without a tool call. Supersedes
  round 2's "a bare number after a listed page is a row pick" (a set answer mints no roster).
- W3 (AC-1368): `turn_runtime.with_new_set_words` drops earlier-turn set entities when the
  message names a class word. Live cause of turn 4, reproduced: the parser handed back the
  listed water closet codes, and the turn asked for the attachment type of a water closet.
- W4 (AC-1369): `describe_set` loses "(default)"; `answer.other_brands_line` closes the
  reply.
- W5 (AC-1370): no code change. The brand scope is `Product.brand_id` joined to `brands`;
  the GB glass basins are Sorento's in the catalogue sample
  (`tests/fixtures/spec_derivation_golden_sample.json`: all 17 `GB*` codes under `SRT-WB`
  carry `brand_name` SORENTO). Pinned by tests that list a Mocha GB-coded basin beside them.
- W6: console bold/underline rendering is #1279's `WhatsAppText`; nothing duplicated here.

## Owner console test of round 3, 27 Sep 2026 00:03 to 00:07 MYT (round 4, R1 to R7)

Console :3084, head 1683cb2f1, contact 900000039. The owner, verbatim on the PR: "i want
weights as brand preference instead of switch, ... Brand, product type needs to be line by
line, label needs to be bold, ... bruh i said 10 but it come out so many, then the ask about
gunmetal wash basin means what ah, so it got match or not? ... i clarify if it is tap or wash
basin and i said tap, you supposed to do the searching ... the water closet t trap ask, why it
match s trap? and why all the values are snake case? too technical, i don't want".

Owner rulings, 27 Sep 2026 (PR #833 comment "Owner console test of round 3"):

- R1 (owner ruling, 27 Sep 2026): brand preference is a weight per brand, not a single
  default switch; the header brand and the "Other brands" line follow the weights.
- R2 (owner ruling, 27 Sep 2026): the set header is line by line, one filter per line,
  labels bold.
- R3 (owner ruling, 27 Sep 2026): a count answer is compact; one product is at most 2 lines
  (name with code, then the one or two facts the ask was about); details on request.
- R4 (owner ruling, 27 Sep 2026): a miss says what was searched and the count in other
  values, before any escalation offer.
- R5 (owner ruling, 27 Sep 2026): a clarify answer re-runs the original ask as a counted
  set; never a code search.
- R6 (owner ruling, 27 Sep 2026): an unknown attribute value is said back with the known
  values, never silently matched to another value.
- R7 (owner ruling, 27 Sep 2026): every value shown to a customer is in plain words, never
  snake_case.

The eight exchanges are the fixture `tests/chatbot/fixtures/owner_console_2026_09_27.json`,
replayed through `engine.run_turn` in `tests/chatbot/test_attribute_asks_round4.py` (real
resolver, class vocabulary, spec registry and brands table, spec fallback on as in
production; parser and MCP stubbed). No stored trace came with the comment, so the parser
reading of each turn is inferred from the reply the owner saw; the fixture says so.

- R1 (AC-1371): `brands.chatbot_weight` NUMERIC(6,2), default 0, replaces
  `brands.is_chatbot_default` (migration `bcw_0001_brand_chatbot_weight` on `bcd_0001`: the
  company's default brand becomes 1.5, else its Sorento rows, then the switch column is
  dropped; downgrade restores the switch on the top weight). 1.5 is the owner's own example
  value and the `value_weights` brand preference. `resolve_product_set(prefer_weighted_brand)`
  ranks the brands the set reaches by weight, then count: the first with a weight above 0
  scopes the set, the rest are the "Other brands" line in that order; none weighted leaves
  the set whole. Master Data > Brands: a "Chatbot weight" list column, a "Chatbot brand
  weight" number on the record page and in the dialog; `BrandService` loses the
  one-default-per-company clear. Two knobs now rank brands (this weight for the counted set,
  `product_spec_registry.value_weights` inside the spec ranker); trigger to fold them into
  one: the owner tuning one and expecting the other to move.
- R2 (AC-1372): `answer.describe_set_line` returns one `*Label:* value` line per binding,
  and the count sentence is its own line under them. `_split_class_tail` also splits a
  class HEAD from a spec tail ("water closet p trap"), the cause of exchange 3's missing
  Product type line. The bold is WhatsApp markup, the contract #1279's console renderer
  reads (#1279 still open; nothing of its component is used here).
- R3 (AC-1373): `fetch.set_rows_text` renders one row per product: "N. name (code)", then
  the facts per leg (`_SET_ROW_FACTS`: stock Total; certificate number and valid until;
  incoming quantity and arrival date; attachment type), a count when the tool returned
  several rows for one product, flags inline. `row_labels` is the name only now. Fifty such
  rows stay under WhatsApp's 4,096 characters (measured 3,641 with 34-character names).
- R4 (AC-1374): a zero set with a class and exactly one property value is recounted
  without that value, grouped by the key's other values (`_near_miss`, same legs, brand and
  membership clause through the new `product_spec_search.membership_clause`), and
  `answer.near_miss_sentence` says it. Trigger to widen to two property keys: an owner turn
  that names two.
- R5 (AC-1375): `focus.set_clarify` keeps `{term, options, ask}` when the turn asks the
  product-type clarify or the R6 question (`turn_runtime.set_clarify_carry`, armed after the
  resolver); the next message that is one of the options is that ask again with the word
  replaced (`turn_runtime.with_clarify_answer`, before the verdict reaches APPLY). Every
  other turn clears it (`turn/apply.py`).
- R6 (AC-1376): `product_spec_search.unknown_spec_values` reads each enum key's head words
  off the registry (a word ending the synonyms of two or more of its values, "trap"); a
  "<word> <head>" that is no synonym, whose modifier is no known word, number or stopword,
  is an unknown value. The resolver answers it before any count (set path,
  `predicate.unknown_values`) or any spec search (product path, `unknown_spec_values`), and
  the miss copy says "I don't know 't trap' as a trap. I know P trap and S trap."
- R7 (AC-1377): `product_spec_registry.display_spec_value` (registry `value_labels`, else a
  slug in sentence case with acronyms in capitals, never a "_") is used by the set header,
  the near miss, `spec_list_for_products` (new `display_value`, read by the MCP products
  presenter) and the chatbot's own Specs line as the last guard for an older MCP. The scan
  found one more leak and it is fixed: the miss copy printed the domain key ("But no
  master_products matched these"), now "master products". Every reply in the round 4 tests
  is scanned for snake_case.

## Reviewer pass at d6fa2b31, 26 Sep 2026 (round 5, B1, S1 to S3, N1 to N4)

- B1 (AC-1376 amended): `unknown_spec_values` now needs two more things before it says a
  value back. The modifier sits in a VALUE position (`_in_value_position`): a single letter
  where the key's own modifiers before that head word are single letters ("t" beside "p" and
  "s" before "trap"), or a one-edit slip of a known modifier of three letters or more ("wll"
  for "wall"). And no active product carries the phrase in its name or description
  (`_names_a_product`, one indexed LIKE per candidate, only reached after the shape check).
  "deck mounted", "long spout", "ceiling mounted", "ceiling mount", "grease trap" and "click
  clack waste" are searched again. The short circuit stays for a phrase that passes both: the
  owner's exchange 8 answers only the known values.
- S1 (AC-1374 amended): `_near_miss` expands a LIST value with `jsonb_array_elements_text`
  (a scalar string is a list of one), groups by each element, and counts `other_total` as
  distinct product families, so a dual-finish product is listed under both finishes and
  counted once.
- S2: `tag_data_service._spec_display_value` is `display_spec_value(..., title_case=True)`;
  its own copy and acronym list are gone.
- S3: both brand migration test files carry `pytestmark = pytest.mark.serial_ddl`.
- N1: a list value's `display_value` is its values through `display_spec_value`, joined " / ".
- N2: `near_miss_sentence` lower-cases the value only when it is not all capitals ("No PVC").
- N3: chatbot weight is bounded 0 to 9999 on the dialog (zod `.max(9999)`, input `min`/`max`,
  `noValidate` so the zod message shows) and on the record page (input `max`, Save disabled).
- N4: MCP presenter test for `display_value`.

## Reviewer pass at 34cb4697, 26 Sep 2026 (round 6, B1-r5, N-r5-1 to N-r5-3)

- B1-r5 (AC-1376 amended): `_names_a_product` matches the phrase as whole words
  (`~* '\m<phrase>\M'` on name and description), not as a substring. "Basket Trap", "Outlet
  trap" and "Bucket trap" no longer name "t trap", so the owner's exchange 8 stays exact
  whatever else the catalogue holds; a product called "... T Trap ..." still does.
- N-r5-1: the brand record page shows "Enter 0 or more" / "Enter 9999 or less" under the weight
  input, the dialog's own words, beside the disabled Save.
- N-r5-2 (AC-1374 amended): the near miss lower-cases a value word by word and keeps an all-caps
  word or a `SPEC_ACRONYMS` word ("No PVC pipe wash basins", "No LED white ...").
- N-r5-3: the console yaml sends all six reviewer phrases and each refuses any "I don't know".

## Owner hand test of rounds 4 to 6, 27 Sep 2026 10:37 to 10:45 MYT (round 7, items 1 to 7)

The asks come from a salesperson (Leena) who types a product code, a descriptor and a field.
Each item was replayed with her exact message against seeded data, red first.

- F1, item 1 (AC-1378): every word that names the leg or selected its domain (the policy row's
  own `switch_words`: "eta", "arriving", "shipment", "container" for incoming) is a predicate
  word, so "65502 eta" never says it did not understand "eta" (a message made only of the
  ask's own words, a bare "promo", is left as before: it names no set); the ask's own word is never
  projected as a missing field ("*Incoming:* not recorded yet"). An incoming set renders each
  row in the normal ETA reply's field structure, with the packing lists (AC-1373 amended).
- F2 and F3, items 2 to 4 (AC-1379, AC-1380): `app/services/product_code_family.py`, called by
  `resolve_reference_post` before resolving. A token that starts with a letters-then-digits
  word of the message is a code token; its family is every active code starting with that word
  or with an X after its letters (SRTWC7604 and SRTWCX7604), not followed by another digit. The
  other words of the message pick variants by two-word phrase, then single words of three
  letters or more, over code, name, description and spec values; a word no variant carries is
  ignored, and when no variant carries all of them the ones carrying the most are offered. The
  code token becomes the picked codes (resolved exactly, so no set ever opens) and a
  category/product type/spec token whose words were read as the descriptor is dropped. A pick
  that selects every variant, or nothing, leaves the request byte-identical.
- F4, items 5 and 7 (AC-1381): `describe_set` says what the thing IS on one line: the
  registry's `product_type` value leads and the class noun follows when the value lacks it
  ("*Product type:* Close coupled water closet", "*Product type:* Kitchen tap"), never a second
  "*Type:*" line. A scope term the set read (not in `unrecognized_terms`) leaves
  `unresolved_tokens`, so "I could not find close couple wc." is never said.
- F5, item 6 (AC-1382): a set answer that closes with "Other brands ... Name one to see them."
  carries the set (`set_page_carry(offer_only=True)`, the offered brands and counts on the
  key). A message that is only one of those brand names (`with_brand_from_offer`) recounts the
  SAME set scoped to that brand (`turn/apply.py`, `set_brand_chosen`), so the count agrees with
  the offer. The page runs off the carry, so the previous answer's rows no longer feed the
  zero-stock ladder on that turn (`engine.py`, `focus_products=None` on a set page turn).
- F6, item 7 (AC-1383): the bare certificate leg surfaces its certificate ids, as the scheme
  form already did, so the attachments call narrows to certificate files; a certificate or
  attachment set renders each row in the normal attachment structure (AC-1373 amended).
- F7 (AC-1384): no reply carries internal phrasing; pinned over every reply of the replay.
- Superseded tests amended to these rulings (a note in each): round 3 W1 and W5 and round 4
  R3 (two-line certificate and incoming rows), `test_lane_require` (two certificate set
  tests, and the bare leg's "no certificate ids" control, now its own ids), and
  `test_resolve_predicate` (the predicate block's keys; the bare leg's certificate ids).

Parser prompt: not changed. No migration.

## Owner retest of round 7, 27 Sep 2026 12:59 to 13:04 MYT (round 8, F1 and F2)

- F1 (AC-1385, AC-1386, AC-1388): `specification` entity kind (contracts, policy row, migration
  `spk_0001_specification_kind`), `SPECIFICATION_ADDENDUM`, and one Specification line per
  registry key in the policy blocks (`chatbot_parser_prompt.specification_lines`, through the
  registry's own readers). `app/services/chatbot/head/grounding.py` runs after every parse:
  class and type words stay the category, registry choices and measurements become
  `specification` entities, a word beside a key's own word that is none of its choices is an
  unknown value said back with the known ones, `attachment_type` survives only for a word on the
  attachment-type list, a document domain chosen only for a misfiled descriptor becomes the
  product domain. Grounded values reach the resolver as `extracted_specs` (a grounded number
  defines membership), unknowns as `unknown_values`; a follow-up keeps them.
- F2 (AC-1387): `answer.build_set_header` is one intro line (the tool's intro with "the
  requested products" replaced by the described set and its count); the set's rows are the
  product-code rows through `fetch._item_line`; `set_rows_text` and the bold header lines are
  gone. Price asks about a set get a `price` leg (no filter).
- Latent defect fixed: `_near_miss`'s empty-list fallback was the JSON string "[]".

## Owner hand test of 28 Sep 2026 11:27 to 11:32 MYT (round 9, one reply structure)

Owner rulings (reply alignment page, round 3, and chat): "you do like here is what you want, but no
incoming, do you want me to escalate..., I want that structure to stay, i don't want so many route";
"need to be more line by line, more structured"; "C: no cap, but no explanations and no repeats";
"always break it down"; "I want us to reuse the functions that we have"; "i don't want new layer".

- AC-1389: a set that qualifies nothing is `answer.what_you_want_reply` (the product-code miss's own
  layout) over `predicate.members` (`product_predicate_service.resolve_product_set`, the set without
  its legs). `near_miss_sentence` is no longer called.
- AC-1390: an unknown value is the same layout; the subject is broken down by the value's key
  (`references._unknown_value_members`, `resolve_product_set(breakdown_key=...)`).
  `unknown_values_sentence` is no longer called. An unread word is the "Couldn't find" line.
- AC-1391: no silent brand default (`prefer_weighted_brand=False` at the resolve endpoint).
- AC-1392: too many is the count plus `_set_breakdown` by the next attribute, never a paging question.
- AC-1393: a document word alone ("cert?") keeps the subject (`turn_runtime.with_carried_entities`).
- AC-1394: a product ask naming a property counts the described set on the existing `price` leg
  (`{"price": "described"}`), so a measurement filters it.
- AC-1395: a category word alone under the document domain is a product ask, never the "Which kind
  of file" menu (`head/grounding._category_word_under_documents`).
- Parser: `SPECIFICATION_ADDENDUM` says a colour word on its own, misspelt or not on the list, is a
  finish specification; published unlabelled by `spk_0002_colour_word_spec`.

## Owner retest of round 10, 28 Sep 2026 19:4x to 20:0x MYT (round 12, attribute-first by structure)

Owner ruling: "what? i thought we are applying a general fix? why the fix that we applied for
incoming cannot work for stock? this is too fragile, that means our solution is not right".

Trace of `any gunmetal basin have stock` (reproduced in the harness with gunmetal wash basins in
stock): the lane owned the turn. `derive_require` gave `{"stock": true}` off `check_stock`, the
resolver counted 2, and `fetch.output_structurer` printed `answer.build_set_header`, which opened a
qualifying set with the leg's tool sentence (`_SET_INTRO_BY_LEG["stock"]`, "Stock summary for
..."). The incoming ask qualified 0 and went to `what_you_want_reply` ("Here's what you want:").
So the shape followed the count, and the leg was a fixed word table read on the intent alone: a
product ask requesting "in stock" or "arriving" had no leg, and one requesting "stock" got product
rows under a stock header, because the tool follows the plan's domain.

- AC-1396: `predicate._leg_in_words` reads a leg through any registry word of a requested attribute
  (only on a product ask or the leg's own ask); `derive_require` also reads the routed domain;
  `predicate.with_set_leg` sets the verdict's domain and intent from the leg in `engine.run_turn`
  after grounding, before APPLY.
- AC-1397: `build_set_header` opens every set with `SET_OPENER` ("Here's what you want:") and the
  leg in the phrase; `_SET_INTRO_BY_LEG` and the tool intro argument are gone.
- AC-1398: `tests/chatbot/test_attribute_asks_round12.py`.

## Definition of done

Every AC in the UAC passes with evidence in `attribute-first-asks-test-report.md`; AC-1325 traces
recorded; PR carries the console screenshots for the two original utterances; plan Status set
to implemented and the triple moved to `documentation/plans/_archive/chatbot/`.

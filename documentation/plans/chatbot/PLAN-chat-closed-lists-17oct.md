# PLAN: CHAT-CLOSED-LISTS - chatbot step 2, closed candidate lists

Status: Plan (discovery done 17 Oct; behaviour card out to crew). Track: full (size L, LEAD pattern).
Branch `claude/chat-closed-lists-x6ymb5` (the sandbox cannot push `crew/chat-closed-lists`), PR #1755.

Design source: CHATBOT-SCALE-DESIGN-16oct section 8 step 2 + D2 (crew-only): code only SHORTLISTS
candidates (exact code, name and spec search, near spellings); the parser CHOOSES from them or says
"none", and then we ask. Code never decides meaning. Card: `chat-closed-lists-behaviour-card.md`.

## Owner CP0 notes this lane answers

| card | owner note | root cause (discovery below) |
|---|---|---|
| c04 / c19 | "do we need to pass certification type as index like spec or attachment type" | cert kinds and schemes never reach the parser as a list; regexes read them (F3) |
| c06 | "Why we cant identify WC ya" (asked "WC (product) or WC (attachment type)?") | resolver probes every token as every kind and the kind pick reads hit counts, not the parser's kind (F4) |
| c07 / c27 | "This should be spec, why we cant find" / "We should have understood this better" | free words matched exactly downstream; re-grounding rules read the message (F1, F5) |

## Discovery (file:line, `sorento_crm_backend/app/` unless stated)

**F1 Spec vocabulary is already closed, and unbounded.** `{{specs}}` (`services/chatbot_prompt_vars.py:368-372`)
renders `chatbot_parser_prompt.specification_lines` (`services/chatbot_parser_prompt.py:1268-1328`): one line per
active registry key (skips class, brand, product_type, :1265), every choice with its synonyms, no cap (50 seed keys,
`product_spec_registry.py:472-1260`). Grounding then re-checks the parser's choice: `_bound` (`chatbot/head/grounding.py:598`,
exact key, exact phrase or lower/`_`->space value), `_validate` (:637), `_as_typed` (:655), `ground_words` (:293,
re-reads the MESSAGE for registry phrases, numbers beside key words, unknown words beside key names), the class split
(:812-865). A value off the registry becomes `spec_value=None` + `spec_known` (:650) or unknown `Grounded("", "", raw)`
(:792), sent on as `unknown_values` (`chatbot/lanes/business/resolve_gate.py:752-763`) and said back by
`answer.unknown_values_sentence` (`chatbot/lanes/business/answer.py:3065`).

**F2 Attachment types are not a closed list.** No synonyms column (`models/resources.py:40-112`; `description` is used
as one). The prompt teaches a fixed taxonomy in code (photo, video, technical drawing, 3D model, certificate:
`chatbot_parser_prompt.py:684` rows, `SPECIFICATION_ADDENDUM` :525-537). Code then maps words to rows through
`grounding.PROMPT_DOCUMENT_KINDS` (:234), `DOCUMENT_TYPE_CODES` (:417), `_listed_document_type` (:422, drop at :866-873),
the resolver's exact + prefix/substring/ILIKE-on-description probes (`services/entity_resolver.py:1984, 2665`), the
predicate's code/alias/description chain (`services/product_predicate_service.py:127, 113, 140, 238`), the reference API's
catalog/catalogue swap (`api/v1/system/references.py:84, 96-99`), then `turn_runtime.answer_parse_output` renames the
entity to the row (`services/turn_runtime.py:5036`). There is no `attachment_types` row in the parser input.

**F3 Certification types.** No table of their own: a cert kind is an `attachment_types` row with `is_certificate`
(`models/resources.py:75`). Register: `certificates` (`models/certificate.py:58`, free `scheme` String(60), body,
number), `certificate_revisions` (:193), `certificate_products` (:279). Scheme vocabulary = active
`Certificate.scheme` values (`product_predicate_service.py:278 _schemes_on_file`) + lookup set `certificate_scheme`
(migration 511 :39). The chatbot reads cert words by regex: `lanes/business/predicate.py` `_CERT_RE` :20,
`_CERTIFICATE_RE` :21, `_BARE_CERT_WORDS` :44, `_BARE_CERT_WORD_RE` :57, `_CERT_PROPERTY_WORDS` :65,
`_LEG_BY_ATTRIBUTE_WORD` :115, `_cert_scheme_from_raw` :74, `derive_require` :210; `recover_certificate_scheme`
(`product_predicate_service.py:287`); a copy of the regex at `api/v1/system/references.py:1761`.

**F4 The cross-domain menu.** `turn/reconcile.py:82 apply_reconciliation` builds "`<raw> (<kind>)`" options whenever
the resolver hits one token under two kinds (:126-148); armed as a `kind_pick` at `turn/apply.py:1804-1824`. The resolver
probes every token as every allowed kind (`resolve_gate.py:582 _allowed_entity_types`, `fallback_to_all_types` :705),
and the hint lock (`resolve_gate.py:533 HINT_LOCKED_KINDS`) strips only `_NAME_MATCHED_KINDS`
(`references.py:1943-1945`), which lacks `attachment_type`; an unconfident hint is not locked at all (:552).

**F5 Rewrites and word rules.** 26 functions rewrite or re-read parser output (grounding x14, reconcile, resolve_gate x2,
predicate, find, turn_runtime x5, engine x2); about 61 hardcoded word rules, about 20 on the spec / product /
document path (list in the discovery comment on PR #1755). The ones this lane owns: everything in F2, F3 and the
spec half of F1.

**F6 Prompt as code.** Prompt text = `chatbot_parser_prompt.py` constants + `alembic/data/*.txt` addenda; a version is
published unlabelled (`scripts/publish_parser_prompt.py`, `chatbot_prompt_vars.publish_wording_edit` :748); labels are
only `production` / `staging` (`services/ai_prompt_service.py:23`); a run pins a version with the dry-run-only
`prompt_overrides` envelope key (`chatbot/engine.py:1304-1326`). CHAT-STATE (#1747) gates its path on the raw template
carrying its section, so production never opts in; this lane uses the same gate idea with its own marker.

**F7 Bench.** `scripts/chatbot_eval/replay.py` (`run --stage grounding` / whole turn, `diff --intended`); no trace fixtures
are committed, so the lane's L1 evidence is the CI-safe synthetic replay tests plus crew's `replay-gate.sh` run on the
lane head (crew runs it; the lane posts the intended-diff file).

## Shape (pending the card answers)

1. **Lists the parser picks from**, rendered by code from the tables, never written by hand:
   - `{{attachment_types}}`: one line per product-facing `attachment_types` row: code, name, description words.
   - `{{certification_types}}`: the cert kinds (`is_certificate` rows) and the schemes on file (+ the lookup set).
   - Specs: `{{specs}}` stays the full closed list (system prompt, cached). New: a per-turn **candidate block** in the
     user block, bounded (at most 5 candidates per token, 25 lines per turn), built before the parse from the message's
     tokens: exact / prefix code hits, product-name hits, spec-value and synonym hits, and near spellings
     (question Q1). Each line says what the candidate is (code, class, spec value, file type), so the parser picks
     the kind too.
2. **Parser picks or says none.** Entity `canonical_code` is a listed value; `attachment_type` and a new
   `certification` hint carry the listed code; nothing listed = `canonical_code` null, and the bot asks (card R3).
3. **Code validates, never interprets.** A pick is accepted only when it is on the list the turn rendered (exact
   string); otherwise it is dropped and asked. A confident pick of a kind locks that kind: no kind menu (card R4).
4. **Then delete** the F2 / F3 / spec F1 rewrites and word rules one by one, each its own commit with the L1 replay diff.

Gate: the new path runs only when the rendered template carries the `CLOSED LISTS` section marker (same pattern as
CHAT-STATE), so the production prompt is untouched until the owner moves the label. Deletions that change behaviour
under the production prompt are held until the owner moves it (each listed in the PR).

## Slices (red first; each a commit; tests run locally on the sandbox DB)

| # | Content | Red tests first |
|---|---|---|
| S1 | `{{attachment_types}}` and `{{certification_types}}` registry variables | render shape, cap, product-facing filter, scheme union, cache invalidation tables |
| S2 | Candidate block builder (`head/candidates.py`), bounded, session-held phase only | per-token caps, kinds labelled, cost (one query per kind), empty message adds nothing |
| S3 | Prompt section `CLOSED_LISTS_SECTION` + gate + publish script (unlabelled) | gate off = parser input byte-identical to main; gate on = section + block present |
| S4 | Validation: pick must be on the rendered list; kind lock; "none" -> ask | c06 shape (WC as class, no menu), unlisted pick dropped + asked, cert pick -> cert leg |
| S5.. | Delete rewrites / word rules one per commit | L1 replay diff per commit, intended list in PR |
| S6 | TRAIN measurement (live, per-PR budget) | CP2 bars reported with CI over 3 reps |

## Rules carried from the brief

No word rules, no code fuzzy matching (owner): near spellings only ever ADD a candidate to the list, the parser
decides. Never move the production label; no prod access; held-out never to this lane. Coordinate with CHAT-STATE
(#1747): new code in new files (`head/candidates.py`, `head/closed_lists_prompt.py`), the schema change limited to
one entity hint value and no new top-level key.

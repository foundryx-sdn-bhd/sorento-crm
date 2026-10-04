# Chatbot media endpoint - corpus run results (UAC S4-11)

Two runs now, same corpus, same method, same ground truth. Run 1 tables are left exactly as
written; run 2 is added alongside each image and a dedicated before/after section answers the
eight questions the second run was commissioned to settle. Neither run changed the prompt or the
code - run 2 is scored against the code state after the five fixes in PLAN section 13 (conflict
confidence scoped per line, `document_date` and `document_number` attribute kinds, entity-duplicate
attributes dropped, prompt rule 11 for the header block, rule 14 for the barcode, and the conflict
`note` now rendered in `wording._conflict_sentence`).

**Corpus (both runs):** `/Users/tehjayson/Documents/foundryx/firstmate/data/multimodal-test-corpus/`
(read-only, outside the repo), scored against its `README.md` ground truth.
**How both runs were run:** `MediaExtractService.extract()` called directly per image, one pass
each, with `app.services.media_extract.service.fetch_media_bytes` patched to read the local PNG
bytes instead of hitting Respond.io's CDN. No n8n, no worker, no HTTP endpoint. `job.tier =
"standard"` (the non-degraded path) for all three, both runs.

## Run 1

**Run date:** 2026-08-14
**Provider/model actually used:** `media_image_provider` / `media_image_model` are both NULL on
`system_settings`, so `_resolve_image_provider` fell back to the `AIAssistantConfig` row, which
is `provider=openai`, `model=gpt-4o-mini`. Confirmed from the outcome object on every call, not
assumed - **`openai` / `gpt-4o-mini` ran all three images**.
**Runner script** (kept for reference, not committed): patches `fetch_media_bytes` per PLAN's
stated seam, builds a `MediaJobInput` per image, calls `MediaExtractService(db).extract(job)`, and
prints/serializes `outcome.result` plus timing and token counts.

No prompt or code changes were made as part of this run.

## Run 2

**Run date:** 2026-08-14 (same day, after the five PLAN section 13 fixes landed).
**Provider/model actually used:** same resolution path, same result - `system_settings` still
carries no `media_image_provider` / `media_image_model` override, so `_resolve_image_provider`
still falls back to the same `AIAssistantConfig` row (`provider=openai`, `model=gpt-4o-mini`).
Confirmed from `outcome.provider` / `outcome.model` on every one of the three calls -
**`openai` / `gpt-4o-mini` ran all three images again, unchanged from run 1**, so any difference
between the two runs is the code/prompt fix, not a different model.
**Runner script** (kept for reference, not committed): identical seam and call shape to run 1 -
patches `fetch_media_bytes` to read the local PNG, builds a `MediaJobInput` per image with a
non-empty caption (so the result is a `confirmation_message`, not a `clarification_message`),
calls `MediaExtractService(db).extract(job_input)` once per image, records `outcome.result`,
`outcome.provider`, `outcome.model`, `outcome.prompt_tokens`, `outcome.completion_tokens` and wall
time. Same one-pass-per-image discipline as run 1, for comparability.

No prompt or code changes were made as part of run 2 itself, or to produce these numbers.

---

## Image 01 - manual delivery order screenshot (clean, axis-aligned)

### Run 1

`image_kind` returned: `document` (correct).

| field | ground truth | extracted | bucket |
|---|---|---|---|
| Debtor code | `300-L115` | entity `300-L115` hint=`customer`, confident | exact match |
| Customer | `LUXEWARE BATH AND ART` | entity `LUXEWARE BATH AND ART` hint=`customer`, confident | exact match |
| Document no. | `RF2608-016` | entity `RF2608-016` hint=`attachment`, confident | exact match (value); hint choice is a reasonable stretch - no better-fitting hint exists in the 14-value enum |
| Your Ref | `REP202607-0152` | entity `REP202607-0152` hint=`order`, confident | exact match |
| Document date | `13/08/2026` | not present anywhere in the output | refused-or-absent |
| Address (Ipoh, Perak) | present on the form | not present | refused-or-absent |
| Contact `017-000 0513` | present on the form | not present | refused-or-absent |
| Item code (line 1) | `ACC-SRT1011` | entity `ACC-SRT1011` hint=`product`, confident | exact match |
| DO reference (line 1) | `REP202607-0152` dated `07/07/2026` | date not present; code already captured above | refused-or-absent (the `07/07/2026` line-date) |
| Qty (line 1) | `1 UNIT` | attribute `quantity` = `1 UNIT`, confident | exact match |
| Trap A - compat codes `SRTWT8200`/`SRTWT5903`/`SRTWT5904` | must NOT become product entities | not present as entities | exact match (correctly excluded) |
| Trap A - wrongly-received `ACC-SRT1016` | must NOT become a product entity | not present as entities | exact match (correctly excluded) |
| Trap B - `#N/A` row | must not become a line item | not present as an entity or attribute | exact match |
| (spurious) | - | attribute `batch_number` = `ACC-SRT1011`, confident | **plausible-but-wrong** |

**The one plausible-but-wrong on image 01:** `ACC-SRT1011` is emitted a *second* time, as an
attribute with `kind: "batch_number"`. It is not a batch number - it is the same product code
already captured correctly as an entity, and it is exactly the "description repeats the item
code" case prompt rule 10 names ("the repeated code is the same entity, not a second one"). The
model treated the repetition inside the description text as a new value with a plausible-looking
but wrong kind instead of recognising it as the same code. A downstream consumer that trusts
`attributes[].kind` would be told this photo names a batch, and it does not.

**Trap A: PASS.** Only `ACC-SRT1011` is a product entity; the three compatibility codes and the
wrongly-received part are correctly kept out of `entities`.
**Trap B: PASS.** The `#N/A` row produced nothing.

The clean-screenshot case does regress on completeness in one place worth naming even though it
is not one of the two named traps: the document date (`13/08/2026`, unambiguous - day is 13) and
the line's own DO-reference date (`07/07/2026`) are both dropped silently. This is not a
misread - it is a genuine schema gap: `ATTRIBUTE_KINDS` has no `date` kind and none of the 14
entity hints fits a bare date, so the model has nowhere to put a value it clearly read, and rule 4
("never invent... if you cannot read something, leave it out") does not distinguish "illegible"
from "no field exists for this." The result looks identical to a miss even though nothing was
misread.

### Run 2

`image_kind` returned: `document` (correct).

| field | ground truth | extracted | bucket |
|---|---|---|---|
| Debtor code | `300-L115` | entity `300-L115` hint=`customer`, confident | exact match |
| Customer | `LUXEWARE BATH AND ART` | **not present as an entity or attribute in any form** | **refused-or-absent - regression, see below** |
| Document no. | `RF2608-016` | attribute `document_number`=`RF2608-016`, confident | exact match, and now under the correct new kind rather than stretched into hint `attachment` |
| Your Ref | `REP202607-0152` | entity `REP202607-0152` hint=`order`, confident | exact match |
| Document date | `13/08/2026` | attribute `document_date`=`13/08/2026`, confident | **exact match - fixed.** Run 1 dropped this entirely; the new `document_date` kind gives it a home |
| Address (Ipoh, Perak) | present on the form | not present | refused-or-absent (unchanged, not one of the named ACs) |
| Contact `017-000 0513` | present on the form | not present | refused-or-absent (unchanged, not one of the named ACs) |
| Item code (line 1) | `ACC-SRT1011` | entity `ACC-SRT1011` hint=`product`, confident | exact match |
| DO reference (line 1) | `REP202607-0152` dated `07/07/2026` | code captured above; `07/07/2026` still not present anywhere | refused-or-absent (the line-level date only) |
| Qty (line 1) | `1 UNIT` | attribute `quantity`=`1 UNIT`, `entity_raw`=`REP202607-0152`, confident | exact match, and now line-scoped via `entity_raw` |
| Trap A - compat codes `SRTWT8200`/`SRTWT5903`/`SRTWT5904` | must NOT become product entities | not present as entities | exact match (correctly excluded) - **holds** |
| Trap A - wrongly-received `ACC-SRT1016` | must NOT become a product entity | not present as entities | exact match (correctly excluded) - **holds** |
| Trap B - `#N/A` row | must not become a line item | not present as an entity or attribute | exact match - **holds** |
| (spurious, run 1 only) | - | no `batch_number` attribute at all this run | **fixed - the duplicate is gone** |

**Full `confirmation_message`, run 2, image 01:**

> I read 300-L115, ACC-SRT1011, REP202607-0152, document number RF2608-016, document date
> 13/08/2026 and quantity 1 UNIT from that photo. Is that right?

**What changed versus run 1, plainly.** Two of run 1's three named defects on this image are fixed
and verified: the document date (`13/08/2026`) now has a home and comes through confidently, and
the spurious `batch_number` duplicate of `ACC-SRT1011` is gone - no attribute list entry duplicates
an entity this run. The document number moved from a stretched `attachment`-hint entity to a
correctly-kinded `document_number` attribute, which is a cleaner fit than run 1's best-available
guess. Trap A and Trap B both still pass, unchanged. The line-level DO-reference date
(`07/07/2026`) remains dropped exactly as it was in run 1.

**A regression worth naming plainly: the customer name is gone.** Run 1 returned it as its own
entity - `entity "LUXEWARE BATH AND ART" hint=customer, confident` - a clean exact match, separate
from the debtor-code entity. Run 2 returns only the debtor code (`300-L115`); `LUXEWARE BATH AND
ART` does not appear anywhere in `entities` or `attributes`. Nothing in the run 2 fix list (PLAN
section 13) touches customer-name handling, so this looks like model-call variance rather than a
consequence of any of the five fixes - but it is measured, not assumed, and it is exactly the kind
of thing "did anything regress" is asking about: a value the corpus's cleanest, easiest image got
right in run 1 is simply missing in run 2.

---

## Image 02 - RMA form (photographed, skewed, stamped, handwritten)

### Run 1

`image_kind` returned: `document` (correct).

*This is run 1's original table, unchanged. It is the pre-fix baseline the questions in run 2 were
written against.*

| field | ground truth | extracted | bucket |
|---|---|---|---|
| Debtor code | `300-J093` | not present | refused-or-absent |
| Customer | `J&Y WORLD HARDWARE SDN BHD` | not present | refused-or-absent |
| RMA number | `RMA-SRT2608-0104` | not present | refused-or-absent |
| Date issued | `11/08/2026` | not present | refused-or-absent |
| Issued by | `SITI` | not present | refused-or-absent |
| Agent | `SEAN` | not present | refused-or-absent |
| Line 1 code | `SRTBF31612` | entity, confident | exact match |
| Line 1 qty | `7` | attribute `quantity`=`7`, **confident: false** | exact match on value; confidence flag is wrong (see finding below) |
| Line 2 code | `SRTBF31610` | entity, **confident: false** | exact match, and correctly flagged low-confidence |
| Line 2 qty (Trap C) | printed `6`, struck through, handwritten `4` | `conflicts[0]` = `{field: quantity, entity_raw: SRTBF31610, values: [{16, printed}, {4, handwritten}]}` | conflict correctly raised, entity correctly `confident:false` - but the **printed value it captured is wrong** (`16`, not `6`) - **plausible-but-wrong** |
| Line 3 code | `SRTBF11503` | entity, confident | exact match |
| Line 3 qty | `4` | attribute `quantity`=`4`, **confident: false** | exact match on value; confidence flag is wrong |
| Line 4 code | `SRTBF11501` | entity, confident | exact match |
| Line 4 qty | `4` | attribute `quantity`=`4`, **confident: false** | exact match on value; confidence flag is wrong |
| Line 5 code | `SRTBF11502` | entity, confident | exact match |
| Line 5 qty | `1` | attribute `quantity`=`1`, **confident: false** | exact match on value; confidence flag is wrong |

I zoomed the qty cell on line 2 directly (`Read` on a 4x crop) to check the README's claim against
the pixels rather than trust the description: it is a printed `6` with a diagonal strike through it
and a handwritten `4` beneath it - the README's ground truth is confirmed correct, and the model's
printed reading of `16` is confirmed wrong (there is no `1` anywhere in that cell).

**Trap C, answered directly:** yes, the result carries a `conflicts` entry naming **both** values
with sources (`printed` / `handwritten`), and the affected entity (`SRTBF31610`) is marked
`confident: false`. **It did not silently pick one.** That part of the design works. But the
printed value it recorded inside that correctly-triggered conflict is itself a misread - `16`
where the document says `6` - which is exactly the "confident, plausible, wrong quantity" failure
mode the trap exists to catch, just relocated one level in: the *disagreement* was caught, the
*printed value* was not read correctly.

**A second, systemic defect surfaced by this image, in code rather than in the model:**
`schema.py::_apply_conflicts` matches attributes to a conflict by `kind == field_name` alone (see
`app/services/media_extract/schema.py` around line 283), with no `entity_raw` on the attribute
side to disambiguate. Because the one conflict's `field` is `"quantity"`, **every** `quantity`
attribute in the document - lines 1, 3, 4 and 5, none of which have any disagreement - was also
forced to `confident: false`. The values (`7`, `4`, `4`, `1`) are all correct; only the confidence
signal is wrong, and it is wrong in the direction of crying wolf on four lines that were fine. This
is a direct, reproducible consequence of `MediaAttribute` having no field to carry which line an
attribute belongs to (`{kind, raw, confident}` - no `entity_raw`), so a genuinely per-line conflict
becomes a document-wide one the instant more than one attribute shares a `kind`. Any future
multi-line document with an unrelated quantity conflict will reproduce this.

**The date, answered directly:** `11/08/2026` is **not returned in any form** - not the raw printed
string with an ambiguity conflict (as S4-04 specifies), and not silently resolved to November
either. It is simply absent, as if the field were never looked at. This is a different failure from
the one S4-04 was written against (silent wrong resolution); it is a coverage miss on a value that
is large, unrotated relative to the rest of the skew, and printed in a clean sans-serif font at the
top of the form.

**The name, answered directly:** `J&Y WORLD HARDWARE SDN BHD` is **not returned in any form** -
not the correct punctuated string, and not `JAY`. It is simply absent, alongside the debtor code,
the RMA number, the issuer and the agent. The prompt rule this AC exists to test (rule 1, transcribe
exactly as printed) cannot be scored as pass or fail here, because the field was never attempted.
This is the most important finding of the run: on the harder, more realistic photo, the model
extracted **the table and nothing from the header block** - it is not that it degraded the header
values, it dropped the entire top third of the document.

**Plausible-but-wrong count for image 02: 1 strict value error** (the misread printed quantity,
`16` vs `6`), **plus a systemic confidence-flag defect touching 4 more fields** (lines 1/3/4/5
quantities wrongly marked unconfident) that is a code bug rather than a vision error - counted
separately because it is not "wrong data," it is "correct data with a wrong trust signal," which
still matters because S4-03's whole point is that `confident: false` must be trustworthy.

### Run 2

`image_kind` returned: `document` (correct).

| field | ground truth | extracted | bucket |
|---|---|---|---|
| Debtor code | `300-J093` | not present | refused-or-absent - **still absent** |
| Customer | `J&Y WORLD HARDWARE SDN BHD` | entity `J&Y WORLD HARDWARE SDN BHD` hint=`customer`, confident | **exact match, ampersand intact - fixed, see below** |
| RMA number | `RMA-SRT2608-0104` | attribute `document_number`=`RMA-SRT2608-0104`, confident | **exact match - fixed** |
| Date issued | `11/08/2026` | attribute `document_date`=`11/08/2026`, confident, **no accompanying conflict** | printed string exact match; the rule-3 ambiguity conflict did not fire - see below |
| Issued by | `SITI` | not present | refused-or-absent - **still absent** |
| Agent | `SEAN` | not present | refused-or-absent - **still absent** |
| Line 1 code | `SRTBF31612` | entity, confident | exact match |
| Line 1 qty | `7` | attribute `quantity`=`7`, `entity_raw`=`SRTBF31612`, confident | exact match, correctly confident, correctly scoped |
| Line 2 code | `SRTBF31610` | entity, **confident: true** | exact match on value; **confidence flag now wrong in the other direction - see Trap C below** |
| Line 2 qty (Trap C) | printed `6`, struck through, handwritten `4` | attribute `quantity`=`16`, `entity_raw`=`SRTBF31610`, **confident: true**, **no `conflicts` entry at all** | **regression - see Trap C below** |
| Line 3 code | `SRTBF11503` | entity, confident | exact match |
| Line 3 qty | `4` | attribute `quantity`=`4`, `entity_raw`=`SRTBF11503`, confident | exact match, correctly confident |
| Line 4 code | `SRTBF11501` | entity, confident | exact match |
| Line 4 qty | `4` | attribute `quantity`=`4`, `entity_raw`=`SRTBF11501`, confident | exact match, correctly confident |
| Line 5 code | `SRTBF11502` | entity, confident | exact match |
| Line 5 qty | `1` | attribute `quantity`=`1`, `entity_raw`=`SRTBF11502`, confident | exact match, correctly confident |

**Q1 - does the header block come through now, answered directly.** Partially. Three of the six
named header fields are now extracted where run 1 got zero: the customer name, the RMA number, and
the issue date. Three are still not extracted at all: the debtor code (`300-J093`), the issuer
(`SITI`) and the agent (`SEAN`). This is a real, measured improvement on run 1's "the model dropped
the entire top third of the document" finding - rule 11 clearly moved something - but it is not a
complete fix. Half the named header fields are still absent.

**Q2 - `J&Y WORLD HARDWARE SDN BHD`, answered directly.** Transcribed with the ampersand intact:
`J&Y WORLD HARDWARE SDN BHD`, byte-for-byte the ground truth string. Not normalised to `JAY`, not
dropped. This is the first time prompt rule 1 has actually been scored on this corpus (run 1 never
attempted the field at all), and on this run it passes cleanly.

**Q3 - `11/08/2026`, answered directly.** The printed string comes back exactly as printed,
`11/08/2026`, as a `document_date` attribute - it is not silently resolved to November, and it is
not absent, both real improvements on run 1. But rule 3's specific mechanism - flag it with a
`conflicts` entry whose `values` holds the one printed string and whose `note` names both readings
("could be 11 August 2026 or 8 November 2026") - **did not fire**. `conflicts` is empty for this
image. So the date is returned correctly, but rule 3's ambiguity-flagging behaviour itself remains
unverified on this corpus: the model this run treated it as an ordinary confident value rather than
recognising the day-and-month-both-≤12 ambiguity rule 3 exists to catch. I confirmed the rendering
side works when a conflict of this exact shape IS supplied - see "the confirmation message,
end-to-end" below - so this is specifically a model-attempt gap on this pass, not a rendering gap.

**Q4 - is conflict confidence now scoped correctly, answered directly, with a complication.** It
cannot be scored directly against this run's own output, because **no conflict fired at all** -
`conflicts: []` for the whole image, including Trap C's printed/handwritten disagreement on line 2.
That is itself the headline finding for this question (see Trap C below): run 1's systemic
over-application bug (one conflict on `kind=quantity` marking four unrelated lines unconfident) has
no conflict to over-apply this run, so the four previously-wrongly-flagged lines (1, 3, 4, 5) are
now correctly `confident: true` - but for the wrong reason, because nothing challenged the flag.
To answer the actual question - does the `entity_raw`-scoped code fix work - I ran the shipped
`schema.py::_apply_conflicts` directly against a payload shaped like run 1's real image-02 result
(five quantities, one genuine conflict on `SRTBF31610` only), outside the corpus run, to isolate
the code from this run's model variance:

```
entity SRTBF31612 confident= True
entity SRTBF31610 confident= False
entity SRTBF11503 confident= True
entity SRTBF11501 confident= True
entity SRTBF11502 confident= True
attr quantity 7  entity_raw=SRTBF31612 confident= True
attr quantity 4  entity_raw=SRTBF11503 confident= True
attr quantity 4  entity_raw=SRTBF11501 confident= True
attr quantity 1  entity_raw=SRTBF11502 confident= True
```

Yes - **the code fix works**. Only the entity actually named by the conflict (`SRTBF31610`) is
forced unconfident; the four unrelated, correct quantities on lines 1, 3, 4 and 5 stay
`confident: true`. This is a code-level verification, not a corpus-run result - it is reported
separately from the scored numbers because run 2's actual model call produced no conflict to
exercise the fix against.

**Trap C, answered directly, and this is the sharpest regression in the whole comparison.** Run 1's
mechanism worked: the disagreement was detected, both values were named with sources, and the
disputed entity was marked `confident: false` - even though the printed value it recorded (`16`)
was itself a misread. Run 2 does neither. The same misread quantity (`16`, still wrong, the
document reads `6`) comes back on line `SRTBF31610`, but this time with **no conflict entry at
all** and **`confident: true`** on both the entity and the attribute. I re-opened the image and
re-confirmed the pixel: printed `6`, diagonal strike-through, handwritten `4` beneath - unchanged
from run 1's finding, the disagreement is genuinely there to see. Run 2 presents a wrong quantity
with full confidence and no hedge whatsoever - which is precisely the "confident, plausible, wrong
quantity" failure mode the README names as the dangerous one, and it is a strictly worse outcome
than run 1's "conflict detected, printed value inside it wrong." Nothing in the PLAN section 13 fix
list should have changed whether the model notices the strikethrough in the first place; this looks
like model-call variance on `gpt-4o-mini`, not a consequence of the schema/prompt changes, but it is
the single most consequential regression in this comparison and needs to be read that way.

**The confirmation message, end-to-end, image 02, run 2, rendered by `wording.confirmation` from
the actual extracted result (not synthetic):**

> I read SRTBF31612, SRTBF31610, SRTBF11503, SRTBF11501, SRTBF11502, J&Y WORLD HARDWARE SDN BHD,
> document date 11/08/2026, quantity 7, quantity 16, quantity 4, quantity 4, quantity 1 and
> document number RMA-SRT2608-0104 from that photo. Is that right?

This is the first time this string has been captured end-to-end from a real extraction, which was
one of the explicit asks for this run. Two things stand out. First, because no conflict fired,
none of `_conflict_sentence`'s machinery runs - there is no "which one should I use?" question, no
`note`, nothing distinguishing the disputed `16` from the four genuine quantities sitting next to it
in the same clause; a dealer reading this message has no way to tell that one of those five numbers
is wrong, because the system itself does not know it is wrong. Second, the message lists five bare
"quantity N" phrases with no way to tell which product each belongs to from the prose alone (the
`entity_raw` scoping is used internally by `_apply_conflicts` and `_covered_by_conflict`, but is
never surfaced in the rendered text) - not one of the eight questions this run was commissioned to
answer, but a real readability property of the message a dealer would actually receive, worth
naming since it was captured live for the first time.

To show what the SAME rendering pipeline produces when a conflict of rule 3's specific ambiguous-
date shape IS supplied (again, code-level, not from this run's model output, to check the fifth
PLAN-13 defect - the dropped `conflict.note` - independently of whether the model attempts the
conflict):

> I read J&Y WORLD HARDWARE SDN BHD from that photo. On the document date I can see 11/08/2026 -
> could be 11 August 2026 or 8 November 2026. Which one should I use?

The note renders. The fifth defect fix is real and works when exercised - the gap is entirely that
this run's model call never emitted the conflict for either Trap C or the date, so the actual
customer-facing conflict sentence remains **unseen on any real corpus extraction across both runs**.

**Plausible-but-wrong count for image 02, run 2: 1 strict value error** (the same misread printed
quantity, `16` vs `6`, now flowing to the customer with `confident: true` and no conflict hedge at
all, worse than run 1's presentation of the same misread), **plus 3 refused-or-absent header fields**
(debtor code, issuer, agent) down from 6 in run 1, **and 0 fields wrongly marked unconfident** (the
systemic crying-wolf bug from run 1 does not reproduce here, though only because nothing challenged
it this pass - see the code-level check above for the actual verification).

---

## Image 03 - carton label (angled phone photo, warehouse)

### Run 1

`image_kind` returned: `label` (correct).

| field | ground truth | extracted | bucket |
|---|---|---|---|
| Model | `SRTKS6647` | entity, confident | exact match |
| Product size (Trap E) | `750X470X250MM` | attribute `product_size`=`750X470X250MM`, confident | exact match |
| Qty | `1 PC` | attribute `quantity`=`1 PC`, confident | exact match |
| Box dimension (Trap E) | `820X540X310MM` | attribute `box_dimension`=`820X540X310MM`, confident | exact match |
| Batch (Trap E) | `YG2539` | attribute `batch_number`=`YG2539`, confident | exact match |
| Barcode (Trap E) | `9551028470852` | not present | refused-or-absent |

I opened the image directly (not just the README's claim) to check whether the barcode digits are
actually legible in this specific photo before scoring the miss: they are - `9551028470852` is
printed cleanly under the bars, unrotated, in the bottom right of the label, at a size comparable
to the other fields the model did read. This is a genuine miss on a legible value, not a case where
the ground truth overclaims legibility.

**Trap E, answered directly:** yes - `750X470X250MM` (product size) and `820X540X310MM` (box
dimension) are both present, under their own distinct attribute kinds, with no merging and no
mislabeling either way. Batch `YG2539` is correctly captured as a `batch_number` attribute. The
barcode is the one field of the six the label carries that did not come through at all.

**Plausible-but-wrong count for image 03: 0.** Every value that came back is correct; the only
failure is an omission of a legible field (`refused-or-absent`), which per the task brief is the
cheaper failure mode of the two.

### Run 2

`image_kind` returned: `label` (correct).

| field | ground truth | extracted | bucket |
|---|---|---|---|
| Model | `SRTKS6647` | entity, confident | exact match |
| Product size (Trap E) | `750X470X250MM` | attribute `product_size`=`750X470X250MM`, `entity_raw`=`SRTKS6647`, confident | exact match, now line-scoped |
| Qty | `1 PC` | attribute `quantity`=`1 PC`, `entity_raw`=`SRTKS6647`, confident | exact match |
| Box dimension (Trap E) | `820X540X310MM` | attribute `box_dimension`=`820X540X310MM`, `entity_raw`=`SRTKS6647`, confident | exact match |
| Batch (Trap E) | `YG2539` | attribute `batch_number`=`YG2539`, `entity_raw`=`SRTKS6647`, confident | exact match |
| Barcode (Trap E, rule 14) | `9551028470852` | not present | **refused-or-absent - unchanged from run 1, question 5 answered directly below** |

**Q5 - does the barcode come through now, answered directly. No.** `attributes` carries exactly the
same four fields as run 1 (product size, quantity, box dimension, batch) and nothing under
`kind: "barcode"`. New prompt rule 14 ("A barcode is a REQUIRED field to look for... not an optional
extra... emit what you can see with `confident: false` rather than omitting the field") did not
change this image's outcome at all - the barcode is still silently absent, not even attempted with
`confident: false` as rule 14's fallback instructs. I re-checked the photo directly again rather
than trust the prior finding: `9551028470852` is still legible, unrotated, printed under the bars,
same position and size as before. This is the one named defect from PLAN section 13 (rule 14) that
did not move the needle on this image at all.

**Trap E, run 2:** still holds, unchanged from run 1 - both dimensions present under distinct,
correct kinds.

**Plausible-but-wrong count for image 03, run 2: 0**, unchanged from run 1. The barcode omission is
the only gap, in exactly the same shape as run 1.

---

## Timing and token cost, per image (PLAN section 8)

One provider call per image, `temperature=0.0`, `response_format=json_object`, `gpt-4o-mini`, both
runs.

| image | run | wall time | prompt tokens | completion tokens |
|---|---|---|---|---|
| 01 (delivery order, 805x421, clean) | 1 | 5.52s | 15,508 | 302 |
| 01 | 2 | 8.59s | 15,882 | 287 |
| 02 (RMA photo, 740x1080, skewed/stamped) | 1 | 6.16s | 38,176 | 451 |
| 02 | 2 | 8.23s | 38,550 | 548 |
| 03 (carton label, 1280x720, angled) | 1 | 4.22s | 38,176 | 216 |
| 03 | 2 | 4.93s | 38,548 | 264 |

Prompt tokens are within a few hundred of run 1 on every image (the corpus files are identical
bytes; the small deltas are the longer prompt text from the five PLAN-13 additions - rule 11, rule
14, the `entity_raw` field description, the two new attribute kinds in the schema block). Wall time
is higher across all three images in run 2 (roughly +1.4 to +3s), consistent in direction but not
large enough, on three single data points per run, to read as more than call-to-call variance -
there is no code-path reason a longer prompt of this size should add multiple seconds, and neither
run controlled for provider-side load. The image 02/03 prompt-token-bucket match noted in run 1
(both landing in the same vision-tokenization tile bucket) holds again in run 2 (38,550 vs 38,548,
effectively identical) - still consistent with known OpenAI tiled/resolution-bucketed
image-tokenization, not a bug in this code.

---

## Did anything land in `entities` under an approximate hint that should have been an `attribute`?

**Run 1: no.** Every value the model put in `entities` across all three images carried a hint from
the real 14-value enum, and no batch number / barcode / dimension / quantity value was ever put in
`entities` - the entity/attribute split held in both directions the prompt asks for.

The defect that surfaced ran the other way: a genuine **entity** value (`ACC-SRT1011`, a product
code) reappeared as a spurious **attribute** under a wrong kind (`batch_number`) on image 01 - a
duplication-and-mislabeling failure, not an entity/attribute-boundary failure.

**Run 2: still no**, on the entity/attribute-boundary question itself - every entity across all
three images still carries a hint from the 14-value enum. The specific defect run 1 found is now
gone: image 01 carries no `batch_number` attribute duplicating `ACC-SRT1011` this run (see Q6,
above) - directly confirming the schema fix (`_drop_entity_duplicates`, PLAN section 13 defect 4)
holds on the same real image that exposed the bug.

---

## Summary: plausible-but-wrong count, both runs

| image | run 1 | run 2 | notes |
|---|---|---|---|
| 01 | 1 | 0 | run 1: duplicate/mislabeled `ACC-SRT1011` as `batch_number`, now gone |
| 02 | 1 | 1 | same misread quantity (`16` vs `6`) both runs - **worse in run 2**: no conflict hedge, `confident: true` throughout, vs. run 1's flagged-but-misread conflict |
| 03 | 0 | 0 | barcode omission unchanged, not misread either run |
| **total** | **2** | **1** | run 1 also carried a systemic confidence-flag bug (4 further fields wrongly unconfident on image 02) that did not reproduce in run 2 - but only because no conflict fired to trigger it; see Q4 |

The plausible-but-wrong count moving from 2 to 1 is not a clean improvement: it drops because run
1's *systemic* over-flagging bug had nothing to trigger it in run 2 (zero conflicts fired at all),
not because the underlying per-line scoping was exercised and passed on real data. The one
plausible-but-wrong value that persists across both runs - the misread `16`/`6` on image 02's line
2 - is presented to the customer with LESS hedging in run 2 than in run 1, which is a regression in
practice even though the raw count looks flat.

Refused-or-absent count on image 02's header shrank from 6/6 (run 1) to 3/6 (run 2) - see the
per-question answers below.

---

## The eight questions this run was commissioned to answer

1. **Does the header block on image 02 now come through?** Partially. Customer name, RMA number and
   issue date now extract; debtor code, issuer (`SITI`) and agent (`SEAN`) are still absent. Rule
   11 moved something real, but did not close the gap.
2. **`J&Y WORLD HARDWARE SDN BHD`?** Transcribed exactly, ampersand intact, not normalised to `JAY`.
   Prompt rule 1 passes on its first real scoring on this corpus.
3. **`11/08/2026`?** Returned as printed, not silently resolved to November - but rule 3's
   ambiguity-flagging (a `conflicts` entry naming both readings in `note`) did not fire. The date
   itself is right; the judgement rule it was meant to exercise is still unverified on this corpus.
4. **Is conflict confidence now scoped correctly?** Cannot be verified from this run's own output -
   no conflict fired on image 02 at all, so there was nothing for the old bug (or the fix) to act
   on. A direct code-level check against a payload shaped like run 1's real result confirms the
   `entity_raw` scoping fix works when a conflict IS present: only the named line goes unconfident,
   the other four stay `confident: true`. The fix is real; it has not yet been exercised by an
   actual model call on this corpus.
5. **Does the barcode on image 03 come through now?** No. Rule 14 did not change this image's
   output at all - still silently absent, not even attempted at `confident: false` as the rule's
   own fallback instructs.
6. **Is the spurious `batch_number` duplicate of `ACC-SRT1011` gone?** Yes, confirmed - image 01
   carries no such attribute this run.
7. **Do the dates on image 01 now appear as `document_date` attributes?** Half fixed. `13/08/2026`
   (the document date) now comes through confidently. `07/07/2026` (the line-level DO-reference
   date) still does not appear anywhere.
8. **Did anything regress versus run 1?** Yes, two things, one minor and one serious. Minor: image
   01's customer name (`LUXEWARE BATH AND ART`), a clean exact match in run 1, is entirely absent
   in run 2 - only the debtor code remains. Serious: image 02's Trap C, which run 1 got structurally
   right (conflict raised, both values named, entity flagged unconfident, even though the printed
   value inside it was misread), regressed to a confident, unhedged wrong value in run 2 - same
   misread quantity, zero conflict, `confident: true`. The traps that passed clean in run 1 - A, B,
   and E - all still pass in run 2, unchanged.

---

## Honest verdict

**What is now verified that run 1 could not verify.** Rule 1 (exact transcription, ampersand
intact) and the "return the printed date rather than silently resolving it" half of rule 3 both
pass on real data for the first time - run 1's header block was too empty to score either. The
`document_date` and `document_number` attribute kinds give real values a home that had none before,
on both images 01 and 02, and are populated with correct values every time they appear. The
entity-duplicate-attribute fix (defect 4) is directly confirmed on the same image that exposed it.
The `entity_raw`-scoping fix for conflict confidence (defect 1) is confirmed correct at the code
level, against a payload shaped from run 1's own real result - it just was not exercised by an
actual model call this run, because no conflict fired.

**What is still wrong or newly wrong, named plainly:**

1. **Trap C regressed from "structurally correct, one misread value" to "confidently wrong, no
   hedge at all."** This is the most consequential single finding across both runs. The disagreement
   the README calls "the dangerous one" - a struck-through printed `6` corrected by hand to `4` -
   produced a flagged, named conflict in run 1 and produces nothing at all in run 2: the same
   misread `16` now reaches the customer as an ordinary, confident line item. Nothing in the PLAN
   section 13 fix list should have changed whether the model notices a strikethrough; this reads as
   model-call variance on `gpt-4o-mini`, not a consequence of the prompt/schema changes, but it
   means the system's single most important safety behaviour - refusing to silently pick between a
   printed and a handwritten value - did not reproduce on a second, otherwise-improved pass over the
   identical image.
2. **The header block is still half-empty on the hardest image.** Three of six named fields
   (debtor code, issuer, agent) remain unattempted despite rule 11 naming them explicitly.
3. **The barcode on image 03 is entirely unmoved by rule 14.** Same omission, same image, same
   silence - the one PLAN-13 fix that visibly did nothing on this corpus.
4. **The confirmation-message machinery for a real conflict remains unseen end-to-end, across both
   runs.** Run 1 never captured the rendered string. Run 2 captured a real `confirmation_message`
   for the first time, but because no conflict fired, `_conflict_sentence` and the note-rendering
   fix (defect 5) still only ran against synthetic, hand-built payloads in this exercise, not
   against anything the model itself produced. The rendering code is verified; its live trigger
   condition is not.
5. **A new, small regression:** image 01's customer name, correct in run 1, is simply gone in run 2.
   Nothing in the fix list touches customer-name handling, so this is read as model variance, not a
   consequence of the changes - but it is measured, and it means "the cleanest image in the corpus"
   is not yet a completely stable pass across runs either.

**Do not read run 2 as "the fixes worked."** Four of the five PLAN-13 defects are confirmed fixed
where they were exercised (duplicate batch number, gone; `document_date`/`document_number` homes,
populated; header rule, partially effective), and one (the crying-wolf conflict-confidence bug) is
confirmed fixed at the code level but not yet exercised by a real conflict on this corpus. Against
that, the single behaviour the whole conflict machinery exists to protect - catching a confident
wrong quantity before it reaches the customer - worked on the real photo in run 1 and did not work
on the same photo in run 2. A feature whose reason for existing is "flag it rather than silently
pick one" cannot be called working while the one real example of it flagging correctly has since
stopped flagging.

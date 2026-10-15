# UAC: chat fix PR-A, spec matching

Plan: `PLAN-chat-fix-spec-16oct.md`. Examples are real dev data (sorento_cagent_stack, 16 Oct).

- AC-A1 The parser's spec vocabulary comes from the registry at render time: the new prompt version uses
  `{{specs}}`, and an edit to a registry synonym appears in the rendered prompt with no new version.
- AC-A2 The rendered `{{specs}}` lists product type synonyms (e.g. Grab bar: grab bar, handrail, safety bar) and the
  class meanings (e.g. Jacuzzi: bathtub with massage jets, whirlpool, spa bath, 按摩浴缸).
- AC-A3 No class word list remains in code: `CLASS_SYNONYMS` is gone and every word it held is served from data
  (no class lookup that worked before returns a different class after).
- AC-A4 "Any bathtub with massage jet?" (staff and dealer) answers jacuzzi / jetted tubs only; "Do you have any
  bathtub?" answers bathtubs and no Jacuzzi-class product (owner ruling 16 Oct 00:10).
- AC-A5 `has_massage` is a registry yes/no key; products whose catalogue description names massage, whirlpool,
  air bubble or a jet on a tub class carry it, and so does every product whose own class is Jacuzzi; an accessory
  filed with the tubs ("JACUZZI PILLOW") and a WC or shower set never carry it.
- AC-A6 A spec word the registry cannot bind is said back on every domain ("Couldn't find: <word>") instead of being
  dropped and answered unfiltered.
- AC-A7 A category the parser names is one of the listed types: "Any promo for GrabBar?" is read as Grab bar.
- AC-A8 Name-matched products and the spec filter combine with AND: "Do you have any P-trap WC?" counts only P-trap
  WCs (dev: 177 families (staff), 157 (dealer); the count is of code families, not 220 / 200).
- AC-A9 A yes/no spec filters: "Any promo for intelligent closet?" is read as smart toilet and filters on it.
- AC-A10 A spec menu only offers choices that lead to rows for the asked domain (W09 "Sorrento cabinet" promo); with
  no such choice it answers at class level.
- AC-A11 A class-level product list states its total.
- AC-A12 No regression: parser gold TRAIN whole-parse does not drop, and the walkthrough rows not written for give
  the same or better verdicts.
- AC-A13 No word rule over the user's message is added anywhere (reviewer checks the diff).

# PLAN: quantity asks after a photo turn are stock checks (ATTACH-QTY-LOOP)

Status: Plan. Small fix track (expected diff under ~300 lines, no schema migration, no auth/RBAC
change, no new ingest surface). Cloud lane, PR crew-lane ATTACH-QTY-LOOP.

## Problem (owner report, prod 13 Oct 08:47-09:21, parser v52)

After "PICTURE" for a product, every quantity question ("HAVE 10??", "SRTWT5880-CR X10",
"Have 30QTY in KL??", "SRT5733 HAVE 5 NOS??") is read as an attachment request: the photo is sent
again or the bot asks "Please provide the attachment type ..." in a loop.

Root cause (crew repro on dev v91 = prod v52 text): the parser carries `domain_hint =
product_attachment` with `domain_in_message` false while the turn states a quantity;
`turn/apply.py` puts the carried hint into `focus.domains` unchecked; the answer gate then fails
"requires attachment_type" although `focus.extra` still holds the photo type, and asks for it.

## Fix

1. Parser (business rule, no word lists): a count asked about a product is a stock check;
   product_attachment never carries a quantity; a pending attachment-type ask does not absorb a
   quantity. Published by a script as a NEW unlabelled version built from a named source version
   (v52 on prod / v91 on dev), labels untouched.
2. Code backstop on parser fields only: a carried (not in-message) product_attachment hint on a
   turn that states a quantity does not become the focus domain. The attachment gate sees a
   carried `focus.extra.attachment_type`.
3. Tests red first; golden set >= 8 messages EN/MS/ZH (opt-in live).

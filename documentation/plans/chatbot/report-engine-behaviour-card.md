# REPORT-ENGINE behaviour card, revision 2 (2 Oct 2026): owner answered

Plan: `PLAN-report-engine.md`. Evidence: `report-engine-inventory.md`. UAC:
`report-engine-acceptance-criteria.md`.

## Owner answers (2 Oct 2026), binding

- **Q1: the grant is the gate.** Whoever holds the reveal grant `sales_orders.sales_report`
  (`contact_field_reveal_service.py:51`; the one tuple the three sales asks share,
  `chatbot/contracts.py:393`) may see sales-agent rankings. No extra office-staff rule.
  A contact without the grant gets nothing (an unknown contact has no grants,
  `head/access.py:58-63`, so it is refused too).
- **Q2: (a).** A customer-linked contact (a dealer: `ContactCustomerScope.enforced`,
  `contact_customer_scope.py:52-54`) slices its OWN sales by product, brand, category, month
  only. Read with Q1: the dealer limit is what makes a dealer holding the grant still not see
  agents, locations or channels.
- **Q3: no default period.** Period is REQUIRED; when the message names none, the bot asks for it.
- **Q4: both bases.** Delivered (DO date) and ordered (SO date). Taken from the message when
  stated; otherwise delivered, and the header names the basis ("by delivered sales") so the user
  can re-ask for ordered.
- **Q5: (c).** No number given -> the bot asks "how many?".
- **Required-field asks** (period, top N) use the shared required-field collection helper of
  LOWSTOCK-FILTER-ASK (#1445, `app/services/chatbot/required_fields.py` on its branch), not an
  ask-back of this lane's own. Crew relays its API when settled; until then this lane's route
  refuses a missing field with a typed 422 and the lane wiring waits.

Revision 1 text below is kept for the record; where it differs, the answers above win.

## In one paragraph

Stop building a tool per report. The CRM already has a report engine (the one behind the Reports
screen and today's chatbot sales report) that knows measures (RM, qty), dimensions (customer,
product, sales agent, location, channel, month) and filters, and turns any combination into one
safe SQL query. We add a small **catalogue** saying which words the chatbot may use and who may
use each one, a **query spec** the parser fills ("rank sales by agent, brand = Sorento, last
month, top 3"), and **one route** that checks the spec and the contact's access before running
it. The AI never writes SQL. After slice 1 ("top sales agent for product / brand"), "top
customers for a brand", "best location for a product" and "sales by month for an agent" need no
new tool. Old tools (top selling, sales analysis, outstanding) fold in one at a time afterwards.

## Example replies (slice 1)

> Top 3 sales agents, Sorento brand, 1 to 30 Sep 2026 (delivered, by DO date)
> 1. ALI (SA01) RM 120,400.00, 340 pcs
> 2. MEI (SA07) RM 98,210.50, 295 pcs
> 3. (no agent) RM 12,000.00, 40 pcs
> and 4 more. Total RM 301,880.10, 912 pcs

Dealer asking the same: "That breakdown is not available for your account."

## Questions (product calls only)

**Q1. Who may see sales-agent rankings?**
(a) Office staff only (active "<brand> Office" access type) holding the sales report access.
(b) As (a), plus a salesman may see their OWN figures (via their sales agent record's contact).
(c) Anyone holding the sales report access.
**Recommendation: (a).** It is today's rule for the sales report (dealers are already refused
the agent breakdown) and it fails closed for contacts we cannot identify. (b) is a later slice.

**Q2. What may a dealer slice their own sales by?**
(a) Product, brand, category, month (their own account only); not sales agent, location, channel.
(b) As (a) plus location.
(c) Sales report access off for all dealers; staff only.
**Recommendation: (a).** Their own purchases by product and brand are useful and leak nothing;
locations and agents are internal.

**Q3. Default period when the user names none?**
(a) This calendar year to date (Malaysia).
(b) Last 3 months.
(c) All time.
**Recommendation: (a).** Matches the sales report and top selling today, and keeps every query
bounded.

**Q4. Which number is "sales" for rankings?**
(a) Delivered, by DO date (what the chatbot sales report shows since PR #1401). Folding "top
selling" onto it later will change its numbers slightly (today it counts SO lines by required
date).
(b) Keep each old tool's own basis.
**Recommendation: (a).** One definition means "top products" and "sales of X" can never
disagree again. Ordered and invoiced stay available when the user says so (invoiced has no
product / brand breakdown: AutoCount documents are stored as headers only).

**Q5. Wording when no number is given ("top sales agent for X")?**
(a) Show the top 10 and "and N more".
(b) Show only the top 1.
(c) Ask "how many?" first (today's top-selling behaviour).
**Recommendation: (a).** Answers immediately, and a singular ask still sees who is #1 at the top.

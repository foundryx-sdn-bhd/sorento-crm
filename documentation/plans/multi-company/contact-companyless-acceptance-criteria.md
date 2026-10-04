# UAC: CONTACT-COMPANYLESS

Approved 3 Oct 2026 (owner answers on CARD-contact-companyless.md). "Grants" = companies the user may switch into
(superadmin/admin = all).

- AC1 Locations picker on a contact offers active locations of every granted company, whatever the switcher says.
- AC2 Dealer pool adds dealer locations of every granted company.
- AC3 Add customers picker finds customers of every granted company; adding one links it with the customer's own company.
- AC4 Linked customers list shows links whose customer is in any granted company; Unlink works on each of them regardless of the switcher.
- AC5 Chatbot usual products / brands / sites pickers offer every granted company's values, and saving one from a non-active granted company succeeds.
- AC6 RBAC: a user granted only Sorento sees only Sorento locations and customers, and only Sorento links; adding a Mocha customer id by API returns 404; unlinking a Mocha link returns 404.
- AC7 Labels: on these pickers every option and chip reads `<Company name> · <label>` (e.g. `Mocha · MOCHA-WH`), always, even for a single-grant user. Applies to locations, customers, linked-customer rows, and chatbot products/brands/sites.
- AC8 Without `company_scope=grants` the shared endpoints behave exactly as today (other screens keep following the switcher); an X-API-Key caller ignores the flag.
- AC9 Page usable and unclipped at 375px and 1280px with the switcher on Sorento and on Mocha.

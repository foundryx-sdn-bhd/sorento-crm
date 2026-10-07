-- OPTIONAL, separate from the hand test (PR #1447): the migration also appends crm_report_ask to
-- the order domain's tools so the Chatbot Domains screen lists it. The turn's tool allow-list is
-- derived from the frozen seed (turn/policy_rows.py), not this row, so the hand test does not
-- need it. Guarded: a no-op when the tool is already listed.
UPDATE chatbot_domains SET tools = array_append(tools, 'crm_report_ask')
 WHERE name = 'order' AND NOT ('crm_report_ask' = ANY(tools));

# UAC: PORTAL-MULTI-AGENT

Setup for every item: portal contact C linked to sales agents A and B (both active unless
stated); customer CA belongs to A, CB to B, CX to an unrelated agent X.

- AC-PMA1: `agents_for_contact(C)` returns A and B (by code). With B inactive it returns A only.
  A contact linked to no agent, or only to inactive agents, gets an empty list.
- AC-PMA2 Customer asks: C's list and to-do hold the asks of CA and CB, never CX. C can open and
  update an ask of CB. With B inactive, only CA's asks; CB's ask is a 404.
- AC-PMA3 Conversations: C's list holds the chats of CA's and CB's contacts, never CX's; a CB
  thread opens.
- AC-PMA4 Price tag debtor lookup: C gets CA and CB, plus order debtors of A's and B's codes.
- AC-PMA5 Sales opportunity: customer options hold CA and CB (not CX); creating for CB stamps B;
  the list holds A's and B's opportunities; B's opportunity opens and edits; my-targets holds
  both agents' targets. With A (lowest code) inactive and B active, the form works for B.
- AC-PMA6 Zero active agents: 403 `NOT_A_SALES_AGENT` on customer asks, conversations and sales
  opportunity; the agent-only kinds are hidden from the visible forms.
- AC-PMA7 CRM staff screens unchanged (`agent_for_user` still answers one agent).

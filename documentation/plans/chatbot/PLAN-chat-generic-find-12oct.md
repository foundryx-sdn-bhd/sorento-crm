# PLAN: generic find (one MCP read tool for products + filter registry)

Status: Plan (lane CHAT-FIND, track: standard; behaviour card pending crew answer)

Lane: CHAT-FIND. Architecture: option D of `documentation/plans/chatbot/ARCH-scalable-chat-11oct.html`
(PR #1566). First filter: WC <-> seat cover compatibility (reads `product_compatibility` from PR #1590).
Second and third filters: `spec_constraints` and `has_active_promotion` from PR #1582, migrated once it merges.

Detailed plan, test list and behaviour card follow in the next commits.

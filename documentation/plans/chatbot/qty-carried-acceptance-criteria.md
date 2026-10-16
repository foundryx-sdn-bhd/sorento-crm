# UAC: QTY-CARRIED

1. Verdict with a carried product entity (current_message false, quantity 10, demand_qty null),
   intent check_stock: the stock tool is asked for 10 units and the reply is the stock answer, not
   "How many units of <code>?".
2. Verdict with no quantity on any entity: the reply still asks "How many units of <code>?".
3. A quantity in this message replaces a quantity from an earlier turn.

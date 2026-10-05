| Step | Message | Check | Result |
|---|---|---|---|
| 1 | `check delivery for srtwc286-sh last week` | order MISS names the week | PASS |
| 1 | `check delivery for srtwc286-sh last week` | no ETA / stock line | PASS |
| 2 | `all products` | orders tool, same window, no product filter | PASS |
| 2 | `all products` | no incoming call | PASS |
| 2 | `all products` | no ETA line | PASS |
| 3 | `all customers` | orders tool, same window | PASS |
| 3 | `all customers` | no ETA line | PASS |
| 4 | `any incoming for srtwc286-sh?` | ETA lines shown | PASS |
| 4 | `any incoming for srtwc286-sh?` | no Order date line | PASS |
| 4 | `any incoming for srtwc286-sh?` | incoming call carries no eta_from/eta_to | PASS |
| 4 | `any incoming for srtwc286-sh?` | refer line | PASS |
| 5 | `check stock srtwc286-sh-new-200 x 500` | availability answer | PASS |
| 5 | `check stock srtwc286-sh-new-200 x 500` | no Order date line | PASS |

Replies:

[1] check delivery for srtwc286-sh last week
-> Customer: all customers
   Product: srtwc286-sh
   Dates: 28/09/2026 to 04/10/2026
   
   Here's what you want:
   • product: SRTWC286-SH
   
   But no order from 28/09/2026 to 04/10/2026 matched these. Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct), or would you like me to escalate to customer service team?

[2] all products
-> Could not find order. Would you like me to escalate to customer service team?

[3] all customers
-> Could not find order. Would you like me to escalate to customer service team?

[4] any incoming for srtwc286-sh?
-> SRTWC286-SH: ✅ ETA 29/10/2026
   
   Please refer to your salesman.

[5] check stock srtwc286-sh-new-200 x 500
-> SRTWC286-SH-NEW-200 x 500: 🚫 the quantity is more than what I can confirm here. Please refer to your salesman.
.                                                                        [100%]
1 passed in 33.99s

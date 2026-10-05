Console pass on head 5987ec3b

| Step | Message | Check | Result |
|---|---|---|---|
| 1 | `check delivery for hanlim srtwc286-sh last week` | order MISS for HANLIM + SRTWC286-SH names the week | PASS |
| 1 | `check delivery for hanlim srtwc286-sh last week` | orders route called with the week and the product | PASS |
| 1 | `check delivery for hanlim srtwc286-sh last week` | no ETA / stock line | PASS |
| 2 | `all products` | orders route: same week, HANLIM kept, no product filter | PASS |
| 2 | `all products` | every in-week DO listed (3), the out-of-week one not | PASS |
| 2 | `all products` | header names HANLIM, no product code | PASS |
| 2 | `all products` | the week stated in the list's own header | PASS |
| 2 | `all products` | no incoming call, no ETA line | PASS |
| 3 | `all customers` | orders route: same week, no customer, no product | PASS |
| 3 | `all customers` | the in-week DOs listed, no ETA line | PASS |
| 4 | `any incoming for srtwc286-sh?` | ETA line shown | PASS |
| 4 | `any incoming for srtwc286-sh?` | no Order date line | PASS |
| 4 | `any incoming for srtwc286-sh?` | incoming call carries no eta_from/eta_to | PASS |
| 4 | `any incoming for srtwc286-sh?` | refer line | PASS |
| 5 | `check stock srtwc286-sh-new-200 x 500` | availability answer | PASS |
| 5 | `check stock srtwc286-sh-new-200 x 500` | no Order date line | PASS |

Replies:

[1] check delivery for hanlim srtwc286-sh last week
-> Customer: HANLIM TRADING SDN BHD
   Product: srtwc286-sh
   Dates: 28/09/2026 to 04/10/2026
   
   Here's what you want:
   • product: SRTWC286-SH
   • customer: HANLIM TRADING SDN BHD
   
   But no order from 28/09/2026 to 04/10/2026 matched these. Reply with another month or dates (e.g. August, or 15 Sep to 10 Oct), or would you like me to escalate to customer service team?

[2] all products
-> Customer: HANLIM TRADING SDN BHD
   Product: all products
   Dates: 28/09/2026 to 04/10/2026
   
   Here are the orders I found.
   
   1. DO-OB-1003
   
   2. DO-OB-0929
   
   3. DO-OB-1001
   
   _Data last updated: 05/10/2026 18:38:14_

[3] all customers
-> Customer: all customers
   Product: all products
   Dates: 28/09/2026 to 04/10/2026
   
   Here are the orders I found.
   
   1. DO-OB-1003
   
   2. DO-OB-0929
   
   3. DO-OB-1001
   
   _Data last updated: 05/10/2026 18:38:14_

[4] any incoming for srtwc286-sh?
-> SRTWC286-SH: ✅ ETA 29/10/2026
   
   Please refer to your salesman.

[5] check stock srtwc286-sh-new-200 x 500
-> SRTWC286-SH-NEW-200 x 500: 🚫 the quantity is more than what I can confirm here. Please refer to your salesman.
.                                                                        [100%]
1 passed in 37.45s

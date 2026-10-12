# PLAN: HQ warehouse as a project reserve

Status: Plan (behaviour card ask pending owner answer). Track: standard (touches SCM planner, Stock Debt, chatbot stock, reorder; no migration expected).

Owner (12 Oct): HQ warehouse is a PROJECT RESERVE. CS put project stock at HQ first (no time
to link it in AutoCount, and BRW stock gets taken by dealers), then move it to the actual
project locations. HQ stock (on hand + incoming PO/SPO) belongs to projects in general, never
to dealers.

Target: a pool warehouse with `segment = project` is a project-only reserve.

1. Planner: any project line may draw it, 0% dealer share, kept out of the dealer side of pools_net.
2. Stock Debt: its on hand + incoming covers any project line, never POOL_GROUP dealer lines.
3. Chatbot / dealer stock and low-stock / reorder skip it.
4. No G12 unclaimed project-bin lock for a project-reserve pool.

Data step `HQ.segment = project` ships WITH the release (release post-step, owner runs prod data).

Details follow once the behaviour card is answered.

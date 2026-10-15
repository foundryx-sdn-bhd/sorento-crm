# PLAN: chat advisor spike ("Claude Code for Sorento"), lane CHAT-ADVISOR-SPIKE

Status: Plan. Track: spike (time-box 3 days, done by ~19 Oct 2026). NOT a product PR: deliverables
are a design page and a measured prototype on a draft PR. Owner approved 15 Oct ~22:15. LLM budget
about $30 total, spend reported.

## Owner's goal (15 Oct)

"I want to build a Claude Code for Sorento. It can counter propose, suggest, think - e.g. think about
alternative products, offer the promotion, engage with the user. Can we even do that with the current
architecture?" Reference: 7-layer agent architecture (1 channels, 2 perception, 3 orchestration and
reasoning, 4 memory, 5 knowledge/RAG, 6 tools/MCP, 7 guardrails + observability + response).

Owner question 15 Oct ~22:20: "as Claude Code, do you extract entities first before the LLM decides?"
Answer: no. Claude Code feeds the raw conversation plus tool descriptions to one model that
understands, decides and calls tools in a loop, looking facts up through tools rather than carrying
knowledge in the prompt. Hence variant C2 below.

## Variants compared

| Id | Shape |
| -- | ----- |
| A  | Today v1: LLM parser (layer 2) + code routing + templated replies |
| B  | PV2 as is |
| C  | HYBRID: thin parser (understanding only) -> deterministic fast path for simple asks (stock, price, ETA, order status); open-ended / selling turns -> layer-3 ADVISOR (reasoning LLM, bounded loop, 3-6 tools chosen from the parser's intent) + one structured conversation state (layer 4) + fact guard (layer 7) |
| C2 | Raw-conversation advisor (Claude Code style): no parser pre-extraction; the advisor reads the raw transcript, same tool subset, same fact guard |
| D  | Full agent: all tools, model chooses, no parser |

Design page covers A-D + C2. The spike measures A (v1) vs C vs C2 on two models, plus a C2-all-tools
run (D-shaped) if budget allows, since the owner's n8n lesson is "more tools = worse tool choice".

## Deliverable 1: design page (by 17 Oct evening)

`documentation/plans/chatbot/ARCH-advisor-16oct.html`, self-contained, phone-friendly, Lavish.
- Map v1 and PV2 onto the 7 layers with file:line.
- Per option: owner's examples, n8n failure modes (wrong tool, worse with more tools, slow), cost/turn,
  p50/p95, risk, migration path from today.
- 4-6 mock WhatsApp conversations built on REAL dev cases (masked names): alternative when out of
  stock, promotion offer, compatible accessory (seat cover / mirror / shelf), counter-proposal on qty or
  ETA, project from a chat or photo.
- Filed as `crew report --ask` with the repo path.

## Deliverable 2: spike (by ~19 Oct)

1. Scenarios: 25-30 "salesman moments" mined from REAL dev conversations (sorento_cagent_stack,
   read-only) and crew eval sets, plus 10 plain fast-path asks as control. Messages kept real, person
   names masked in anything committed. Expected behaviour per scenario cites owner-ruling memory files.
2. Prototype (dev only, behind a flag, never prod): advisor loop over the EXISTING MCP tools (no new
   data access paths), tool subset from the parser intent (C) or from the same subset table (C2),
   structured state in, fact guard out. Two models: a Claude model via the dev Anthropic key and the
   current OpenAI mini.
3. Measure per scenario: right tools chosen, made-up facts (target 0; fact guard + judge), owner-rule
   compliance, helpfulness (judge rubric), p50/p95 latency, tokens + cost/turn. Same scenarios on v1.
4. Report `crew/state/reports/CHAT-ADVISOR-SPIKE.md`: table per model + v1, failure examples,
   recommendation, production build size / migration / gates.

Tests: red-first does not apply to the throwaway prototype; the fact guard and the tool-subset
selection get unit tests.

## Steps

- [ ] S1 facts: map v1/PV2 code, MCP tool catalogue, crew eval assets (subagents, file:line).
- [ ] S2 design page draft -> `crew report --ask`.
- [ ] S3 scenario set (masked) committed under the spike dir.
- [ ] S4 prototype: fact guard + subset selection (unit tests), advisor loop C and C2, runner.
- [ ] S5 runs: v1, C x 2 models, C2 x 2 models; judge; numbers.
- [ ] S6 report + design page updated with measured numbers; draft PR.

## Open questions

- C2 tool menu: same subset as C (isolates the context variable) vs all tools (true Claude Code).
  Plan: run both if budget allows; same-subset is the primary C vs C2 comparison.

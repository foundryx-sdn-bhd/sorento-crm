import { numberFieldError } from '@/lib/number-field';
import type { RuleCondition, RuleFactItem, RuleGroup } from './types';

/** True when a number-typed literal in the tree holds text that cannot be read as a number. */
export function ruleTreeHasBadNumber(
  group: RuleGroup | null | undefined,
  facts: RuleFactItem[] | undefined,
): boolean {
  if (!group || !facts) return false;
  const numeric = new Set(facts.filter((f) => f.type === 'number').map((f) => f.key));
  const bad = (v: unknown) =>
    typeof v === 'number' ? !Number.isFinite(v) : typeof v === 'string' && v.trim() !== '' && numberFieldError(v) !== undefined;
  const walk = (node: RuleGroup | RuleCondition): boolean =>
    node.kind === 'group'
      ? node.rules.some(walk)
      : node.valueKind !== 'fact' &&
        numeric.has(node.fact) &&
        (Array.isArray(node.value) ? node.value.some(bad) : bad(node.value));
  return walk(group);
}

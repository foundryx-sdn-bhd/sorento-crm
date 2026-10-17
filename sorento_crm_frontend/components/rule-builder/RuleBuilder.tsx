'use client';

/**
 * RuleBuilder - reusable condition-tree builder over the whitelisted fact
 * registry (`GET /rule-facts`). Facts grouped by source in a searchable
 * select, operators per fact type, literal⇄fact cross-compare on scalar ops,
 * AND/OR groups nesting to depth 5, stale-fact warning.
 *
 * The public component takes `sources` and fetches the facts itself. `value`
 * seeds the draft on mount; remount (key) to load another tree. Emits the
 * serialized RuleGroup on every edit - null when empty (= unconditional).
 *
 * Ported from foundryx-shared-service, adapted to sorento's shared
 * SearchableSelect / SearchableMultiSelect (the repo's searchable-dropdown
 * standard - never `ui/select`).
 */
import { useMemo, useRef, useState, type ComponentProps, type ReactNode } from 'react';
import { AlertTriangle, ArrowLeftRight, Loader2, Plus, Trash2, Undo2 } from 'lucide-react';
import {
  CROSS_FACT_OPERATORS,
  RULE_MAX_DEPTH,
  RULE_OPERATORS,
  type RuleCondition,
  type RuleFactItem,
  type RuleGroup,
  type RuleOperator,
} from './types';
import { useRuleFacts } from './useRuleFacts';
import { cn } from '@/lib/utils';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { NumberInput } from '@/components/ui/number-input';
import { numberFieldError } from '@/lib/number-field';
import { ruleTreeHasBadNumber } from './ruleTreeBadNumber';
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import {
  SearchableSelect,
  type SearchableSelectOption,
} from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';

/**
 * Comma-separated free-text multi value. Keeps the RAW text locally and emits
 * the parsed array (a controlled join/split round-trip drops a trailing comma
 * every keystroke, blocking entry of a second value).
 */
function FreeFormMulti({
  selected,
  disabled,
  onChange,
}: {
  selected: string[];
  disabled: boolean;
  onChange: (value: string[]) => void;
}) {
  const [raw, setRaw] = useState(selected.join(', '));
  return (
    <Input
      className="h-8 w-48"
      placeholder="Comma-separated values"
      value={raw}
      disabled={disabled}
      onChange={(e) => {
        setRaw(e.target.value);
        onChange(
          e.target.value
            .split(',')
            .map((s) => s.trim())
            .filter(Boolean),
        );
      }}
    />
  );
}

/** Icon-button wrapper with an explanatory hover tooltip. */
function IconHint({ hint, children }: { hint: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent className="max-w-64">{hint}</TooltipContent>
    </Tooltip>
  );
}

// ---- Internal draft tree (stable React key per node) ----
interface DraftCondition {
  key: string;
  kind: 'condition';
  fact: string;
  operator: RuleOperator;
  valueKind: 'literal' | 'fact';
  value: unknown;
}
interface DraftGroup {
  key: string;
  kind: 'group';
  combinator: 'and' | 'or';
  rules: DraftRule[];
}
type DraftRule = DraftCondition | DraftGroup;

/** Which value widget an operator needs - value resets when the shape changes. */
type ValueShape = 'none' | 'single' | 'pair' | 'multi';

function shapeFor(operator: RuleOperator): ValueShape {
  if (operator === 'is_true' || operator === 'is_false') return 'none';
  if (operator === 'between') return 'pair';
  if (
    operator === 'in' ||
    operator === 'not_in' ||
    operator === 'contains_any' ||
    operator === 'contains_all' ||
    operator === 'not_contains'
  )
    return 'multi';
  return 'single';
}

function emptyValue(shape: ValueShape): unknown {
  if (shape === 'none') return null;
  if (shape === 'pair') return ['', ''];
  if (shape === 'multi') return [];
  return '';
}

/** Operators offered for a fact - the type table, narrowed by the fact's own
 * `operators` whitelist when the backend supplied one. */
function operatorsFor(fact: RuleFactItem | undefined): { value: RuleOperator; label: string }[] {
  const defs = RULE_OPERATORS[fact?.type ?? 'string'];
  if (!fact?.operators?.length) return defs;
  const allowed = new Set(fact.operators);
  const narrowed = defs.filter((o) => allowed.has(o.value));
  return narrowed.length ? narrowed : defs;
}

function toWire(rule: DraftRule): RuleGroup | RuleCondition {
  if (rule.kind === 'group') {
    return {
      kind: 'group',
      combinator: rule.combinator,
      rules: rule.rules.map(toWire),
    };
  }
  return {
    kind: 'condition',
    fact: rule.fact,
    operator: rule.operator,
    valueKind: rule.valueKind,
    value: rule.value,
  };
}

function collectStale(group: DraftGroup, known: Set<string>): string[] {
  return group.rules.flatMap((r) =>
    r.kind === 'group'
      ? collectStale(r, known)
      : known.has(r.fact)
        ? []
        : [r.fact],
  );
}

export interface RuleBuilderProps {
  /** Fact sources for this consumer's trigger (from the trigger spec). */
  sources: string[];
  /** Initial tree (null = unconditional). Remount to load a different one. */
  value: RuleGroup | null;
  /** Fires on every edit with the serialized tree - null when empty. */
  onChange: (group: RuleGroup | null) => void;
  /** Told after each edit whether a number box holds text that cannot be read as a number. */
  onBadNumberChange?: (bad: boolean) => void;
  disabled?: boolean;
}

/** Public component: fetches facts for `sources`, then renders the builder. */
export function RuleBuilder({ sources, value, onChange, onBadNumberChange, disabled = false }: RuleBuilderProps) {
  const { data: facts, isLoading, isError, error } = useRuleFacts(sources);

  if (sources.length === 0) return null;

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-lg border border-border p-3 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" /> Loading fields…
      </div>
    );
  }

  if (isError || !facts) {
    return (
      <div className="flex items-start gap-2 rounded-md border border-destructive/40 bg-destructive/5 p-2.5 text-xs text-destructive">
        <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
        <span>{error instanceof Error ? error.message : 'Could not load rule fields.'}</span>
      </div>
    );
  }

  if (facts.length === 0) {
    return (
      <p className="rounded-lg border border-border p-3 text-xs text-muted-foreground">
        This trigger has no filterable fields.
      </p>
    );
  }

  return <RuleBuilderInner facts={facts} value={value} onChange={onChange} onBadNumberChange={onBadNumberChange} disabled={disabled} />;
}

function RuleBuilderInner({
  facts,
  value,
  onChange,
  onBadNumberChange,
  disabled = false,
}: {
  facts: RuleFactItem[];
  value: RuleGroup | null;
  onChange: (group: RuleGroup | null) => void;
  onBadNumberChange?: (bad: boolean) => void;
  disabled?: boolean;
}) {
  const keyRef = useRef(0);
  const nextKey = () => `k${++keyRef.current}`;

  const fromWire = (rule: RuleGroup | RuleCondition): DraftRule =>
    rule.kind === 'group'
      ? {
          key: nextKey(),
          kind: 'group',
          combinator: rule.combinator,
          rules: rule.rules.map(fromWire),
        }
      : { key: nextKey(), ...rule };

  const [root, setRoot] = useState<DraftGroup>(() =>
    value
      ? (fromWire(value) as DraftGroup)
      : { key: nextKey(), kind: 'group', combinator: 'and', rules: [] },
  );

  const factByKey = useMemo(() => new Map(facts.map((f) => [f.key, f])), [facts]);
  // Fact-picker options, grouped by source label.
  const factOptions = useMemo<SearchableSelectOption[]>(
    () => facts.map((f) => ({ value: f.key, label: f.label, group: f.sourceLabel })),
    [facts],
  );
  const stale = collectStale(root, new Set(factByKey.keys()));

  const newCondition = (): DraftCondition => {
    const first = facts[0];
    const operator = operatorsFor(first)[0].value;
    return {
      key: nextKey(),
      kind: 'condition',
      fact: first?.key ?? '',
      operator,
      valueKind: 'literal',
      value: emptyValue(shapeFor(operator)),
    };
  };

  const update = (next: DraftGroup) => {
    setRoot(next);
    const wire = next.rules.length === 0 ? null : (toWire(next) as RuleGroup);
    onBadNumberChange?.(ruleTreeHasBadNumber(wire, facts));
    onChange(wire);
  };

  return (
    <div className="flex flex-col gap-2">
      {stale.length > 0 && (
        <div className="flex items-start gap-2 rounded-md border border-[var(--color-warning-soft,var(--color-yellow-200))] bg-[var(--color-warning-soft,var(--color-yellow-50))] p-2.5 text-xs text-[var(--color-warning-accent,var(--color-yellow-700))]">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            Some conditions reference fields that no longer exist - fix or remove them before
            saving.
          </span>
        </div>
      )}
      <GroupEditor
        group={root}
        depth={0}
        facts={facts}
        factByKey={factByKey}
        factOptions={factOptions}
        newCondition={newCondition}
        nextKey={nextKey}
        disabled={disabled}
        onChange={update}
      />
    </div>
  );
}

interface GroupEditorProps {
  group: DraftGroup;
  depth: number;
  facts: RuleFactItem[];
  factByKey: Map<string, RuleFactItem>;
  factOptions: SearchableSelectOption[];
  newCondition: () => DraftCondition;
  nextKey: () => string;
  disabled: boolean;
  onChange: (group: DraftGroup) => void;
  /** Removes THIS group from its parent - absent on the root. */
  onRemove?: () => void;
}

function GroupEditor({
  group,
  depth,
  facts,
  factByKey,
  factOptions,
  newCondition,
  nextKey,
  disabled,
  onChange,
  onRemove,
}: GroupEditorProps) {
  function updateRule(key: string, next: DraftRule | null) {
    onChange({
      ...group,
      rules: group.rules.flatMap((r) => (r.key === key ? (next ? [next] : []) : [r])),
    });
  }

  return (
    <div
      className={cn(
        'flex flex-col gap-2 rounded-lg border border-border p-3',
        depth > 0 && 'bg-muted/40',
      )}
    >
      {(group.rules.length > 1 || depth > 0) && (
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">Match</span>
          <ToggleGroup
            type="single"
            variant="outline"
            size="sm"
            value={group.combinator}
            onValueChange={(v) =>
              !disabled && v && onChange({ ...group, combinator: v as 'and' | 'or' })
            }
          >
            <ToggleGroupItem value="and" disabled={disabled}>
              AND
            </ToggleGroupItem>
            <ToggleGroupItem value="or" disabled={disabled}>
              OR
            </ToggleGroupItem>
          </ToggleGroup>
        </div>
      )}

      {group.rules.length === 0 && depth === 0 && (
        <p className="text-xs text-muted-foreground">
          No conditions - always matches{disabled ? '.' : ' (add one below).'}
        </p>
      )}

      <div className="flex flex-col gap-2">
        {group.rules.map((rule) =>
          rule.kind === 'condition' ? (
            <ConditionRow
              key={rule.key}
              condition={rule}
              facts={facts}
              factByKey={factByKey}
              factOptions={factOptions}
              disabled={disabled}
              onChange={(next) => updateRule(rule.key, next)}
              onRemove={() => updateRule(rule.key, null)}
            />
          ) : (
            <GroupEditor
              key={rule.key}
              group={rule}
              depth={depth + 1}
              facts={facts}
              factByKey={factByKey}
              factOptions={factOptions}
              newCondition={newCondition}
              nextKey={nextKey}
              disabled={disabled}
              onChange={(next) => updateRule(rule.key, next)}
              onRemove={() => updateRule(rule.key, null)}
            />
          ),
        )}
      </div>

      {!disabled && (
        <div className="flex items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onChange({ ...group, rules: [...group.rules, newCondition()] })}
          >
            <Plus /> Add condition
          </Button>
          {depth < RULE_MAX_DEPTH - 1 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() =>
                onChange({
                  ...group,
                  rules: [
                    ...group.rules,
                    {
                      key: nextKey(),
                      kind: 'group',
                      combinator: 'and',
                      rules: [newCondition()],
                    },
                  ],
                })
              }
            >
              <Plus /> Add group
            </Button>
          )}
          {onRemove && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              mode="icon"
              className="ms-auto text-muted-foreground"
              aria-label="Remove group"
              onClick={onRemove}
            >
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

interface ConditionRowProps {
  condition: DraftCondition;
  facts: RuleFactItem[];
  factByKey: Map<string, RuleFactItem>;
  factOptions: SearchableSelectOption[];
  disabled: boolean;
  onChange: (next: DraftCondition) => void;
  onRemove: () => void;
}

function ConditionRow({
  condition,
  facts,
  factByKey,
  factOptions,
  disabled,
  onChange,
  onRemove,
}: ConditionRowProps) {
  const fact = factByKey.get(condition.fact);
  const type = fact?.type ?? 'string';
  const operators = operatorsFor(fact);
  const crossable = CROSS_FACT_OPERATORS.has(condition.operator);

  const changeFact = (key: string) => {
    const next = factByKey.get(key);
    const operator = operatorsFor(next)[0].value;
    onChange({
      ...condition,
      fact: key,
      operator,
      valueKind: 'literal',
      value: emptyValue(shapeFor(operator)),
    });
  };

  const changeOperator = (op: string) => {
    const operator = op as RuleOperator;
    const sameShape = shapeFor(operator) === shapeFor(condition.operator);
    const keepFactMode =
      condition.valueKind === 'fact' && CROSS_FACT_OPERATORS.has(operator);
    onChange({
      ...condition,
      operator,
      valueKind: keepFactMode ? 'fact' : 'literal',
      value: keepFactMode || sameShape ? condition.value : emptyValue(shapeFor(operator)),
    });
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {fact ? (
        <div className="w-44">
          <SearchableSelect
            size="sm"
            options={factOptions}
            value={condition.fact}
            onChange={changeFact}
            placeholder="Field"
            disabled={disabled}
          />
        </div>
      ) : (
        <Badge variant="warning" appearance="light" className="h-8 px-2.5">
          <AlertTriangle className="size-3" /> Unknown field: {condition.fact}
        </Badge>
      )}

      {fact && (
        <div className="w-36">
          <SearchableSelect
            size="sm"
            options={operators.map((o) => ({ value: o.value, label: o.label }))}
            value={condition.operator}
            onChange={changeOperator}
            placeholder="Operator"
            disabled={disabled}
          />
        </div>
      )}

      {fact && condition.valueKind === 'fact' ? (
        <div className="w-44">
          <SearchableSelect
            size="sm"
            options={facts
              .filter((f) => f.type === type && f.key !== condition.fact)
              .map((f) => ({ value: f.key, label: f.label }))}
            value={typeof condition.value === 'string' ? condition.value : ''}
            onChange={(v) => onChange({ ...condition, value: v })}
            placeholder="Pick a field…"
            disabled={disabled}
          />
        </div>
      ) : (
        fact && (
          <ConditionValue
            condition={condition}
            fact={fact}
            disabled={disabled}
            onChange={onChange}
          />
        )
      )}

      {fact && crossable && !disabled && (
        <IconHint
          hint={
            condition.valueKind === 'fact'
              ? 'Switch back to typing a fixed value'
              : 'Compare with another field instead of a fixed value'
          }
        >
          <Button
            type="button"
            variant="ghost"
            size="sm"
            mode="icon"
            aria-label={
              condition.valueKind === 'fact' ? 'Use literal value' : 'Compare to field'
            }
            onClick={() =>
              onChange({
                ...condition,
                valueKind: condition.valueKind === 'fact' ? 'literal' : 'fact',
                value:
                  condition.valueKind === 'fact'
                    ? emptyValue(shapeFor(condition.operator))
                    : null,
              })
            }
          >
            {condition.valueKind === 'fact' ? (
              <Undo2 className="size-3.5" />
            ) : (
              <ArrowLeftRight className="size-3.5" />
            )}
          </Button>
        </IconHint>
      )}

      {!disabled && (
        <Button
          type="button"
          variant="ghost"
          size="sm"
          mode="icon"
          aria-label="Remove condition"
          onClick={onRemove}
        >
          <Trash2 className="size-3.5 text-muted-foreground" />
        </Button>
      )}
    </div>
  );
}

/** A condition's typed value: the shared number field for numbers, a plain input otherwise. */
function ValueInput({
  inputType,
  error,
  ...props
}: { inputType: 'number' | 'date' | 'text'; error?: string } & Omit<ComponentProps<typeof Input>, 'type'>) {
  return inputType === 'number' ? (
    <NumberInput {...props} error={error} errorClassName="w-full" value={Array.isArray(props.value) ? undefined : (props.value as string | number | undefined)} />
  ) : (
    <Input type={inputType} {...props} />
  );
}

/** A typed number bound that cannot be read (bad input) says so on its own box. */
function memberError(inputType: 'number' | 'date' | 'text', member: string | undefined): string | undefined {
  if (inputType !== 'number' || !member || !member.trim()) return undefined;
  return numberFieldError(member);
}

function ConditionValue({
  condition,
  fact,
  disabled,
  onChange,
}: {
  condition: DraftCondition;
  fact: RuleFactItem;
  disabled: boolean;
  onChange: (next: DraftCondition) => void;
}) {
  const shape = shapeFor(condition.operator);
  if (shape === 'none') return null;

  const inputType =
    fact.type === 'number' ? 'number' : fact.type === 'date' ? 'date' : 'text';

  if (shape === 'pair') {
    const v = Array.isArray(condition.value) ? (condition.value as string[]) : ['', ''];
    return (
      <div className="flex flex-wrap items-center gap-1">
        <ValueInput
          inputType={inputType}
          className="h-8 w-32"
          value={v[0] ?? ''}
          error={memberError(inputType, v[0])}
          disabled={disabled}
          onChange={(e) => onChange({ ...condition, value: [e.target.value, v[1] ?? ''] })}
        />
        <span className="text-xs text-muted-foreground">and</span>
        <ValueInput
          inputType={inputType}
          className="h-8 w-32"
          value={v[1] ?? ''}
          error={memberError(inputType, v[1])}
          disabled={disabled}
          onChange={(e) => onChange({ ...condition, value: [v[0] ?? '', e.target.value] })}
        />
      </div>
    );
  }

  if (shape === 'multi') {
    const selected = Array.isArray(condition.value) ? (condition.value as string[]) : [];
    if (fact.options?.length) {
      return (
        <div className="min-w-44 flex-1">
          <SearchableMultiSelect
            size="sm"
            options={fact.options}
            value={selected}
            onChange={(value) => onChange({ ...condition, value })}
            placeholder="Select…"
            disabled={disabled}
          />
        </div>
      );
    }
    // Free-form multi (string in/not_in without options) - comma-separated.
    return (
      <FreeFormMulti
        selected={selected}
        disabled={disabled}
        onChange={(value) => onChange({ ...condition, value })}
      />
    );
  }

  // single
  if (fact.type === 'enum' && fact.options?.length) {
    return (
      <div className="w-40">
        <SearchableSelect
          size="sm"
          options={fact.options}
          value={typeof condition.value === 'string' ? condition.value : ''}
          onChange={(value) => onChange({ ...condition, value })}
          placeholder="Select…"
          disabled={disabled}
        />
      </div>
    );
  }
  if (fact.type === 'boolean') return null;

  const singleValue =
    typeof condition.value === 'string' || typeof condition.value === 'number'
      ? String(condition.value)
      : '';
  return (
    <ValueInput
      inputType={inputType}
      className={cn('h-8', inputType === 'date' ? 'w-36' : 'w-40')}
      placeholder="Value"
      value={singleValue}
      error={memberError(inputType, singleValue)}
      disabled={disabled}
      onChange={(e) =>
        onChange({
          ...condition,
          value:
            inputType === 'number' && e.target.value !== '' && e.target.value !== 'NaN'
              ? Number(e.target.value)
              : e.target.value,
        })
      }
    />
  );
}

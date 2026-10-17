/**
 * NUMBER-FIELDS census (UAC3.1, UAC3.2). A property of the whole tree, which a render test
 * cannot speak for (same reasoning as `app/(protected)/scm/loading-plan/no-confirm-dialog-imports.test.ts`).
 *
 * Every number field goes through `NumberInput` (components/ui/number-input.tsx), so a raw
 * number input, or a change handler that rewrites what was typed (a fallback or a clamp),
 * is a defect. The offender list this prints IS the migration checklist.
 *
 * The second `it` runs each rule over fixture strings so the scan can never pass vacuously.
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, it, expect } from 'vitest';

const ROOT = path.resolve(__dirname, '..', '..');
const DIRS = ['app', 'components', 'lib', 'hooks', 'modules'];
const SHARED_COMPONENT = path.join(ROOT, 'components', 'ui', 'number-input.tsx');
const SKIP_DIRS = new Set(['node_modules', '.next']);

type Rule = {
  name: string;
  /** Returns the 0-based line indexes that offend. */
  find: (lines: string[]) => number[];
};

const RAW_NUMBER_TYPE = /\btype=(?:"number"|\{[^}]{0,200}['"]number['"])/;
const REWRITE_FALLBACK = /\b(?:Number|parseInt|parseFloat)\(\s*\w+(?:\.\w+)*\.target\.value[^)]*\)\s*\|\|/;
const EMPTY_RETURN = /if\s*\(\s*\w+\.target\.value\s*===\s*(['"])\1\s*\)\s*return/;
const MATH_CLAMP = /Math\.(?:max|min)\(/;
const TYPED_VALUE = /\.target\.value\b|\.target\.valueAsNumber\b|\.valueAsNumber\b/;
const ON_VALUE_CHANGE = /onValueChange=\{/g;
const REWRITE_IN_HANDLER = /Math\.(?:max|min)\(|\?\?\s*\d|\|\|\s*\d/;

const lineRule = (re: RegExp) => (lines: string[]) =>
  lines.flatMap((l, i) => (re.test(l) ? [i] : []));

const RULES: Rule[] = [
  { name: 'raw type="number" input (use NumberInput)', find: lineRule(RAW_NUMBER_TYPE) },
  {
    name: 'raw type={...number...} input (use NumberInput)',
    // A multi-line `type={ cond ? 'number' : 'text' }` that the per-line rule cannot see.
    find: (lines) => {
      const text = lines.join('\n');
      const out: number[] = [];
      for (const m of text.matchAll(/\btype=\{[^}]{0,200}['"]number['"]/g)) {
        out.push(text.slice(0, m.index).split('\n').length - 1);
      }
      return out;
    },
  },
  { name: 'change handler rewrites a typed number with a fallback (|| n)', find: lineRule(REWRITE_FALLBACK) },
  {
    name: 'change handler clamps a typed number (Math.max/Math.min on the typed value)',
    find: (lines) =>
      lines.flatMap((l, i) => {
        if (!TYPED_VALUE.test(l) && !MATH_CLAMP.test(l)) return [];
        const window = lines.slice(Math.max(0, i - 1), i + 2).join('\n');
        return MATH_CLAMP.test(window) && TYPED_VALUE.test(window) && MATH_CLAMP.test(l) ? [i] : [];
      }),
  },
  { name: "change handler ignores an empty value (if (x.target.value === '') return)", find: lineRule(EMPTY_RETURN) },
  {
    name: 'onValueChange handler rewrites the value (Math.max/Math.min, ?? n, || n)',
    find: (lines) => {
      const text = lines.join('\n');
      const out: number[] = [];
      for (const m of text.matchAll(ON_VALUE_CHANGE)) {
        const start = (m.index ?? 0) + m[0].length;
        if (REWRITE_IN_HANDLER.test(text.slice(start, start + 200))) {
          out.push(text.slice(0, m.index).split('\n').length - 1);
        }
      }
      return out;
    },
  },
];

function sourceFiles(dir: string): string[] {
  const out: string[] = [];
  const walk = (d: string) => {
    if (!fs.existsSync(d)) return;
    for (const entry of fs.readdirSync(d, { withFileTypes: true })) {
      const full = path.join(d, entry.name);
      if (entry.isDirectory()) {
        if (!SKIP_DIRS.has(entry.name)) walk(full);
      } else if (
        (entry.name.endsWith('.tsx') || entry.name.endsWith('.ts')) &&
        !entry.name.includes('.test.') &&
        full !== SHARED_COMPONENT
      ) {
        out.push(full);
      }
    }
  };
  walk(dir);
  return out;
}

describe('NUMBER-FIELDS census: no raw number input, no rewriting handler', () => {
  it('finds no offender anywhere in app/, components/, lib/, hooks/, modules/', () => {
    const offenders: string[] = [];
    for (const d of DIRS) {
      for (const file of sourceFiles(path.join(ROOT, d))) {
        const lines = fs.readFileSync(file, 'utf8').split('\n');
        for (const rule of RULES) {
          for (const i of new Set(rule.find(lines))) {
            offenders.push(`${path.relative(ROOT, file)}:${i + 1}: ${rule.name}`);
          }
        }
      }
    }
    expect(offenders).toEqual([]);
  });

  describe('the rules are not vacuous', () => {
    const hits = (rule: Rule, src: string) => rule.find(src.split('\n')).length;
    const byName = (fragment: string) => RULES.filter((r) => r.name.includes(fragment));

    it.each([
      ['type="number"', '<Input type="number" min={0} />', '<Input type="text" />'],
      ["type={'number'}", "<Input type={'number'} />", "<Input type={'text'} />"],
      ['type={"number"}', '<Input type={"number"} />', '<Input type={"text"} />'],
      [
        'ternary yielding number',
        "<Input type={numeric ? 'number' : 'text'} />",
        "<Input type={numeric ? 'tel' : 'text'} />",
      ],
    ])('(a) raw input: %s', (_label, positive, negative) => {
      const rules = RULES.filter((r) => r.name.startsWith('raw type'));
      expect(rules.some((r) => hits(r, positive) > 0)).toBe(true);
      expect(rules.every((r) => hits(r, negative) === 0)).toBe(true);
    });

    it('(a) multi-line ternary type is caught', () => {
      const src = ['<Input', '  type={', "    numeric ? 'number' : 'text'", '  }', '/>'].join('\n');
      expect(RULES.filter((r) => r.name.startsWith('raw type')).some((r) => hits(r, src) > 0)).toBe(true);
    });

    it('(b) fallback rewrite: Number/parseInt/parseFloat(x.target.value) ||', () => {
      const [rule] = byName('fallback');
      expect(hits(rule, 'onChange={(e) => set(Number(e.target.value) || 0)}')).toBe(1);
      expect(hits(rule, 'onChange={(e) => set(parseInt(e.target.value, 10) || 1)}')).toBe(1);
      expect(hits(rule, 'onChange={(e) => set(parseFloat(e.target.value) || 0)}')).toBe(1);
      expect(hits(rule, 'onChange={(e) => set(e.target.value)}')).toBe(0);
      expect(hits(rule, 'const n = Number(price) || 0;')).toBe(0);
    });

    it('(b) clamp: Math.max/Math.min on the typed value', () => {
      const [rule] = byName('clamps');
      expect(hits(rule, 'set(Math.max(0, Number(e.target.value)))')).toBe(1);
      expect(hits(rule, 'set(Math.min(max, e.target.valueAsNumber))')).toBe(1);
      expect(hits(rule, 'const total = Math.max(a, b);')).toBe(0);
      expect(hits(rule, 'set(e.target.value)')).toBe(0);
    });

    it("(b) empty guard: if (x.target.value === '') return", () => {
      const [rule] = byName('ignores an empty');
      expect(hits(rule, "  if (e.target.value === '') return;")).toBe(1);
      expect(hits(rule, "  if (name === '') return;")).toBe(0);
    });

    it('(c) onValueChange handlers that rewrite the value', () => {
      const [rule] = byName('onValueChange');
      expect(hits(rule, 'onValueChange={(v) => set(Math.max(1, v ?? 1))}')).toBe(1);
      expect(hits(rule, 'onValueChange={(v) => set(Math.min(v, 9))}')).toBe(1);
      expect(hits(rule, 'onValueChange={(v) => set(v ?? 0)}')).toBe(1);
      expect(hits(rule, 'onValueChange={(v) => set(v || 1)}')).toBe(1);
      expect(hits(rule, 'onValueChange={(v) => set(v)}')).toBe(0);
      expect(hits(rule, 'onValueChange={setQty}')).toBe(0);
    });
  });
});

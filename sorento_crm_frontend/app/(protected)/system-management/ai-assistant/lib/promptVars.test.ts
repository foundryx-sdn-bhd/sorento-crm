import { describe, expect, it } from 'vitest';
import { tokensInTemplate, validateVars } from './promptVars';

describe('promptVars', () => {
  it('extracts unique tokens', () => {
    expect(tokensInTemplate('a {{x}} b {{y}} c {{x}}')).toEqual(['x', 'y']);
  });

  it('tolerates whitespace inside braces', () => {
    expect(tokensInTemplate('{{ current_date }}')).toEqual(['current_date']);
  });

  it('flags a present declared var green (present, not missing)', () => {
    const v = validateVars('hi {{current_date}}', ['current_date']);
    expect(v.present).toEqual(['current_date']);
    expect(v.missing).toEqual([]);
    expect(v.unknown).toEqual([]);
  });

  it('soft-warns when a declared var is missing from the template', () => {
    const v = validateVars('no vars here', ['current_date']);
    expect(v.missing).toEqual(['current_date']);
    expect(v.unknown).toEqual([]);
  });

  it('hard-flags an unknown token not declared for the key', () => {
    const v = validateVars('hi {{bogus}}', ['current_date']);
    expect(v.unknown).toEqual(['bogus']);
    expect(v.missing).toEqual(['current_date']);
  });
});

describe('validateVars with registry variables (PROMPT-DYNAMIC)', () => {
  it('treats registry tokens as known and never as missing', () => {
    const v = validateVars('x {{domains}} {{current_date}}', ['current_date'], ['domains', 'statuses']);
    expect(v.unknown).toEqual([]);
    expect(v.missing).toEqual([]);
  });

  it('still flags a token that is neither declared nor a registry variable', () => {
    expect(validateVars('{{nope}} {{current_date}}', ['current_date'], ['domains']).unknown).toEqual(['nope']);
  });
});

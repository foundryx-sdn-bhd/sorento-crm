import { describe, expect, it } from 'vitest';
import {
  isProjectLinkError,
  projectSuggestionQuery,
  PROJECT_LINK_NOT_FOUND,
} from './project-link';

describe('isProjectLinkError', () => {
  it('recognises the coded refusal', () => {
    const e = Object.assign(new Error('x'), { code: PROJECT_LINK_NOT_FOUND });
    expect(isProjectLinkError(e)).toBe(true);
  });
  it('falls back to the message', () => {
    expect(isProjectLinkError(new Error('That project was not found.'))).toBe(true);
  });
  it('ignores other errors', () => {
    expect(isProjectLinkError(new Error('Server error'))).toBe(false);
    expect(isProjectLinkError(null)).toBe(false);
  });
});

describe('projectSuggestionQuery (AC-FP-14)', () => {
  it('suggests the free-text name only while nothing is linked', () => {
    expect(projectSuggestionQuery(null, '  Damai Residences ')).toBe('Damai Residences');
    expect(projectSuggestionQuery('p-1', 'Damai Residences')).toBeUndefined();
  });
  it('never suggests a blank', () => {
    expect(projectSuggestionQuery(null, '   ')).toBeUndefined();
    expect(projectSuggestionQuery('', null)).toBeUndefined();
  });
});

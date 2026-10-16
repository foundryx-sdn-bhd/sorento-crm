/**
 * The Plan action's gating, as a value rather than a rendered menu item.
 *
 * The sentences it refuses with are the part worth pinning: they are what a user reads
 * when the board will not open, so "disabled" on its own would be exactly the dead click
 * this action was moved out of the bulk strip to stop being.
 */
import { describe, it, expect, vi } from 'vitest';
import { buildPlanActions } from './planActions';

const handlers = { onPlan: vi.fn() };

describe('buildPlanActions', () => {
  it('is offered with an empty selection, disabled, saying what it wants', () => {
    // In the bulk strip this action did not exist until rows were ticked, so nobody who
    // had not already found it could learn it was there.
    const [action] = buildPlanActions({ selectedCount: 0, canPlan: true, max: 50 }, handlers);

    expect(action.label).toBe('Plan selected (0)');
    expect(action.disabled).toBe(true);
    expect(action.disabledReason).toContain('Tick the sales orders');
  });

  it('names what the calling list holds in its refusal (CONSIGN-PLAN)', () => {
    const [action] = buildPlanActions(
      { selectedCount: 0, canPlan: true, max: 50, noun: 'consignments' },
      handlers,
    );

    expect(action.disabledReason).toBe('Tick the consignments to plan first.');
  });

  it('offers nothing without the permission the board itself requires', () => {
    // Hidden, not disabled: a door that answers 403 is worse than no door.
    expect(buildPlanActions({ selectedCount: 3, canPlan: false, max: 50 }, handlers)).toEqual([]);
  });

  it('counts the selection in the label', () => {
    const [one] = buildPlanActions({ selectedCount: 1, canPlan: true, max: 50 }, handlers);
    const [many] = buildPlanActions({ selectedCount: 7, canPlan: true, max: 50 }, handlers);

    expect(one.label).toBe('Plan selected (1)');
    expect(one.disabled).toBe(false);
    expect(many.label).toBe('Plan selected (7)');
    expect(many.disabled).toBe(false);
  });

  it('disables at more than the board holds, and names both figures', () => {
    const [action] = buildPlanActions({ selectedCount: 51, canPlan: true, max: 50 }, handlers);

    expect(action.disabled).toBe(true);
    expect(action.disabledReason).toContain('up to 50');
    expect(action.disabledReason).toContain('51 are selected');
  });

  it('allows exactly the bound', () => {
    const [action] = buildPlanActions({ selectedCount: 50, canPlan: true, max: 50 }, handlers);

    expect(action.disabled).toBe(false);
    expect(action.disabledReason).toBeUndefined();
  });
});

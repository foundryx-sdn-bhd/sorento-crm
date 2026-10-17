import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { WorkflowHeaderFieldsForm, workflowNumbersInvalid } from './WorkflowDynamicFields';
import type { WorkflowFormSchema } from '../types/workflowForms.types';

const schema = {
  header_fields: [{ id: 'qty', type: 'number', label: 'Quantity', required: true }],
  line_groups: [],
  states: [],
  transitions: [],
  notification_rules: [],
} as unknown as WorkflowFormSchema;

describe('WorkflowDynamicFields required number (S10)', () => {
  it('a blank required number is invalid, a figure is not', () => {
    expect(workflowNumbersInvalid(schema, {})).toBe(true);
    expect(workflowNumbersInvalid(schema, { qty: null })).toBe(true);
    expect(workflowNumbersInvalid(schema, { qty: 4 })).toBe(false);
  });

  it('shows the sentence and aria-invalid on the blank required box once errors are on', () => {
    render(<WorkflowHeaderFieldsForm schema={schema} data={{}} onChange={vi.fn()} showNumberErrors />);
    expect(screen.getByText('Enter a number.')).toBeVisible();
    expect(screen.getByRole('spinbutton')).toHaveAttribute('aria-invalid', 'true');
  });
});

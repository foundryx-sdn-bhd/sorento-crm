/**
 * NUMBER-FIELDS: Save is enabled on a blank measurement; pressing it says why and saves nothing.
 */
import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

import { ProposalValueEditor } from './ProposalValueEditor';

describe('ProposalValueEditor - blank numeric value', () => {
  it('keeps Save enabled, shows the message on press and does not save', () => {
    const onSave = vi.fn();
    render(
      <ProposalValueEditor
        label="Width"
        dataType="numeric"
        draft=""
        onDraftChange={() => {}}
        onSave={onSave}
        onCancel={() => {}}
      />,
    );

    const save = screen.getByRole('button', { name: 'Save Width' });
    expect(save).not.toBeDisabled();
    fireEvent.click(save);

    expect(screen.getByText('Enter a number.')).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
  });
});

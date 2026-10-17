import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ProjectLinkValue } from './ProjectLinkValue';

describe('ProjectLinkValue (AC-FP-30)', () => {
  it('links CODE - Title to the project', () => {
    render(<ProjectLinkValue projectId="p-1" projectLabel="PRJ-001 - Damai Residences" />);
    const link = screen.getByRole('link', { name: 'PRJ-001 - Damai Residences' });
    expect(link.getAttribute('href')).toBe('/project-sales/p-1');
  });
  it('says No project when unlinked', () => {
    render(<ProjectLinkValue projectId={null} projectLabel={null} />);
    expect(screen.getByText('No project')).toBeTruthy();
    expect(screen.queryByRole('link')).toBeNull();
  });
  it('does not claim No project when a link exists but its label is hidden', () => {
    render(<ProjectLinkValue projectId="p-1" projectLabel={null} />);
    expect(screen.getByText('Linked project not visible')).toBeTruthy();
    expect(screen.queryByText('No project')).toBeNull();
    expect(screen.queryByRole('link')).toBeNull();
  });
});

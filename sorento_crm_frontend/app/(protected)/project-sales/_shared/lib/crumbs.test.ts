import { describe, expect, it } from 'vitest';
import { projectCrumbs, registerProjectCrumbs } from './crumbs';

// Owner, 13 Oct: the list a project sits in is "Projects", never "Pipeline".
describe('project crumbs', () => {
  it('name the list Projects', () => {
    expect(projectCrumbs('p1')[1]).toEqual({ title: 'Projects', path: '/project-sales/pipeline' });
    expect(registerProjectCrumbs()[1]).toEqual({
      title: 'Projects',
      path: '/project-sales/pipeline',
    });
  });
});

// QUOTE-APPROVAL-UX G2: a quotation page's "Project" crumb lands on the Quotations tab; every
// other project sub-page keeps the plain project path.
describe('project crumbs: quotations tab (G2)', () => {
  // The option is the new, documented shape `projectCrumbs(id, { tab: 'quotations' }, ...below)`.
  const withOptions = projectCrumbs as unknown as (
    ...args: unknown[]
  ) => ReturnType<typeof projectCrumbs>;

  it('G2: the Project crumb links to the Quotations tab when asked', () => {
    const crumbs = withOptions('p1', { tab: 'quotations' }, { title: 'SRT/Q/2026/0141' });
    expect(crumbs[2]).toEqual({ title: 'Project', path: '/project-sales/p1?tab=quotations' });
    expect(crumbs[3]).toEqual({ title: 'SRT/Q/2026/0141' });
    expect(crumbs).toHaveLength(4);
  });

  it('G2: by default the Project crumb still links /project-sales/<id>', () => {
    const crumbs = projectCrumbs('p1', { title: 'Purchase orders' });
    expect(crumbs[2]).toEqual({ title: 'Project', path: '/project-sales/p1' });
    expect(crumbs[3]).toEqual({ title: 'Purchase orders' });
  });
});

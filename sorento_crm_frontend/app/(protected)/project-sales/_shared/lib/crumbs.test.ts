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

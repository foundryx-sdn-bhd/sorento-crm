import type { PageHeaderCrumb } from '@/components/common/PageHeader';

/**
 * The trail for a screen the sidebar does not name.
 *
 * Everything under `/project-sales/[projectId]` is a record inside a project,
 * and the sidebar stops at Projects. Derived from the pathname alone the trail
 * would collapse to "Dashboards > Sales Order", which says neither which
 * project nor how to get back to it, so these pages pass their own.
 *
 * The wording matches the sidebar's, per S5-02: the group is "Project Sales"
 * and its page is "Projects". The project's own crumb reads "Project" rather
 * than an id, because no UUID renders in the UI and the project's name is not
 * known to the server component that draws the header.
 */
export function projectCrumbs(
  projectId: string,
  ...below: PageHeaderCrumb[]
): PageHeaderCrumb[] {
  return [
    { title: 'Project Sales' },
    { title: 'Projects', path: '/project-sales/pipeline' },
    { title: 'Project', path: `/project-sales/${projectId}` },
    ...below,
  ];
}

/** The trail for `/project-sales/new`, off the pipeline rather than a project record. */
export function registerProjectCrumbs(): PageHeaderCrumb[] {
  return [
    { title: 'Project Sales' },
    { title: 'Projects', path: '/project-sales/pipeline' },
    { title: 'Register a project' },
  ];
}

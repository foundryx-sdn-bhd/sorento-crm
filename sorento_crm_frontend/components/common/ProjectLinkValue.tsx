import Link from 'next/link';

interface ProjectLinkValueProps {
  projectId?: string | null;
  /** `CODE - Title`, as the server sends it. */
  projectLabel?: string | null;
  className?: string;
}

/** A form's linked project as `CODE - Title`, a link to the project; "No project" when empty. */
export function ProjectLinkValue({ projectId, projectLabel, className }: ProjectLinkValueProps) {
  if (!projectId) {
    return <span className={className ?? 'text-muted-foreground'}>No project</span>;
  }
  // Linked, but the viewer's companies do not include it, so there is no label to show.
  if (!projectLabel) {
    return (
      <span className={className ?? 'text-muted-foreground'}>Linked project not visible</span>
    );
  }
  return (
    <Link
      href={`/project-sales/${projectId}`}
      className={className ?? 'text-primary hover:underline break-words'}
    >
      {projectLabel}
    </Link>
  );
}

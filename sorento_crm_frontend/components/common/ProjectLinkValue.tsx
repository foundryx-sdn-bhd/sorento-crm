import Link from 'next/link';

interface ProjectLinkValueProps {
  projectId?: string | null;
  /** `CODE - Title`, as the server sends it. */
  projectLabel?: string | null;
  className?: string;
}

/** A form's linked project as `CODE - Title`, a link to the project; "No project" when empty. */
export function ProjectLinkValue({ projectId, projectLabel, className }: ProjectLinkValueProps) {
  if (!projectId || !projectLabel) {
    return <span className={className ?? 'text-muted-foreground'}>No project</span>;
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

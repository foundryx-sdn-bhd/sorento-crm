import { Badge, BadgeDot } from '@/components/ui/badge';
import type { IdeaStatusColor } from '@/types/ideas';

type Variant = 'secondary' | 'info' | 'primary' | 'warning' | 'success' | 'destructive';

/** ss's tenant-editable colour keys (`gray`, `blue`, `amber`, ...) onto the CRM status pill variants. */
const VARIANT: Record<string, Variant> = {
  gray: 'secondary',
  grey: 'secondary',
  blue: 'info',
  info: 'info',
  indigo: 'primary',
  primary: 'primary',
  violet: 'info',
  amber: 'warning',
  yellow: 'warning',
  orange: 'warning',
  warning: 'warning',
  green: 'success',
  success: 'success',
  red: 'destructive',
  destructive: 'destructive',
};

/** The status pill. The label is the tenant's own wording, never a hard-coded one. */
export function IdeaStatusBadge({ label, color }: { label: string; color: IdeaStatusColor }) {
  return (
    <Badge variant={VARIANT[color.toLowerCase()] ?? 'secondary'} appearance="light">
      <BadgeDot />
      {label}
    </Badge>
  );
}

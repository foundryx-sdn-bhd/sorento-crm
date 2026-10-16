'use client';

import { Badge } from '@/components/ui/badge';
import type { RespondContact } from '../types/contact.types';

/** The Tag settings column of the contacts list: Matches, "Differs: n" (labels in the title), or a dash. */
export default function ContactTagSettingsCell({
  contact,
}: {
  contact: Pick<RespondContact, 'tag_settings'>;
}) {
  const ts = contact.tag_settings;
  if (!ts || ts.status === 'none')
    return <span className="text-muted-foreground">-</span>;
  if (ts.status === 'matches') {
    return (
      <Badge variant="success" appearance="light">
        Matches
      </Badge>
    );
  }
  return (
    <span title={ts.differ_labels.join(', ')}>
      <Badge
        variant="warning"
        appearance="light"
      >{`Differs: ${ts.differ_count}`}</Badge>
    </span>
  );
}

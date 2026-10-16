'use client';

import {
  PillOverflow,
  PillOverflowList,
} from '@/components/common/PillOverflow';
import type { RespondContact } from '../types/contact.types';

/** The Tags column of the contacts list: one line of pills, the rest behind "+N". */
export default function ContactTagsCell({
  contact,
}: {
  contact: Pick<RespondContact, 'id' | 'name' | 'phone_number' | 'tags'>;
}) {
  const tags = contact.tags ?? [];
  if (tags.length === 0)
    return <span className="text-muted-foreground">-</span>;
  return (
    <PillOverflow
      ariaLabel={`Tags of ${contact.name || contact.phone_number}`}
      testId={`contact-tags-${contact.id}`}
      items={tags.map((t) => ({ key: t.id, label: t.name }))}
      renderPopover={(items) => <PillOverflowList items={items} />}
    />
  );
}

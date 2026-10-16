'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Badge } from '@/components/ui/badge';
import {
  Card,
  CardContent,
  CardHeader,
  CardHeading,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';
import ApplyTagSettingsDialog from '../../components/ApplyTagSettingsDialog';
import {
  getContactTagSettings,
  type TagSettingsItem,
} from '../services/contactTagSettingsService';

function show(value: unknown): string {
  if (value === true) return 'On';
  if (value === false) return 'Off';
  if (value === null || value === undefined) return 'Not set';
  if (Array.isArray(value)) return value.length ? value.join(', ') : 'None';
  return String(value);
}

function ItemRow({ item }: { item: TagSettingsItem }) {
  return (
    <li
      className={`grid grid-cols-1 gap-1 py-2 text-sm sm:grid-cols-[200px_minmax(0,1fr)] sm:gap-3 ${
        item.conflict ? 'rounded-md bg-warning/10 px-2' : ''
      }`}
    >
      <span className="min-w-0 truncate font-medium" title={item.label}>
        {item.label}
      </span>
      {item.conflict ? (
        <span className="flex flex-wrap items-center gap-2">
          <Badge variant="warning" appearance="light">
            Conflict
          </Badge>
          <span className="text-muted-foreground">
            {item.conflict_tags
              .map((t) => `${t.name}: ${show(t.value)}`)
              .join(', ')}
          </span>
        </span>
      ) : (
        <span className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span>
            <span className="text-muted-foreground">Now </span>
            {show(item.current)}
          </span>
          <span>
            <span className="text-muted-foreground">Tags say </span>
            {show(item.expected)}
          </span>
          {item.decided_by.length ? (
            <span className="text-muted-foreground">
              Decided by {item.decided_by.join(', ')}
            </span>
          ) : null}
        </span>
      )}
    </li>
  );
}

/** The contact page's Tags section: its tags, then each setting that differs from what they say. */
export default function ContactTagSettingsSection({
  contactId,
  canApply = true,
}: {
  contactId: string;
  /** Whether to offer Apply; the page passes the viewer's `contacts.edit`. */
  canApply?: boolean;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['contact-tag-settings', contactId],
    queryFn: () => getContactTagSettings(contactId),
  });
  const [applyOpen, setApplyOpen] = useState(false);
  const differing = (data?.items ?? []).filter((i) => i.differs);

  return (
    <Card>
      <CardHeader>
        <CardHeading>
          <CardTitle>Tags</CardTitle>
        </CardHeading>
        {canApply && data && data.status !== 'none' ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => setApplyOpen(true)}
          >
            Apply tag settings
          </Button>
        ) : null}
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : error ? (
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        ) : data ? (
          <div className="space-y-3">
            {data.tags.length ? (
              <div className="flex flex-wrap gap-1">
                {data.tags.map((t) => (
                  <Badge key={t.id} variant="secondary" className="font-normal">
                    {t.name}
                  </Badge>
                ))}
              </div>
            ) : null}
            {data.status === 'differs' && differing.length ? (
              <ul className="divide-y">
                {differing.map((item) => (
                  <ItemRow key={`${item.section}:${item.key}`} item={item} />
                ))}
              </ul>
            ) : (
              <div className="flex flex-col gap-1">
                <h3 className="text-base font-medium">
                  {data.status === 'none'
                    ? 'No tag settings'
                    : 'Everything matches its tags'}
                </h3>
                <p className="text-sm text-muted-foreground">
                  {data.status === 'none'
                    ? 'None of this contact’s tags set anything.'
                    : 'No differences found.'}
                </p>
              </div>
            )}
          </div>
        ) : null}
      </CardContent>
      {applyOpen ? (
        <ApplyTagSettingsDialog
          open
          onOpenChange={setApplyOpen}
          contactIds={[contactId]}
        />
      ) : null}
    </Card>
  );
}

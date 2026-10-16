'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ChevronLeft, Pencil } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
  Card,
  CardContent,
  CardHeader,
  CardHeading,
  CardTitle,
} from '@/components/ui/card';
import { Skeleton } from '@/components/ui/skeleton';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import ListPager from '@/components/common/ListPager';
import DeferredActionButton from '@/components/common/DeferredActionButton';
import { useDeferredAction } from '@/hooks/useDeferredAction';
import { useHasPermission } from '@/hooks/usePermissions';
import { formatDate } from '@/lib/helpers';
import { getContacts } from '../../contacts/[id]/services/contactService';
import { useContactTagQuery } from '../hooks/useContactTags';
import { contactTagsPagerQuery } from '../lib/listQuery';
import { CONTACT_TAG_PERMS } from '../lib/permissions';
import ContactTagFormDialog from './ContactTagFormDialog';
import ContactTagSettings from './ContactTagSettings';

const HOLDERS_LIMIT = 50;

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <span className="text-xs text-muted-foreground">{label}</span>
      <div className="min-w-0 text-sm">{children}</div>
    </div>
  );
}

export default function ContactTagDetail({ id }: { id: string }) {
  const router = useRouter();
  const searchParams = useSearchParams();
  const canEdit = useHasPermission(CONTACT_TAG_PERMS.edit);
  const [editOpen, setEditOpen] = useState(false);
  const [editingSettings, setEditingSettings] = useState(false);
  const { data: tag, isLoading, error } = useContactTagQuery(id);

  const listSearch = searchParams.toString();
  const backHref = `/user-management/contact-tags${listSearch ? `?${listSearch}` : ''}`;

  const remove = useDeferredAction({
    actionKey: 'contact_tag.delete',
    entityType: 'contact_tag',
    entityId: id,
    verb: 'Deleting',
    subject: tag?.name ?? '',
    surface: 'inline',
    watchFromMount: true,
    successMessage: 'Contact tag deleted',
    invalidateKeys: [
      ['contact-tags'],
      ['contact-tag-options'],
      ['respond-contacts'],
    ],
    onCommitted: () => router.push('/user-management/contact-tags'),
  });

  const holders = useQuery({
    queryKey: ['respond-contacts', 'tag-holders', id],
    queryFn: () =>
      getContacts(
        {
          pageIndex: 0,
          pageSize: HOLDERS_LIMIT,
          sorting: [{ id: 'name', desc: false }],
          searchQuery: '',
        },
        { tag: id },
      ),
    enabled: !!tag,
  });

  const actions = (
    <div className="flex flex-wrap items-center gap-2">
      <ListPager
        ariaLabel="contact tag"
        detailPath="/user-management/contact-tags"
        currentId={id}
        {...contactTagsPagerQuery}
      />
      <Button variant="outline" size="sm" asChild>
        <Link href={backHref}>
          <ChevronLeft className="me-1 size-4" />
          Back to tags
        </Link>
      </Button>
      {canEdit && tag ? (
        <>
          <Button variant="outline" size="sm" onClick={() => setEditOpen(true)}>
            <Pencil className="me-1 size-4" />
            Edit
          </Button>
          {!editingSettings ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setEditingSettings(true)}
            >
              Edit settings
            </Button>
          ) : null}
          <DeferredActionButton
            pending={remove.pending}
            verb="Deleting"
            onCancel={remove.cancel}
            idle={
              <Button
                variant="destructive"
                size="sm"
                onClick={() => remove.start()}
                disabled={remove.isBlocked}
              >
                Delete
              </Button>
            }
          />
        </>
      ) : null}
    </div>
  );

  if (error) {
    return (
      <>
        <Container>
          <PageHeader title="Contact tag" actions={actions} />
        </Container>
        <Container>
          <p className="text-sm text-destructive">{(error as Error).message}</p>
        </Container>
      </>
    );
  }

  const holderRows = holders.data?.data ?? [];
  const holderTotal = holders.data?.pagination?.total ?? holderRows.length;

  return (
    <>
      <Container>
        <PageHeader
          title={tag ? tag.name : <Skeleton className="h-7 w-40" />}
          crumbTitle={tag?.name ?? 'Contact tag'}
          actions={actions}
        />
      </Container>
      <Container>
        <div className="space-y-5">
          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>Details</CardTitle>
              </CardHeading>
            </CardHeader>
            <CardContent>
              {isLoading || !tag ? (
                <Skeleton className="h-16 w-full" />
              ) : (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                  <Field label="Status">
                    {tag.is_active ? (
                      <Badge variant="success" appearance="light">
                        Active
                      </Badge>
                    ) : (
                      <Badge variant="secondary" appearance="light">
                        Inactive
                      </Badge>
                    )}
                  </Field>
                  <Field label="Sort order">{tag.sort_order}</Field>
                  <Field label="Created">
                    {formatDate(new Date(tag.created_at))}
                  </Field>
                  <Field label="Updated">
                    {formatDate(new Date(tag.updated_at))}
                  </Field>
                  <div className="sm:col-span-2 lg:col-span-4">
                    <Field label="Description">{tag.description || '-'}</Field>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {tag ? (
            <ContactTagSettings
              key={`${tag.updated_at}-${editingSettings}`}
              tag={tag}
              editing={editingSettings}
              onDone={() => setEditingSettings(false)}
            />
          ) : null}

          <Card>
            <CardHeader>
              <CardHeading>
                <CardTitle>
                  Contacts{tag ? ` (${tag.contact_count})` : ''}
                </CardTitle>
              </CardHeading>
            </CardHeader>
            <CardContent>
              {holders.isLoading ? (
                <Skeleton className="h-16 w-full" />
              ) : holders.error ? (
                <p className="text-sm text-destructive">
                  {(holders.error as Error).message}
                </p>
              ) : holderRows.length === 0 ? (
                <div className="flex flex-col gap-1">
                  <h3 className="text-base font-medium">No contacts</h3>
                  <p className="text-sm text-muted-foreground">
                    Contacts holding this tag appear here.
                  </p>
                </div>
              ) : (
                <ul className="divide-y">
                  {holderRows.map((c) => (
                    <li
                      key={c.id}
                      className="flex min-w-0 items-center justify-between gap-3 py-2"
                    >
                      <Link
                        href={`/user-management/contacts/${c.id}`}
                        className="min-w-0 truncate text-sm text-primary hover:underline"
                        title={c.name || c.phone_number}
                      >
                        {c.name || c.phone_number}
                      </Link>
                      <span className="shrink-0 font-mono text-xs text-muted-foreground">
                        {c.phone_number}
                      </span>
                    </li>
                  ))}
                  {holderTotal > holderRows.length ? (
                    <li className="py-2 text-xs text-muted-foreground">
                      Showing {holderRows.length} of {holderTotal}
                    </li>
                  ) : null}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>
      </Container>
      <ContactTagFormDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        tag={tag ?? null}
      />
    </>
  );
}

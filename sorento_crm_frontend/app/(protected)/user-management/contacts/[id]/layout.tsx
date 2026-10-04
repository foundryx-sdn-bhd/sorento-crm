'use client';

import React, { use, useEffect, useMemo, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { Bot, KeyRound, MessageSquare, Route, UserPen } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Container } from '@/components/common/container';
import { PageHeader } from '@/components/common/PageHeader';
import DetailActions from '@/components/common/DetailActions';
import BackToList, { useBackToListHref } from '@/components/common/BackToList';
import { contactsPagerQuery } from '../lib/listQuery';
import PortalLinkButton from '@/components/contacts/PortalLinkButton';
import ContactDeleteDialog from '../components/ContactDeleteDialog';
import { ContactProvider } from './components/contact-context';
import ContactHero from './components/contact-hero';
import { useContactQuery } from './hooks/useContactQuery';
import { contactActions } from '../actions';
import { ContactImpersonateDialog } from '../components/ContactImpersonateDialog';
import type { RespondContact } from '../types/contact.types';
import { formatDateTimeInMalaysia } from '@/lib/helpers';
import { isNotFound } from '@/lib/api-client';
import { QueryErrorState } from '@/components/common/LoadErrorState';

/**
 * The contact's read-only record metadata, in the page header.
 *
 * Read-only values with no edit counterpart belong in the header, never in a
 * tab body and never beside the actions (ADR product standards). Sitting in the
 * record card's right-hand column, this grid stacked under the identity below
 * `lg` and turned the pager, gear and primary button into a footer strip.
 */
function ContactMeta({ contact }: { contact: RespondContact }) {
  const items: [string, string][] = [
    ['Respond.io ID', contact.respond_io_id || 'Not set'],
    ['Created At', formatDateTimeInMalaysia(contact.created_at)],
    ['Updated At', formatDateTimeInMalaysia(contact.updated_at)],
  ];

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-2sm text-muted-foreground">
      {items.map(([label, value]) => (
        <span key={label} className="break-words">
          {label}: <span className="text-foreground">{value}</span>
        </span>
      ))}
    </div>
  );
}

type NavRoutes = Record<
  string,
  {
    title: string;
    icon: React.FC<React.SVGProps<SVGSVGElement>>;
    /** Appended to the record base path; empty for the first tab. */
    segment: string;
    path: string;
  }
>;

export default function ContactLayout({
  params,
  children,
}: {
  params: Promise<{ id: string }>;
  children: React.ReactNode;
}) {
  const { id } = use(params);
  const pathname = usePathname();
  const router = useRouter();
  const searchParams = useSearchParams();
  const backHref = useBackToListHref('/user-management/contacts');
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  // Impersonate is on the record now as well as the row (D15), through the one
  // dialog both surfaces share.
  const [impersonateTarget, setImpersonateTarget] = useState<RespondContact | null>(null);
  const [activeTab, setActiveTab] = useState<string>('general');

  const navRoutes = useMemo<NavRoutes>(
    () => ({
      general: {
        title: 'Profile',
        icon: UserPen,
        segment: '',
        path: `/user-management/contacts/${id}`,
      },
      access: {
        title: 'Access',
        icon: KeyRound,
        segment: '/access',
        path: `/user-management/contacts/${id}/access`,
      },
      routing: {
        title: 'Routing',
        icon: Route,
        segment: '/routing',
        path: `/user-management/contacts/${id}/routing`,
      },
      chat: {
        title: 'Chat',
        icon: MessageSquare,
        segment: '/chat',
        path: `/user-management/contacts/${id}/chat`,
      },
      // Chatbot memory lane A round 3 (plan 4.5 / mockup): the memory cards move off
      // the Access tab onto their own, matching the mockup's dedicated "Chatbot" tab.
      chatbot: {
        title: 'Chatbot',
        icon: Bot,
        segment: '/chatbot',
        path: `/user-management/contacts/${id}/chatbot`,
      },
    }),
    [id],
  );

  useEffect(() => {
    const found = Object.keys(navRoutes).find((key) => pathname === navRoutes[key].path);
    setActiveTab(found ?? 'general');
  }, [navRoutes, pathname]);

  const { data: contact, isLoading, error, refetch, isFetching } = useContactQuery(id);


  // Tabs are routes, so the click must carry the list position (page/sort/search)
  // the detail URL is holding, or the record pager falls back to "row 1" the
  // moment a tab is clicked (it reads that query string, not local state).
  const handleTabClick = (key: string, path: string) => {
    setActiveTab(key);
    const query = searchParams.toString();
    router.push(query ? `${path}?${query}` : path);
  };


  // Only a 404 is "not found"; a refusal or a fault is not (NEVER-STUCK-UI S3).
  const notFound = isNotFound(error);
  // A background refetch that fails over a contact already shown keeps showing it.
  const failed = !!error && !contact;

  return (
    <ContactProvider contact={contact} isLoading={isLoading} contactId={id}>
      <Container>
        <PageHeader
          title="Contact Details"
          crumbs={[
            { title: 'Internal Users', path: '/user-management/contacts' },
            { title: 'Contact Details' },
          ]}
          actions={
            <BackToList listPath="/user-management/contacts" label="Back to contacts" />
          }
        />
        {contact ? <ContactMeta contact={contact} /> : null}

        {notFound ? (
          <div className="text-center py-12">
            <p className="text-muted-foreground">Contact not found</p>
            <Button
              variant="outline"
              onClick={() => router.push('/user-management/contacts')}
              className="mt-4"
            >
              Back to contacts
            </Button>
          </div>
        ) : failed ? (
          // Before the hero and tabs, which all wait on `contact`: drawing them
          // would leave the Profile tab on its skeleton forever (lever L9).
          <QueryErrorState
            error={error}
            title="Could not load this contact"
            onRetry={() => void refetch()}
            retrying={isFetching}
          />
        ) : (
          <>
            <ContactHero
              contact={contact}
              isLoading={isLoading}
              actions={
                <DetailActions
                  pager={{
                    ...contactsPagerQuery,
                    detailPath: '/user-management/contacts',
                    currentId: id,
                    ariaLabel: 'contact',
                    // Stepping keeps the tab being read, so comparing the same
                    // section across contacts does not bounce back to Profile.
                    hrefFor: (nextId, search) =>
                      `/user-management/contacts/${nextId}${
                        navRoutes[activeTab]?.segment ?? ''
                      }${search ? `?${search}` : ''}`,
                  }}
                  actions={
                    contact
                      ? contactActions(contact, {
                          impersonate: () => setImpersonateTarget(contact),
                          remove: () => setDeleteDialogOpen(true),
                        })
                      : []
                  }
                  gearLabel="Contact options"
                  primary={
                    <PortalLinkButton
                      contactId={id}
                      contactLabel={contact?.name ?? contact?.phone_number ?? 'this contact'}
                      canSendViaRespondIo={!!contact?.respond_io_id}
                    />
                  }
                />
              }
            />
            <Tabs defaultValue={activeTab} value={activeTab}>
              <TabsList variant="line" className="mb-5">
                {Object.entries(navRoutes).map(([key, { title, icon: Icon, path }]) => (
                  <TabsTrigger
                    key={key}
                    value={key}
                    disabled={isLoading}
                    onClick={() => handleTabClick(key, path)}
                  >
                    <Icon />
                    <span>{title}</span>
                  </TabsTrigger>
                ))}
              </TabsList>
            </Tabs>
            {children}
          </>
        )}
        <ContactImpersonateDialog
          contact={impersonateTarget}
          onClose={() => setImpersonateTarget(null)}
        />
      </Container>

      <ContactDeleteDialog
        open={deleteDialogOpen}
        onOpenChange={setDeleteDialogOpen}
        contact={contact ?? null}
        onSuccess={() => router.push(backHref)}
      />
    </ContactProvider>
  );
}

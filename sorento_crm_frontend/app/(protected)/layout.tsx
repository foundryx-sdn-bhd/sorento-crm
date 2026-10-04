'use client';

import { Fragment, useEffect } from 'react';
import { useSession } from 'next-auth/react';
import { ScreenLoader } from '@/components/common/screen-loader';
import { Demo1Layout } from '../components/layouts/demo1/layout';
import { useImpersonation } from '@/hooks/useImpersonation';
import { endSessionAndRedirect, setSignedInShell } from '@/lib/session-end';
import GuideTargetSpotlight from '@/app/components/common/GuideTargetSpotlight';
import {
  UploadActivityDrawer,
  UploadManagerProvider,
} from '@/components/upload-activity';
import { MyDownloadsProvider } from '@/components/my-downloads/MyDownloadsContext';
import { MyDownloadsDrawer } from '@/components/my-downloads/MyDownloadsDrawer';
import { CompanyProvider } from '@/app/providers/CompanyProvider';
import PushPrompts from '@/components/pwa/PushPrompts';
import PermissionsLoadBanner from '@/app/components/common/PermissionsLoadBanner';

export default function ProtectedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const { data: session, status } = useSession();
  const { hydrate } = useImpersonation();

  useEffect(() => {
    if (status === 'authenticated') {
      hydrate().catch(() => {});
    }
  }, [status, hydrate]);

  useEffect(() => {
    setSignedInShell(status === 'authenticated');
    return () => setSignedInShell(false);
  }, [status]);

  useEffect(() => {
    if (status === 'unauthenticated') {
      // Permanent deep-link-after-login: the return URL is the FULL relative URL
      // (path + query + hash). One latched hard navigation shared with apiFetch's
      // dead-session path, so a session that dies mid-page reaches /signin exactly
      // once and leaves view-as behind (SESSION-NEVER-STUCK). The signin page only
      // honours same-origin relative callbackUrls. Staff/NextAuth only - the portal
      // contact OTP flow uses public /view links and never hits this layout.
      endSessionAndRedirect();
    }
  }, [status]);

  if (status === 'loading' || status === 'unauthenticated') {
    return <ScreenLoader />;
  }

  if (!session) {
    return <ScreenLoader />;
  }

  return (
    <CompanyProvider>
      <UploadManagerProvider>
        <MyDownloadsProvider>
          <GuideTargetSpotlight />
          <Demo1Layout>
            {/* In flow at the top of the page body: neither prompt can cover the
                primary action of the page beneath it (AC-P16). Keyed because
                Demo1Layout renders its children as a list inside <main>, and the
                routed page arrives with no key of its own: without these, every
                page logged a missing-key error (the dev "1 Issue" badge, #1286). */}
            <PushPrompts key="push-prompts" />
            {/* A failed permission check hides menus and buttons (fail closed);
                this says so, with Retry, instead of leaving a thinner app (L6). */}
            <PermissionsLoadBanner key="permissions-load-banner" />
            <Fragment key="page">{children}</Fragment>
          </Demo1Layout>
          <UploadActivityDrawer />
          <MyDownloadsDrawer />
        </MyDownloadsProvider>
      </UploadManagerProvider>
    </CompanyProvider>
  );
}

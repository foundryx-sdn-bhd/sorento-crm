/**
 * SESSION-NEVER-STUCK: the protected shell's own "unauthenticated" redirect goes through
 * the same latched `endSessionAndRedirect` as apiFetch, so a dead session navigates to
 * sign-in exactly once (it used to be a soft `router.push` PLUS apiFetch's hard
 * redirect: two navigations), and view-as is cleared on the way out.
 */
import { render } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const push = vi.fn();
const sessionState: { status: string; data: unknown } = { status: 'unauthenticated', data: null };

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push, replace: push }),
  usePathname: () => '/procurement-management/packing-lists/pl-1',
}));
vi.mock('next-auth/react', () => ({
  useSession: () => sessionState,
  signOut: vi.fn(async () => undefined),
}));
vi.mock('@/hooks/useImpersonation', () => ({
  useImpersonation: () => ({ hydrate: vi.fn(async () => null) }),
}));
vi.mock('@/components/common/screen-loader', () => ({
  ScreenLoader: () => <div data-testid="screen-loader" />,
}));
vi.mock('../components/layouts/demo1/layout', () => ({
  Demo1Layout: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));
const Pass = ({ children }: { children?: React.ReactNode }) => <>{children}</>;
vi.mock('@/app/components/common/GuideTargetSpotlight', () => ({ default: () => null }));
vi.mock('@/components/upload-activity', () => ({
  UploadActivityDrawer: () => null,
  UploadManagerProvider: Pass,
}));
vi.mock('@/components/my-downloads/MyDownloadsContext', () => ({ MyDownloadsProvider: Pass }));
vi.mock('@/components/my-downloads/MyDownloadsDrawer', () => ({ MyDownloadsDrawer: () => null }));
vi.mock('@/app/providers/CompanyProvider', () => ({ CompanyProvider: Pass }));
vi.mock('@/components/pwa/PushPrompts', () => ({ default: () => null }));
vi.mock('@/app/components/common/PermissionsLoadBanner', () => ({ default: () => null }));

describe('ProtectedLayout when the NextAuth session is gone', () => {
  beforeEach(() => {
    sessionState.status = 'unauthenticated';
    sessionState.data = null;
    push.mockReset();
    window.history.replaceState(null, '', '/procurement-management/packing-lists/pl-1?tab=lines');
  });

  it('hands off to endSessionAndRedirect once (no soft router.push), with the return URL', async () => {
    const end = await import('@/lib/session-end');
    const assign = vi.fn();
    end.sessionNavigation.assign = assign as unknown as (url: string) => void;
    const { default: ProtectedLayout } = await import('./layout');

    const { rerender } = render(<ProtectedLayout>page</ProtectedLayout>);
    rerender(<ProtectedLayout>page</ProtectedLayout>);

    await vi.waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    expect(push).not.toHaveBeenCalled();
    expect(assign).toHaveBeenCalledWith(
      `/signin?callbackUrl=${encodeURIComponent('/procurement-management/packing-lists/pl-1?tab=lines')}`,
    );
  });
});

describe('ProtectedLayout marks the signed-in shell', () => {
  it('on while authenticated, off once unmounted (public pages never end a session)', async () => {
    sessionState.status = 'authenticated';
    sessionState.data = { user: { id: 'u-1' } };
    const end = await import('@/lib/session-end');
    const { default: ProtectedLayout } = await import('./layout');

    const { unmount } = render(<ProtectedLayout>page</ProtectedLayout>);
    await vi.waitFor(() => expect(end.isSignedInShell()).toBe(true));
    unmount();

    expect(end.isSignedInShell()).toBe(false);
  });
});

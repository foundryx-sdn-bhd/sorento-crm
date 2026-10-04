'use client';

import { useEffect, useRef, useState } from 'react';
import { signIn } from 'next-auth/react';
import { LoaderCircleIcon } from 'lucide-react';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { Button } from '@/components/ui/button';
import { Separator } from '@/components/ui/separator';
import type { DevLoginUser } from '@/services/devLoginService';
import { useDevLoginUsers } from '../hooks/useDevLoginUsers';

/**
 * DEV-LOGIN-BYPASS (PLAN-dev-login-bypass-03oct.md): on a local test copy with the flag on,
 * sign in as the default dev user once per tab, then offer a "Sign in as" picker so a tester
 * can switch users. After a sign-out in the same tab the picker shows instead of auto-signing
 * straight back in. Renders nothing when dev sign-in is off.
 */
export const DEV_LOGIN_AUTO_KEY = 'sorento.devLogin.autoDone';

function autoAlreadyRan(): boolean {
  try {
    return window.sessionStorage.getItem(DEV_LOGIN_AUTO_KEY) === '1';
  } catch {
    return true; // storage blocked: never loop, just show the picker
  }
}

function markAutoRan(): void {
  try {
    window.sessionStorage.setItem(DEV_LOGIN_AUTO_KEY, '1');
  } catch {
    // ignore: autoAlreadyRan() already reads blocked storage as "ran"
  }
}

function userLabel(u: DevLoginUser): string {
  const name = u.name || u.email;
  return u.role_name ? `${name} (${u.role_name})` : name;
}

interface DevLoginPickerProps {
  onSignedIn: () => Promise<void> | void;
  onError: (message: string | null) => void;
  disabled?: boolean;
}

export function DevLoginPicker({ onSignedIn, onError, disabled }: DevLoginPickerProps) {
  const { data: users } = useDevLoginUsers();
  const [selected, setSelected] = useState('');
  const [busy, setBusy] = useState(false);
  const autoStarted = useRef(false);

  async function signInAs(email: string) {
    setBusy(true);
    onError(null);
    const response = await signIn('dev-login', { redirect: false, email }).catch(() => null);
    if (!response || response.error) {
      let message = 'Dev sign-in is not available.';
      try {
        message = JSON.parse(response?.error ?? '').message || message;
      } catch {
        // keep the fallback
      }
      onError(message);
      setBusy(false);
      return;
    }
    await onSignedIn();
  }

  useEffect(() => {
    if (!users?.length || autoStarted.current) return;
    setSelected((cur) => cur || users[0].email);
    if (autoAlreadyRan()) return;
    autoStarted.current = true;
    markAutoRan();
    void signInAs(users[0].email);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [users]);

  if (!users?.length) return null;

  return (
    <div className="space-y-3" data-testid="dev-login-picker">
      <div className="flex items-center gap-3">
        <Separator className="flex-1" />
        <span className="text-xs text-muted-foreground">Dev sign-in</span>
        <Separator className="flex-1" />
      </div>
      <div className="flex flex-col gap-2.5 sm:flex-row">
        <SearchableSelect
          id="dev-login-user"
          aria-label="Sign in as"
          className="min-w-0 flex-1"
          value={selected}
          onChange={setSelected}
          options={users.map((u) => ({ value: u.email, label: userLabel(u), description: u.email }))}
          placeholder="Sign in as"
          disabled={busy || disabled}
        />
        <Button
          type="button"
          variant="outline"
          disabled={!selected || busy || disabled}
          aria-busy={busy}
          onClick={() => void signInAs(selected)}
        >
          {busy ? (
            <LoaderCircleIcon aria-hidden="true" className="size-4 animate-spin motion-reduce:animate-none" />
          ) : null}
          {busy ? 'Signing you in' : 'Sign in as user'}
        </Button>
      </div>
    </div>
  );
}

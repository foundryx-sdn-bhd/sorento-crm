'use client';

import { useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { zodResolver } from '@hookform/resolvers/zod';
import { AlertCircle, ArrowLeft, Eye, EyeOff, Smartphone } from 'lucide-react';
import { getSession, signIn } from 'next-auth/react';
import { useForm } from 'react-hook-form';
import { Alert, AlertIcon, AlertTitle } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';
import { Separator } from '@/components/ui/separator';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { LoaderCircleIcon } from 'lucide-react';
import { getSigninSchema, SigninSchemaType } from '../forms/signin-schema';
import { toAbsoluteUrl } from '@/lib/helpers';
import { PhoneSignIn } from './components/phone-signin';
import { DevLoginPicker } from './components/dev-login-picker';

type SigninMode = 'email' | 'phone';

/**
 * A same-origin, path-only `callbackUrl` - never a protocol-relative or
 * backslash-smuggled one (security round S3, #1280: `/\evil.com` passed the
 * old `startsWith('/') && !startsWith('//')` check because a leading
 * backslash isn't a leading slash, and some browsers then read the whole
 * thing as `//evil.com`, an open redirect). `startsWith('/')` plus "no
 * backslash anywhere" rejects the smuggling attempts outright; the `new URL`
 * origin check is the actual same-origin proof for anything that survives
 * those two.
 */
export function isSafeCallbackUrl(cb: string | null | undefined): cb is string {
  if (!cb || typeof cb !== 'string') return false;
  if (!cb.startsWith('/') || cb.includes('\\')) return false;
  try {
    return (
      new URL(cb, window.location.origin).origin === window.location.origin
    );
  } catch {
    return false;
  }
}

/**
 * A deep link the user followed wins; otherwise the session's own `homePath`
 * (a salesperson's portal home, the CRM home for anyone with a permission,
 * else the portal home), AC-28. The bare `/` the protected layout adds on a
 * plain visit is not a deep link, so it never beats `homePath`.
 */
export function pickLandingUrl(
  callbackUrl: string | null,
  homePath: string | null | undefined,
): string {
  if (isSafeCallbackUrl(callbackUrl) && callbackUrl !== '/') return callbackUrl;
  return homePath || '/';
}

async function resolveLandingUrl(callbackUrl: string | null): Promise<string> {
  if (isSafeCallbackUrl(callbackUrl) && callbackUrl !== '/') return callbackUrl;
  const session = await getSession();
  return pickLandingUrl(callbackUrl, session?.user?.homePath);
}

export default function Page() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [mode, setMode] = useState<SigninMode>('email');
  const [passwordVisible, setPasswordVisible] = useState(false);
  const [isProcessing, setIsProcessing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [phoneBusy, setPhoneBusy] = useState(false);

  const form = useForm<SigninSchemaType>({
    resolver: zodResolver(getSigninSchema()),
    mode: 'onTouched',
    defaultValues: {
      email: '',
      password: '',
    },
  });

  const handleModeChange = (next: SigninMode) => {
    setMode(next);
    setError(null);
  };

  async function goToLandingUrl() {
    const target = await resolveLandingUrl(
      searchParams?.get('callbackUrl') ?? null,
    );
    router.push(target);
  }

  // Same rule as the phone step (fix round 4, #1307): a successful sign-in
  // keeps "Signing you in" up through the session read and the route change,
  // because `router.push` returns before the destination paints and the old
  // `finally` reset left an idle Continue on screen for that whole span. The
  // page unmounts when the destination renders; only a failure unlocks it.
  async function onSubmit(values: SigninSchemaType) {
    setIsProcessing(true);
    setError(null);

    try {
      const response = await signIn('credentials', {
        redirect: false,
        email: values.email,
        password: values.password,
      });

      if (response?.error) {
        try {
          const errorData = JSON.parse(response.error);
          setError(errorData.message || response.error);
        } catch {
          // If error is not JSON, use it directly (might be a Prisma error or other non-JSON error)
          setError(response.error || 'An error occurred during sign in.');
        }
        setIsProcessing(false);
      } else {
        await goToLandingUrl();
      }
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'An unexpected error occurred. Please try again.',
      );
      setIsProcessing(false);
    }
  }

  return (
    <div className="block w-full space-y-5">
      <div className="flex justify-start pb-4">
        {/* The dark wordmark on the dark backdrop was all but invisible when the
            user's theme is dark, which is the same swap the app header does. */}
        <Link href="/">
          <img
            src={toAbsoluteUrl('/media/app/sorento-logo.svg')}
            className="h-[28px] max-w-none dark:hidden"
            alt="Sorento"
          />
          <img
            src={toAbsoluteUrl('/media/app/sorento-logo-dark.svg')}
            className="h-[28px] max-w-none hidden dark:inline"
            alt="Sorento"
          />
        </Link>
      </div>

      <div className="space-y-1.5 pb-3">
        <h1 className="text-2xl font-semibold tracking-tight text-center">
          Sign in to Sorento
        </h1>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertIcon>
            <AlertCircle />
          </AlertIcon>
          <AlertTitle>{error}</AlertTitle>
        </Alert>
      )}

      {mode === 'email' ? (
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <fieldset disabled={isProcessing} className="space-y-5 min-w-0">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input placeholder="Your email" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <div className="flex justify-between items-center gap-2.5">
                      <FormLabel>Password</FormLabel>
                      <Link
                        href="/reset-password"
                        className="text-sm font-semibold text-foreground hover:text-primary"
                      >
                        Forgot Password?
                      </Link>
                    </div>
                    <div className="relative">
                      <Input
                        placeholder="Your password"
                        type={passwordVisible ? 'text' : 'password'} // Toggle input type
                        {...field}
                      />
                      <Button
                        type="button"
                        variant="ghost"
                        mode="icon"
                        size="sm"
                        onClick={() => setPasswordVisible(!passwordVisible)} // Toggle visibility
                        className="absolute end-0 top-1/2 -translate-y-1/2 h-7 w-7 me-1.5 bg-transparent!"
                        aria-label={
                          passwordVisible ? 'Hide password' : 'Show password'
                        }
                      >
                        {passwordVisible ? (
                          <EyeOff className="text-muted-foreground" />
                        ) : (
                          <Eye className="text-muted-foreground" />
                        )}
                      </Button>
                    </div>
                    <FormMessage />
                  </FormItem>
                )}
              />

              <div className="flex flex-col gap-2.5">
                <Button
                  type="submit"
                  disabled={isProcessing}
                  aria-busy={isProcessing}
                >
                  {isProcessing ? (
                    <LoaderCircleIcon
                      aria-hidden="true"
                      className="size-4 animate-spin motion-reduce:animate-none"
                    />
                  ) : null}
                  {isProcessing ? 'Signing you in' : 'Continue'}
                </Button>
              </div>
            </fieldset>
          </form>
        </Form>
      ) : (
        <PhoneSignIn
          onError={setError}
          onSignedIn={goToLandingUrl}
          onBusyChange={setPhoneBusy}
        />
      )}

      <DevLoginPicker
        onSignedIn={goToLandingUrl}
        onError={setError}
        disabled={isProcessing || phoneBusy}
      />

      {mode === 'email' ? (
        <>
          <div className="flex items-center gap-3">
            <Separator className="flex-1" />
            <span className="text-xs text-muted-foreground">
              or Log in with
            </span>
            <Separator className="flex-1" />
          </div>
          <div className="flex justify-center">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  type="button"
                  variant="outline"
                  mode="icon"
                  shape="circle"
                  size="lg"
                  aria-label="Phone number"
                  onClick={() => handleModeChange('phone')}
                  disabled={isProcessing}
                >
                  <Smartphone />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Phone number</TooltipContent>
            </Tooltip>
          </div>
        </>
      ) : (
        <div className="flex justify-center">
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => handleModeChange('email')}
            disabled={phoneBusy}
            className="text-muted-foreground"
          >
            <ArrowLeft />
            Back to email
          </Button>
        </div>
      )}
    </div>
  );
}

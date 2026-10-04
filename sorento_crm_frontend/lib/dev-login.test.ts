/**
 * DEV-LOGIN-BYPASS frontend guard truth table (AC-09). Each guard is killed on its own.
 */
import { describe, expect, it } from 'vitest';
import {
  boundToLoopback,
  devLoginAllowed,
  devLoginBackendHeaders,
  devLoginConfigured,
  hostFromHeaders,
  isLocalHost,
} from './dev-login';

const ON = {
  DEV_AUTO_LOGIN: 'true',
  NODE_ENV: 'development',
  DEV_AUTO_LOGIN_SECRET: 'fe-server-secret-0123456789',
  __NEXT_PRIVATE_ORIGIN: 'http://127.0.0.1:3101',
};

describe('devLoginConfigured', () => {
  it('is on only with the exact flag outside a production build', () => {
    expect(devLoginConfigured(ON)).toBe(true);
    expect(devLoginConfigured({ ...ON, NODE_ENV: 'test' })).toBe(true);
  });
  it.each([
    ['flag unset', { ...ON, DEV_AUTO_LOGIN: undefined }],
    ['secret unset', { ...ON, DEV_AUTO_LOGIN_SECRET: undefined }],
    ['secret short', { ...ON, DEV_AUTO_LOGIN_SECRET: 'short' }],
    ['flag false', { ...ON, DEV_AUTO_LOGIN: 'false' }],
    ['flag 1', { ...ON, DEV_AUTO_LOGIN: '1' }],
    ['flag TRUE', { ...ON, DEV_AUTO_LOGIN: 'TRUE' }],
    ['production build', { ...ON, NODE_ENV: 'production' }],
  ])('kill: %s', (_name, env) => {
    expect(devLoginConfigured(env)).toBe(false);
    expect(devLoginAllowed('localhost:3000', env)).toBe(false);
  });
});

describe('isLocalHost', () => {
  it.each([
    ['localhost', true],
    ['localhost:3101', true],
    ['LocalHost:3101', true],
    ['lane-a.localhost:3101', true],
    ['127.0.0.1:3000', true],
    ['', false],
    [null, false],
    [undefined, false],
    ['localhost.', false],
    ['localhost.evil.com', false],
    ['evillocalhost', false],
    ['a.b.localhost.com', false],
    ['tehs-mac-mini:3000', false],
    ['100.64.0.1:3000', false],
    ['[::1]:3000', false],
    ['127.0.0.2', false],
    ['localhost:abc', false],
  ])('%s -> %s', (host, ok) => {
    expect(isLocalHost(host as string | null | undefined)).toBe(ok);
  });
});

describe('boundToLoopback (security review B2)', () => {
  it.each([
    ['http://127.0.0.1:3101', true],
    // Next reports `localhost` when it listens on every interface, so it never counts.
    ['http://localhost:3101', false],
    ['http://0.0.0.0:3101', false],
    ['http://192.168.1.5:3101', false],
    ['http://[::1]:3101', false],
    ['not a url', false],
    [undefined, false],
  ])('%s -> %s', (origin, ok) => {
    expect(boundToLoopback({ __NEXT_PRIVATE_ORIGIN: origin as string | undefined })).toBe(ok);
  });

  it('kill: a dev server bound to every interface refuses even a localhost Host', () => {
    expect(devLoginAllowed('localhost:3101', { ...ON, __NEXT_PRIVATE_ORIGIN: 'http://localhost:3101' })).toBe(false);
    expect(devLoginAllowed('localhost:3101', { ...ON, __NEXT_PRIVATE_ORIGIN: undefined })).toBe(false);
  });
});

describe('devLoginBackendHeaders', () => {
  it('carries the server-only secret', () => {
    expect(devLoginBackendHeaders(ON)).toEqual({ 'X-Dev-Login-Secret': ON.DEV_AUTO_LOGIN_SECRET });
  });
});

describe('devLoginAllowed', () => {
  it('kill: a non-local host refuses even with the flag on', () => {
    expect(devLoginAllowed('sorento.example.com', ON)).toBe(false);
    expect(devLoginAllowed('lane-a.localhost:3101', ON)).toBe(true);
  });
});

describe('hostFromHeaders', () => {
  it('reads Fetch Headers and plain records', () => {
    expect(hostFromHeaders(new Headers({ host: 'localhost:3000' }))).toBe('localhost:3000');
    expect(hostFromHeaders({ host: 'a.localhost' })).toBe('a.localhost');
    expect(hostFromHeaders({ host: ['b.localhost'] })).toBe('b.localhost');
    expect(hostFromHeaders(undefined)).toBe(null);
    expect(hostFromHeaders({})).toBe(null);
  });
});

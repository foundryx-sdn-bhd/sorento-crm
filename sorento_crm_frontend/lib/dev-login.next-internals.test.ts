/**
 * DEV-LOGIN-BYPASS (security review SF2): `boundToLoopback` trusts Next's private
 * `__NEXT_PRIVATE_ORIGIN`, which today reads `localhost` when the dev server listens on every
 * interface and `127.0.0.1` only for `-H 127.0.0.1`. A Next upgrade that changed that mapping
 * could make the guard fail open, so this pins the exact source lines and fails on a bump.
 */
import { readFileSync } from 'fs';
import path from 'path';
import { describe, expect, it } from 'vitest';

const startServer = readFileSync(
  path.resolve(__dirname, '../node_modules/next/dist/server/lib/start-server.js'),
  'utf8',
);

describe('Next start-server internals the dev-login bind check relies on', () => {
  it('maps an unset or 0.0.0.0 bind to localhost and [::] to [::1]', () => {
    expect(startServer).toContain(
      "const formattedHostname = !hostname || actualHostname === '0.0.0.0' ? 'localhost' : actualHostname === '[::]' ? '[::1]' : (0, _formathostname.formatHostname)(hostname);",
    );
  });

  it('records that hostname as __NEXT_PRIVATE_ORIGIN', () => {
    expect(startServer).toContain('const appUrl = `${protocol}://${formattedHostname}:${port}`;');
    expect(startServer).toContain('process.env.__NEXT_PRIVATE_ORIGIN = appUrl;');
  });
});

import { defineConfig } from 'vitest/config';
import os from 'os';
import path from 'path';
import react from '@vitejs/plugin-react';

export default defineConfig({
  resolve: {
    // Array form so the tooltip shim can match EXACTLY '@/components/ui/tooltip'
    // and nothing else; order matters, first match wins.
    alias: [
      // M2-07 test-harness shim: Tooltip is a bare Root in production (one
      // ambient TooltipProvider in ClientProviders.tsx), so this wraps it in
      // the calibrated Provider for the test run only - see
      // test-mocks/ui-tooltip.tsx for the full rationale. It imports the real
      // component by relative path, so this alias does not recurse.
      {
        find: /^@\/components\/ui\/tooltip$/,
        replacement: path.resolve(__dirname, 'test-mocks/ui-tooltip.tsx'),
      },
      { find: '@', replacement: path.resolve(__dirname) },
    ],
  },
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./vitest.setup.ts'],
    /*
      Comfortably above the 5s Testing Library gives one async assertion (see
      vitest.setup.ts). Equal budgets mean the TEST times out first and reports
      "timed out in 5000ms" instead of the assertion's own message, which says
      what was actually missing from the DOM - so a real failure arrives with no
      diagnosis at all.
    */
    testTimeout: 20000,
    /*
      CI only: a failed test is re-run up to twice before it counts. The full
      suite (~10.5k tests) runs on a 2-vCPU runner, and on 2026-09-06 five
      consecutive deploy runs were each blocked by ONE different test hitting
      its 5s async wait with a starved worker, while the same files pass in a
      loop locally. A real failure still fails all three attempts and blocks.
      Vitest reports a test that passed on retry as flaky in the summary, so
      the signal is not lost. Local runs keep retry at 0 so a flake is seen
      while it is being written.
    */
    retry: process.env.CI ? 2 : 0,
    /*
      CI only: one worker per core. Vitest's default is cores - 1, which on the
      2-vCPU runner is ONE worker, so a shard ran its ~377 files strictly one
      after another (~1.4s each, mostly jsdom setup) and took 18.9 of its 20
      minutes; releases 38406697551 and 38415596343 lost shard 2 to the job
      timeout 0.2s after its summary printed (it read as a hang, it was not).
      Two workers on two cores measured 34% faster (shard 2/16 pinned to two
      CPUs: 227s -> 150s, same 925 tests green). Local runs keep the default
      so a dev machine keeps a core free.
    */
    maxWorkers: process.env.CI ? os.availableParallelism() : undefined,
    exclude: ['node_modules/**', 'e2e/**', '.next/**'],
  },
});


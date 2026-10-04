/** @type {import('next').NextConfig} */
const nextConfig = {
  // For local development, basePath is '/'
  // This file will be overwritten during deployment with the appropriate basePath
  images: {},
  output: 'standalone',
  // `next dev` and `next start` share `.next/` by default, so starting a dev server in
  // this directory REPLACES the production build a running `next start` is serving and
  // takes it down with a client-side exception. Set NEXT_DIST_DIR=.next-dev when running
  // a dev server alongside a prod one so the two never write to the same directory.
  ...(process.env.NEXT_DIST_DIR ? { distDir: process.env.NEXT_DIST_DIR } : {}),
  // Don't fail build on ESLint errors (warnings will still be shown)
  // This allows Docker builds to complete even with linting issues
  eslint: {
    ignoreDuringBuilds: true,
  },
  // The Docker image build skips tsc (NEXT_SKIP_TYPECHECK=1, set only in the
  // frontend Dockerfile's builder stage) because the `typecheck-frontend` CI
  // job runs `tsc --noEmit` on the runner in parallel with the test jobs and
  // gates the deploy on it. A local `npm run build` still type-checks: the
  // env var is unset outside the Docker build.
  typescript: {
    ignoreBuildErrors: process.env.NEXT_SKIP_TYPECHECK === '1',
  },
  // lucide-react is on Next's built-in optimizePackageImports list; @remixicon/react is not,
  // so its barrel imports would otherwise pull the whole icon set into every dev compile.
  // proxyTimeout: the rewrite proxy below (dev, or a NEVER_STUCK_API_PROXY build) cuts a
  // request at 30s by default and the browser gets a bare 500, while a slow chatbot turn
  // can legitimately take longer than that.
  experimental: {
    optimizePackageImports: ['@remixicon/react'],
    proxyTimeout: 120000,
  },
  compiler: {
    removeConsole: process.env.NODE_ENV === 'production' ? {
      exclude: ['error', 'warn'], // Keep errors and warnings in production
    } : false,
  },
  // Optimize production builds
  productionBrowserSourceMaps: false, // Disable source maps in production for faster builds
  // Reduce build output size
  poweredByHeader: false,
  reactStrictMode: true,
  // Hosts (besides localhost) allowed to load /_next/* from the dev server: Tailscale name,
  // LAN IP, etc. Comma-separated; wildcards like *.ts.net allowed. Dev only, ignored by builds.
  // Next normally 308s '/x/' to '/x'. The FastAPI trailing-slash 307 goes the other way, and
  // the two chase each other forever through the /api/v1 rewrite below. Let '/x/' through.
  skipTrailingSlashRedirect: true,
  allowedDevOrigins: (process.env.NEXT_DEV_ALLOWED_ORIGINS ?? '')
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean),
  // Proxy API requests to FastAPI backend in development
  async rewrites() {
    // Only apply rewrites in development mode. NEVER_STUCK_API_PROXY=1 at BUILD time turns
    // them on for a production build too: the nightly never-stuck smoke runs `next start`
    // with no reverse proxy in front (production has one; rewrites are baked in at build).
    if (process.env.NODE_ENV === 'development' || process.env.NEVER_STUCK_API_PROXY === '1') {
      return [
        // The rewrite param drops a trailing slash, and FastAPI 307s a list route without
        // one, so '/x/' must be forwarded as '/x/' or the two loop.
        {
          source: '/api/v1/:path*/',
          destination: `${process.env.FASTAPI_INTERNAL_URL ?? 'http://localhost:8000'}/api/v1/:path*/`,
        },
        {
          source: '/api/v1/:path*',
          destination: `${process.env.FASTAPI_INTERNAL_URL ?? 'http://localhost:8000'}/api/v1/:path*`,
        },
      ];
    }
    return [];
  },
};

export default nextConfig;

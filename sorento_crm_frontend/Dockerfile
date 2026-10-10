# Multi-stage build for Next.js frontend
# Stage 1: Build the Next.js application
FROM node:20-alpine AS builder

WORKDIR /app

# Copy package files first for better layer caching
COPY package*.json ./

# Install dependencies with BuildKit cache mount for faster rebuilds
# Use npm ci with aggressive caching and parallel installs
RUN --mount=type=cache,target=/root/.npm \
    npm ci --prefer-offline --no-audit --legacy-peer-deps

# Copy all source code (optimize by copying everything except node_modules)
# Using a single COPY is actually faster than multiple COPYs due to layer optimization
# The .dockerignore ensures only necessary files are copied
COPY . .

# Set build-time environment variable for API URL
# Empty string means use relative paths (nginx will proxy)
ARG NEXT_PUBLIC_API_URL=
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL

# PWA web-push public key (TCK-33). NEXT_PUBLIC_* is inlined into the client
# bundle at build time, so it MUST be present here, not just at runtime. Empty
# default = no push subscription offered (installability still works). Supply via
# the deploy workflow's build-args from a GitHub secret — no server SSH needed.
ARG NEXT_PUBLIC_VAPID_PUBLIC_KEY=
ENV NEXT_PUBLIC_VAPID_PUBLIC_KEY=$NEXT_PUBLIC_VAPID_PUBLIC_KEY

# Set Node.js options for faster builds
# Increase memory and enable parallel garbage collection. 6144 MB since the
# repo went private: private-repo runners have less RAM and the CI image
# build hit the 4096 MB heap limit (10 Oct 2026).
ENV NODE_OPTIONS="--max-old-space-size=6144 --max-semi-space-size=128"

# Skip tsc here: the `typecheck-frontend` CI job already runs `npm run
# typecheck` on the runner, in parallel with the test jobs, and gates the
# deploy on it (see next.config.mjs). A local `npm run build` still
# type-checks since this var is unset outside the Docker build.
ENV NEXT_SKIP_TYPECHECK=1

# Build the Next.js application with BuildKit cache mount
# Caches .next/cache and node_modules/.cache between builds for faster incremental builds
# Disable telemetry and set production mode for faster builds
RUN --mount=type=cache,target=/app/.next/cache,sharing=locked \
    --mount=type=cache,target=/app/node_modules/.cache,sharing=locked \
    NEXT_TELEMETRY_DISABLED=1 \
    NODE_ENV=production \
    npm run build

# Stage 2: Production image with Node.js and Nginx
FROM node:20-alpine

# Install nginx and curl for health checks
RUN apk add --no-cache nginx curl supervisor

# Copy custom Nginx configuration
# Alpine nginx expects server blocks in http.d, not conf.d
RUN mkdir -p /etc/nginx/http.d
COPY nginx.conf /etc/nginx/http.d/default.conf

# Copy the built Next.js standalone application from builder
# Next.js standalone creates a minimal server in .next/standalone
WORKDIR /app
COPY --from=builder /app/.next/standalone ./
COPY --from=builder /app/.next/static ./.next/static
COPY --from=builder /app/public ./public

# The standalone output structure has server.js in the root of .next/standalone
# We need to ensure the static files are in the right place
RUN if [ -d ".next/standalone/.next" ]; then \
        cp -r .next/standalone/.next/static .next/static 2>/dev/null || true; \
    fi

# Set environment variable for Next.js to run on port 3000
ENV PORT=3000
ENV HOSTNAME=0.0.0.0

# Create supervisor config directory and file to run both nginx and Next.js
RUN mkdir -p /etc/supervisor/conf.d && \
    echo '[supervisord]' > /etc/supervisor/conf.d/supervisord.conf && \
    echo 'nodaemon=true' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo '' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo '[program:nginx]' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'command=nginx -g "daemon off;"' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'autostart=true' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'autorestart=true' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo '' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo '[program:nextjs]' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'command=node server.js' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'directory=/app' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'autostart=true' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'autorestart=true' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'stdout_logfile=/dev/stdout' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'stdout_logfile_maxbytes=0' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'stderr_logfile=/dev/stderr' >> /etc/supervisor/conf.d/supervisord.conf && \
    echo 'stderr_logfile_maxbytes=0' >> /etc/supervisor/conf.d/supervisord.conf

# Create nginx user and set permissions
RUN chown -R node:node /app && \
    chmod -R 755 /app

# Expose port 80
EXPOSE 80

# Health check
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD curl -f http://localhost/health || exit 1

# Start supervisor to run both nginx and Next.js.
# Prisma removed: NextAuth/session state is owned by FastAPI now, so there is no
# frontend DB to migrate at boot. The backend image runs Alembic migrations.
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]

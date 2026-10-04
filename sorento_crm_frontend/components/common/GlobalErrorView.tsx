'use client';

/**
 * What `app/global-error.tsx` draws. It replaces the ROOT layout, so nothing the
 * root layout provides can be assumed: not the client providers (their chunk
 * failing to load is the commonest way to land here), not the theme, and not the
 * stylesheet. Plain elements and inline styles only, and the one action that
 * recovers from a stale build: a full page load.
 */
export default function GlobalErrorView({
  digest,
  onReload,
}: {
  digest?: string;
  onReload: () => void;
}) {
  return (
    <main
      style={{
        minHeight: '100dvh',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 16,
        fontFamily: 'system-ui, -apple-system, Segoe UI, Roboto, sans-serif',
        background: '#ffffff',
        color: '#111827',
      }}
    >
      <div style={{ maxWidth: 420, width: '100%', textAlign: 'center' }}>
        <h1 style={{ fontSize: 20, fontWeight: 600, margin: '0 0 8px' }}>
          Something went wrong
        </h1>
        <p style={{ fontSize: 14, color: '#4b5563', margin: '0 0 16px' }}>
          The app could not start. This usually clears after a reload, for
          example after an update.
        </p>
        {digest ? (
          <p
            style={{
              fontSize: 12,
              fontFamily: 'monospace',
              color: '#6b7280',
              margin: '0 0 16px',
            }}
          >
            Reference: {digest}
          </p>
        ) : null}
        <button
          type="button"
          onClick={onReload}
          style={{
            fontSize: 14,
            fontWeight: 500,
            padding: '8px 16px',
            borderRadius: 6,
            border: 'none',
            background: '#111827',
            color: '#ffffff',
            cursor: 'pointer',
          }}
        >
          Reload
        </button>
      </div>
    </main>
  );
}

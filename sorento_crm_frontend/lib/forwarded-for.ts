/**
 * SORENTO-CLIENT-IP: the browser request's `X-Forwarded-For`, to pass on when the Next server
 * calls the backend for it. Without it the backend sees only this server's own address, so its
 * per-IP sign-in throttles put every user in one bucket. The backend believes the header only
 * because this server is a trusted proxy, and reads it right to left, so a value the browser
 * typed is ignored as long as the proxy in front (nginx) appends the address it saw.
 */
export function forwardedForHeader(headers: unknown): Record<string, string> {
  if (!headers) return {};
  let value: string | string[] | null | undefined;
  if (typeof (headers as Headers).get === 'function') {
    value = (headers as Headers).get('x-forwarded-for');
  } else {
    value = (headers as Record<string, string | string[] | undefined>)['x-forwarded-for'];
  }
  const joined = Array.isArray(value) ? value.join(', ') : value;
  return joined ? { 'X-Forwarded-For': joined } : {};
}

/**
 * `sha256Hex` — the client-side pre-hash `src/api/auth.ts`'s `login()` applies to a password
 * before it ever leaves the page. This is defense-in-depth on top of HTTPS, not a replacement
 * for it: the backend (`src/backend-argus/app/schemas/common.py`'s `Sha256HexDigest`) bcrypts
 * whatever digest arrives here, so this digest itself becomes the effective secret in transit.
 *
 * `crypto.subtle` is only available in a "secure context" (HTTPS, or `localhost` for dev) by
 * browser spec — calling this on a plain-HTTP production origin throws, and login simply fails.
 * That's deliberate: it nudges a production deployment toward HTTPS by construction rather than
 * by policy, instead of silently falling back to sending the raw password.
 */
export async function sha256Hex(input: string): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error(
      'crypto.subtle is unavailable — this page must be served over HTTPS (or localhost) to log in.',
    )
  }
  const bytes = new TextEncoder().encode(input)
  const digest = await globalThis.crypto.subtle.digest('SHA-256', bytes)
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, '0'))
    .join('')
}

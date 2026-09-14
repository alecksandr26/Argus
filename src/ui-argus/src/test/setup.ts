// Runs once before every test file (see vite.config.ts's `test.setupFiles`).
//
// The `/vitest` entry point (not plain '@testing-library/jest-dom') both registers the DOM
// matchers (`toBeInTheDocument`, `toHaveTextContent`, ...) against Vitest's own `expect` and
// ambiently augments its `Assertion` type — so every test file gets typed access to them for
// free, without a separate global.d.ts or a tsconfig "types" edit.
import '@testing-library/jest-dom/vitest'
import { webcrypto } from 'node:crypto'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// React Testing Library normally auto-registers this via a global `afterEach` — but
// `globals: false` (vite.config.ts) means no test-framework globals are injected, so without
// this, each render() below would stay mounted into the same jsdom `document.body` and leak
// into the next test in the same file (multiple matches for the same query, flaky selectors).
afterEach(cleanup)

// jsdom provides `window.crypto` but not `crypto.subtle` — `src/utils/crypto.ts`'s `sha256Hex`
// (used by the real login flow) would throw in every test that exercises it without this.
// Node's own `webcrypto` implements the same `SubtleCrypto` interface, so swap it in globally.
if (!globalThis.crypto?.subtle) {
  Object.defineProperty(globalThis, 'crypto', { value: webcrypto, configurable: true })
}

// `AuthContext` persists the session to localStorage — clear it between tests in the same file
// so one test's login doesn't leak into the next (same rationale as `cleanup()` above).
afterEach(() => localStorage.clear())

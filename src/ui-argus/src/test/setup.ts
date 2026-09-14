// Runs once before every test file (see vite.config.ts's `test.setupFiles`).
//
// The `/vitest` entry point (not plain '@testing-library/jest-dom') both registers the DOM
// matchers (`toBeInTheDocument`, `toHaveTextContent`, ...) against Vitest's own `expect` and
// ambiently augments its `Assertion` type — so every test file gets typed access to them for
// free, without a separate global.d.ts or a tsconfig "types" edit.
import '@testing-library/jest-dom/vitest'
import { afterEach } from 'vitest'
import { cleanup } from '@testing-library/react'

// React Testing Library normally auto-registers this via a global `afterEach` — but
// `globals: false` (vite.config.ts) means no test-framework globals are injected, so without
// this, each render() below would stay mounted into the same jsdom `document.body` and leak
// into the next test in the same file (multiple matches for the same query, flaky selectors).
afterEach(cleanup)

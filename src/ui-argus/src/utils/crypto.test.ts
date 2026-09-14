import { describe, it, expect } from 'vitest'
import { sha256Hex } from './crypto'

describe('sha256Hex', () => {
  it('matches the known SHA-256 digest of a fixed input', async () => {
    // echo -n 'password123' | sha256sum
    expect(await sha256Hex('password123')).toBe(
      'ef92b778bafe771e89245b89ecbc08a44a4e166c06659911881f383d4473e94f',
    )
  })

  it('always returns a 64-character lowercase hex string', async () => {
    const digest = await sha256Hex('anything at all')
    expect(digest).toMatch(/^[0-9a-f]{64}$/)
  })

  it('is deterministic for the same input', async () => {
    expect(await sha256Hex('same input')).toBe(await sha256Hex('same input'))
  })

  it('differs for different input', async () => {
    expect(await sha256Hex('input a')).not.toBe(await sha256Hex('input b'))
  })
})

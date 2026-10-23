import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { act, renderHook } from '@testing-library/react'
import { usePolling } from './usePolling'

let visibility: DocumentVisibilityState = 'visible'

function setVisibility(v: DocumentVisibilityState) {
  visibility = v
  act(() => {
    document.dispatchEvent(new Event('visibilitychange'))
  })
}

beforeEach(() => {
  vi.useFakeTimers()
  visibility = 'visible'
  Object.defineProperty(document, 'visibilityState', {
    configurable: true,
    get: () => visibility,
  })
})

afterEach(() => {
  vi.useRealTimers()
  visibility = 'visible'
})

describe('usePolling', () => {
  it('polls at once and then every interval', () => {
    const poll = vi.fn()
    renderHook(() => usePolling(poll, 5000))
    expect(poll).toHaveBeenCalledTimes(1)
    act(() => {
      vi.advanceTimersByTime(4999)
    })
    expect(poll).toHaveBeenCalledTimes(1)
    act(() => {
      vi.advanceTimersByTime(1)
    })
    expect(poll).toHaveBeenCalledTimes(2)
    act(() => {
      vi.advanceTimersByTime(10000)
    })
    expect(poll).toHaveBeenCalledTimes(4)
  })

  it('pauses while the tab is hidden and polls at once when it shows again', () => {
    const poll = vi.fn()
    renderHook(() => usePolling(poll, 5000))
    expect(poll).toHaveBeenCalledTimes(1)

    setVisibility('hidden')
    act(() => {
      vi.advanceTimersByTime(60000)
    })
    expect(poll).toHaveBeenCalledTimes(1)

    setVisibility('visible')
    expect(poll).toHaveBeenCalledTimes(2)
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(poll).toHaveBeenCalledTimes(3)
  })

  it('does not poll on mount in a hidden tab, only once it shows', () => {
    visibility = 'hidden'
    const poll = vi.fn()
    renderHook(() => usePolling(poll, 5000))
    act(() => {
      vi.advanceTimersByTime(20000)
    })
    expect(poll).not.toHaveBeenCalled()
    setVisibility('visible')
    expect(poll).toHaveBeenCalledTimes(1)
  })

  it('keeps polling while hidden when pauseWhenHidden is false', () => {
    visibility = 'hidden'
    const poll = vi.fn()
    renderHook(() => usePolling(poll, 1000, { pauseWhenHidden: false }))
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(poll).toHaveBeenCalledTimes(4)
  })

  it('skips a tick while the previous poll is still pending', async () => {
    let resolve!: () => void
    const poll = vi.fn(
      () =>
        new Promise<void>((r) => {
          resolve = r
        }),
    )
    renderHook(() => usePolling(poll, 1000))
    expect(poll).toHaveBeenCalledTimes(1)
    act(() => {
      vi.advanceTimersByTime(3000)
    })
    expect(poll).toHaveBeenCalledTimes(1)
    await act(async () => {
      resolve()
    })
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('a failed poll does not stop polling', async () => {
    const poll = vi.fn().mockRejectedValue(new Error('down'))
    renderHook(() => usePolling(poll, 1000))
    await act(async () => {
      await Promise.resolve()
    })
    await act(async () => {
      vi.advanceTimersByTime(1000)
    })
    expect(poll).toHaveBeenCalledTimes(2)
  })

  it('does nothing while disabled', () => {
    const poll = vi.fn()
    renderHook(() => usePolling(poll, 1000, { enabled: false }))
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    expect(poll).not.toHaveBeenCalled()
  })

  it('uses the latest poll function without restarting, and restarts on resetKey', () => {
    const first = vi.fn()
    const second = vi.fn()
    const { rerender } = renderHook(
      ({ fn, key }: { fn: () => void; key: string }) => usePolling(fn, 1000, { resetKey: key }),
      { initialProps: { fn: first, key: 'a' } },
    )
    expect(first).toHaveBeenCalledTimes(1)

    rerender({ fn: second, key: 'a' })
    expect(second).not.toHaveBeenCalled()
    act(() => {
      vi.advanceTimersByTime(1000)
    })
    expect(second).toHaveBeenCalledTimes(1)

    rerender({ fn: second, key: 'b' })
    expect(second).toHaveBeenCalledTimes(2)
  })

  it('stops on unmount', () => {
    const poll = vi.fn()
    const { unmount } = renderHook(() => usePolling(poll, 1000))
    unmount()
    act(() => {
      vi.advanceTimersByTime(5000)
    })
    setVisibility('visible')
    expect(poll).toHaveBeenCalledTimes(1)
  })
})

import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import VersionWarning from './VersionWarning'
import { versionsDiffer } from '../utils/version'

function mockHealth(body: unknown) {
  const fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => body })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('versionsDiffer', () => {
  it.each([
    ['abc123', 'def456', true],
    ['abc123', 'abc123', false],
    ['dev', 'def456', false],
    ['abc123', 'dev', false],
    ['abc123', null, false],
    ['abc123', undefined, false],
  ])('%s vs %s -> %s', (ui, backend, expected) => {
    expect(versionsDiffer(ui, backend)).toBe(expected)
  })
})

describe('VersionWarning', () => {
  it('warns when the UI and the backend are different builds', async () => {
    const fetchMock = mockHealth({ status: 'ok', version: 'def456' })
    render(<VersionWarning uiVersion="abc123" />)
    expect(await screen.findByTestId('version-warning')).toHaveTextContent(
      'This UI (abc123) and the backend (def456) are different versions — redeploy both.',
    )
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/\/health$/)
  })

  it('stays quiet when both are the same build', async () => {
    const fetchMock = mockHealth({ status: 'ok', version: 'abc123' })
    render(<VersionWarning uiVersion="abc123" />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByTestId('version-warning')).not.toBeInTheDocument()
  })

  it('stays quiet for a backend that reports no version', async () => {
    const fetchMock = mockHealth({ status: 'ok' })
    render(<VersionWarning uiVersion="abc123" />)
    await waitFor(() => expect(fetchMock).toHaveBeenCalled())
    expect(screen.queryByTestId('version-warning')).not.toBeInTheDocument()
  })

  it("doesn't ask the backend from a dev build", () => {
    const fetchMock = mockHealth({ status: 'ok', version: 'def456' })
    render(<VersionWarning uiVersion="dev" />)
    expect(fetchMock).not.toHaveBeenCalled()
    expect(screen.queryByTestId('version-warning')).not.toBeInTheDocument()
  })
})

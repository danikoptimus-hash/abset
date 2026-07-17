import { describe, expect, it, vi } from 'vitest'
import { buildExperimentPermalink, copyText, shareToastMessage } from './share'

// Кнопка Share (пакет share+folders). Тесты — чистые, без DOM: vitest здесь
// поднят с `environment: "node"` (frontend/vitest.config.ts), поэтому буфер
// обмена приходит инъекцией, а не через глобальный navigator.

describe('buildExperimentPermalink', () => {
  it('builds an id-based link, not a name-based one', () => {
    expect(buildExperimentPermalink('https://abset.corp', 'abc-123')).toBe(
      'https://abset.corp/experiments/by-id/abc-123',
    )
  })

  it('does not double the slash when origin has a trailing one', () => {
    expect(buildExperimentPermalink('https://abset.corp/', 'abc-123')).toBe(
      'https://abset.corp/experiments/by-id/abc-123',
    )
  })

  it('encodes the id', () => {
    expect(buildExperimentPermalink('https://abset.corp', 'a b/c')).toBe(
      'https://abset.corp/experiments/by-id/a%20b%2Fc',
    )
  })
})

describe('shareToastMessage', () => {
  it('warns that a draft is not openable by the recipient', () => {
    const text = shareToastMessage('draft')
    expect(text).toContain('Link copied')
    expect(text).toContain('draft')
    expect(text).toContain('only you, explicitly granted users, and Admins can open it')
  })

  it('stays plain for a published experiment', () => {
    expect(shareToastMessage('published')).toBe('Link copied')
  })
})

describe('copyText', () => {
  it('uses the clipboard API when available', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    const fallbackCopy = vi.fn().mockReturnValue(true)

    await expect(copyText('https://x/y', { writeText, fallbackCopy })).resolves.toBe(true)
    expect(writeText).toHaveBeenCalledWith('https://x/y')
    expect(fallbackCopy).not.toHaveBeenCalled()
  })

  it('falls back when the clipboard API is absent (plain-http deployment)', async () => {
    // navigator.clipboard не существует вне secure context — именно этот
    // случай ждет корпоративный стенд, пока он на http.
    const fallbackCopy = vi.fn().mockReturnValue(true)

    await expect(copyText('https://x/y', { fallbackCopy })).resolves.toBe(true)
    expect(fallbackCopy).toHaveBeenCalledWith('https://x/y')
  })

  it('falls back when the clipboard API rejects (permission denied)', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('NotAllowedError'))
    const fallbackCopy = vi.fn().mockReturnValue(true)

    await expect(copyText('https://x/y', { writeText, fallbackCopy })).resolves.toBe(true)
    expect(writeText).toHaveBeenCalled()
    expect(fallbackCopy).toHaveBeenCalledWith('https://x/y')
  })

  it('reports failure when both paths fail', async () => {
    const writeText = vi.fn().mockRejectedValue(new Error('nope'))
    const fallbackCopy = vi.fn().mockReturnValue(false)

    await expect(copyText('https://x/y', { writeText, fallbackCopy })).resolves.toBe(false)
  })
})

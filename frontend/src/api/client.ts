import createClient from 'openapi-fetch'
import type { paths } from './schema'

// Пути в src/api/schema.ts УЖЕ содержат полный префикс "/api/v1/..." (так
// openapi-typescript генерирует paths из реальной схемы FastAPI — роутеры
// подключены через include_router(prefix="/api/v1")). baseUrl поэтому пуст
// по умолчанию — не "/api/v1" (иначе получится /api/v1/api/v1/...); в проде
// frontend и /api/* на одном origin через nginx (относительный путь и так
// работает), dev-прокси на localhost:8000 настроен в vite.config.ts.
// VITE_API_BASE — задать, только если backend реально живет на другом origin.
const baseUrl = import.meta.env.VITE_API_BASE ?? ''

export const apiClient = createClient<paths>({
  baseUrl,
  credentials: 'include',
})

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> }
}

export function errorMessage(error: unknown, fallback = 'An error occurred'): string {
  if (error && typeof error === 'object' && 'error' in error) {
    const body = error as ApiErrorBody
    return body.error?.message ?? fallback
  }
  return fallback
}

/** openapi-fetch's defaultBodySerializer только пропускает body насквозь,
 * если это УЖЕ реальный FormData (body instanceof FormData) — плоский объект
 * с File-полем оно НЕ конвертирует в multipart автоматически, несмотря на то
 * что сгенерированный тип (openapi-typescript) для multipart-операций рисует
 * это как обычный object. Без ручной сборки FormData бэкенд получает
 * kind/file как "не переданы" (проверено e2e: 422 "Field required"). */
export function toFormData(fields: Record<string, string | Blob | undefined>): FormData {
  const fd = new FormData()
  for (const [key, value] of Object.entries(fields)) {
    if (value !== undefined) fd.append(key, value)
  }
  return fd
}

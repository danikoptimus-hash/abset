// Кнопка Share на странице теста: сборка ссылки, текст тоста, копирование.
//
// Почему отдельный .ts-модуль, а не всё внутри ExperimentPage.tsx: vitest в
// этом проекте настроен на `environment: "node"` и `include:
// src/**/*.test.ts` (frontend/vitest.config.ts) — то есть юнит-тестами
// покрываются ТОЛЬКО чистые функции без DOM (см. charts/
// MonitoringLineChart.tsx, тот же прием и та же причина). Поэтому здесь
// лежит всё, что можно проверить без браузера, а сам вызов остается в
// компоненте.

/** Стабильная ссылка на тест.
 *
 * НЕ `/experiments/<name>`, хотя именно так тест адресуется везде: имя
 * мутабельно, и переименование молча ломает уже разосланные ссылки
 * (CLAUDE.md, "Известный техдолг" — полная миграция адресации на uuid
 * осознанно отложена). `/experiments/by-id/<id>` — узкий permalink-вход,
 * который резолвится в текущее имя и редиректит на него, так что ссылка
 * переживает ренейм, не трогая остальную маршрутизацию.
 */
export function buildExperimentPermalink(origin: string, experimentId: string): string {
  return `${origin.replace(/\/+$/, '')}/experiments/by-id/${encodeURIComponent(experimentId)}`
}

/** Текст тоста после копирования.
 *
 * Ссылка полезна ровно настолько, насколько у получателя есть права: у
 * черновика их нет ни у кого, кроме владельца, явно приглашенных и админов
 * (abkit/access.py::can_view_experiment), поэтому про draft предупреждаем
 * СРАЗУ при копировании — иначе человек узнает об этом только от коллеги,
 * который открыл ссылку и увидел "not found".
 */
export function shareToastMessage(publicationStatus: string): string {
  if (publicationStatus === 'draft') {
    return (
      'Link copied. Note: this experiment is a draft — only you, explicitly ' +
      'granted users, and Admins can open it.'
    )
  }
  return 'Link copied'
}

/** Инъекция буфера обмена — ради тестируемости под `environment: "node"`,
 * где никакого `navigator` нет вовсе. */
export interface ClipboardDeps {
  /** navigator.clipboard.writeText, если доступен. */
  writeText?: (text: string) => Promise<void>
  /** Запасной путь: document + execCommand (см. copyText). */
  fallbackCopy?: (text: string) => boolean
}

/** Копирует текст, возвращает true при успехе.
 *
 * `navigator.clipboard` существует ТОЛЬКО в secure context (https либо
 * localhost) — на корпоративном стенде по обычному http его нет вообще
 * (`navigator.clipboard === undefined`), и без запасного пути кнопка Share
 * там молча ничего не делала бы. Поэтому: сначала асинхронный
 * clipboard API, при его отсутствии ИЛИ отказе (в т.ч. отозванного
 * разрешения) — устаревший, но повсеместно работающий execCommand('copy')
 * через временный textarea.
 */
export async function copyText(text: string, deps: ClipboardDeps = {}): Promise<boolean> {
  const writeText =
    deps.writeText ??
    (typeof navigator !== 'undefined' && navigator.clipboard
      ? navigator.clipboard.writeText.bind(navigator.clipboard)
      : undefined)

  if (writeText) {
    try {
      await writeText(text)
      return true
    } catch {
      // Проваливаемся в fallback: clipboard API мог отказать по правам, а не
      // из-за отсутствия — тогда execCommand еще может сработать.
    }
  }

  const fallbackCopy = deps.fallbackCopy ?? defaultFallbackCopy
  return fallbackCopy(text)
}

function defaultFallbackCopy(text: string): boolean {
  if (typeof document === 'undefined') return false
  const textarea = document.createElement('textarea')
  textarea.value = text
  // Вне экрана, но в DOM: execCommand копирует только из отрисованного и
  // выделенного элемента, поэтому display:none/hidden тут не годятся.
  textarea.style.position = 'fixed'
  textarea.style.top = '-1000px'
  textarea.setAttribute('readonly', '')
  document.body.appendChild(textarea)
  try {
    textarea.select()
    return document.execCommand('copy')
  } catch {
    return false
  } finally {
    document.body.removeChild(textarea)
  }
}

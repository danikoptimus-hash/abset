// Состояние вкладки SQL Lab, переживающее навигацию внутри сессии (ТЗ п.1).
//
// Почему sessionStorage, а не React-состояние выше по дереву: страница
// размонтируется при уходе на другой роут, а поднимать её состояние в
// AppLayout значило бы тащить туда знание о внутренностях одной страницы.
// sessionStorage (а не localStorage) — потому что "в рамках сессии": вкладку
// закрыли — черновик запроса не должен всплывать через неделю на общем
// компьютере. Это ЕДИНСТВЕННОЕ клиентское хранилище в проекте, и оно
// намеренно держит только черновик UI, никаких данных.

const KEY = 'abset.sqlLab.tab'

export interface SqlLabTabState {
  connectionId?: string
  schema?: string
  table?: string
  sql: string
}

const EMPTY: SqlLabTabState = { sql: '' }

export function loadSqlLabState(): SqlLabTabState {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw) as Partial<SqlLabTabState>
    // Явная нормализация: в хранилище могла остаться форма состояния от
    // предыдущей версии приложения, и падать из-за этого страница не должна.
    return {
      connectionId: typeof parsed.connectionId === 'string' ? parsed.connectionId : undefined,
      schema: typeof parsed.schema === 'string' ? parsed.schema : undefined,
      table: typeof parsed.table === 'string' ? parsed.table : undefined,
      sql: typeof parsed.sql === 'string' ? parsed.sql : '',
    }
  } catch {
    return EMPTY
  }
}

export function saveSqlLabState(state: SqlLabTabState): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(state))
  } catch {
    // Приватный режим / переполненное хранилище: потеря черновика неприятна,
    // но это не повод ронять страницу.
  }
}

export function clearSqlLabState(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* см. выше */
  }
}

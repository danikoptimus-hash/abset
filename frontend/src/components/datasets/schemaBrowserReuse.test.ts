import { describe, expect, it } from 'vitest'

/**
 * Гард против ФОРКА браузера схем.
 *
 * SQL Lab уже импортировал общий компонент — и все равно пикер таблиц был
 * пуст: сломана была ОБВЯЗКА (один общий обработчик пересобирал вторую
 * половину состояния из устаревшего замыкания и отменял выбор схемы). Отсюда
 * два разных требования, и оба проверяются здесь на уровне исходников:
 *
 *  (1) все формы рендерят ОДИН И ТОТ ЖЕ компонент — «починить свою копию»
 *      не должно быть доступным вариантом;
 *  (2) обвязка у них одинаковая — раздельные обработчики схемы и таблицы,
 *      как в CreateDatasetModal, чью версию мы считаем эталонной.
 *
 * Тест читает исходники, а не рендерит: vitest здесь поднят с
 * environment: "node" (frontend/vitest.config.ts) — DOM'а нет намеренно, и
 * ради этого гарда его заводить незачем. Исходники подтягиваются
 * `import.meta.glob(..., '?raw')`, а не через node:fs: в tsconfig приложения
 * типы Node не подключены (types: ["vite/client"]), и тащить их туда ради
 * одного теста — цена выше пользы.
 */

const SOURCES = import.meta.glob('../../{pages,components}/**/*.tsx', {
  query: '?raw',
  import: 'default',
  eager: true,
}) as Record<string, string>

const CONSUMERS = [
  'pages/SqlLab.tsx',
  'pages/datasets/CreateDatasetModal.tsx',
  'pages/datasets/EditDatasetModal.tsx',
]

const read = (rel: string): string => {
  const key = Object.keys(SOURCES).find((k) => k.endsWith(`/${rel}`))
  if (!key) throw new Error(`source not found: ${rel} (renamed? then update this guard)`)
  return SOURCES[key]
}

describe('schema/table browser is shared, not reimplemented', () => {
  it.each(CONSUMERS)('%s imports the shared SchemaTableCascade', (rel) => {
    const source = read(rel)
    expect(source).toMatch(/import\s*\{[^}]*SchemaTableCascade[^}]*\}\s*from\s*'[^']*SchemaTableCascade'/)
    expect(source).toContain('<SchemaTableCascade')
  })

  it('exactly one file in the app owns the schema/table selects', () => {
    // Сканируется ВЕСЬ pages/+components/, а не список известных файлов:
    // вторая копия компонента появится именно там, где ее сейчас никто не
    // ждет. aria-label пикера — ее опознавательный знак.
    const owners = Object.entries(SOURCES)
      .filter(([, src]) => src.includes('aria-label="from-sql-table-select"'))
      .map(([path]) => path)
    // Ключи glob'а относительны ЭТОМУ файлу, поэтому сравнение по хвосту, а
    // не по абсолютному пути.
    expect(owners).toHaveLength(1)
    expect(owners[0]).toMatch(/SchemaTableCascade\.tsx$/)
  })

  it.each(CONSUMERS)('%s passes a plain setter to onSchemaChange', (rel) => {
    // Компонент на выбор схемы вызывает ДВА обработчика подряд:
    // onSchemaChange(value), затем onTableChange(undefined). Пока в
    // onSchemaChange уходит голый сеттер, это безопасно. Стоит подставить
    // туда стрелку, пересобирающую состояние (`(next) => onCascadeChange(next,
    // undefined)`), — и второй вызов запишет в схему устаревшее значение из
    // того же рендера, отменив первый: схема сбросится в тот же тик, запрос
    // таблиц не уйдет, пикер таблиц останется пустым. Ровно так и было в
    // SQL Lab, при том что компонент импортировался общий.
    //
    // Поэтому требование конкретное: сеттер, а не стрелка. Правило грубое,
    // зато его нарушение видно глазами в ревью и здесь.
    const schemaProp = read(rel).match(/onSchemaChange=\{([^}]*)\}/)?.[1]?.trim()
    expect(schemaProp, `${rel}: onSchemaChange must receive a state setter`).toMatch(
      /^[A-Za-z_$][\w$]*$/,
    )
  })
})

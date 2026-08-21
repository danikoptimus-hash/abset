import { useEffect, useState } from 'react'
import { Alert, DatePicker, Space, Typography } from 'antd'
import dayjs from 'dayjs'
import { apiClient, errorMessage } from '../../api/client'

export interface SqlDateParamsValue {
  dateFrom: string | null
  dateTo: string | null
}

export interface SqlParamsInfo {
  placeholders: string[]
  requiresDateFrom: boolean
  requiresDateTo: boolean
  error: string | null
}

/**
 * Разбор плейсхолдеров ЖИВЁТ НА СЕРВЕРЕ (abkit/db_connections/sql_params.py),
 * а не дублируется регуляркой на клиенте: правило «разрешены только эти два
 * имени» одно, и второй его экземпляр рано или поздно разошёлся бы с первым.
 * Клиент лишь спрашивает «что в этом запросе» и получает заодно валидацию —
 * неизвестное имя видно СРАЗУ, а не после долгой материализации.
 */
export function useSqlParams(sql: string): SqlParamsInfo {
  const [info, setInfo] = useState<SqlParamsInfo>({
    placeholders: [], requiresDateFrom: false, requiresDateTo: false, error: null,
  })

  useEffect(() => {
    if (!sql.trim() || !sql.includes('{{')) {
      setInfo({ placeholders: [], requiresDateFrom: false, requiresDateTo: false, error: null })
      return
    }
    let cancelled = false
    // Небольшая задержка: запрос правят посимвольно, и дёргать сервер на
    // каждый символ незачем — половина промежуточных состояний заведомо
    // невалидна («{{dat»).
    const timer = setTimeout(async () => {
      const { data, error } = await apiClient.POST('/api/v1/datasets/inspect-sql-params', {
        body: { sql },
      })
      if (cancelled) return
      if (error) {
        setInfo({
          placeholders: [], requiresDateFrom: false, requiresDateTo: false,
          error: errorMessage(error, 'Invalid placeholder'),
        })
        return
      }
      setInfo({
        placeholders: data.placeholders,
        requiresDateFrom: data.requires_date_from,
        requiresDateTo: data.requires_date_to,
        error: null,
      })
    }, 400)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [sql])

  return info
}

/**
 * Поля дат, появляющиеся ТОЛЬКО когда в запросе есть плейсхолдеры (ТЗ п.2).
 * Показывать их всегда значило бы предлагать заполнить то, что ни на что не
 * влияет.
 */
export function SqlDateParamsFields({
  info,
  value,
  onChange,
  title = 'Parameter values for this snapshot',
}: {
  info: SqlParamsInfo
  value: SqlDateParamsValue
  onChange: (next: SqlDateParamsValue) => void
  title?: string
}) {
  if (info.error) {
    return <Alert type="error" showIcon message={info.error} style={{ marginBottom: 12 }} />
  }
  if (!info.requiresDateFrom && !info.requiresDateTo) return null

  return (
    <div style={{ marginBottom: 12 }}>
      <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 4, fontSize: 13 }}>
        {title}
      </Typography.Text>
      <Space wrap>
        {info.requiresDateFrom && (
          <DatePicker
            aria-label="param-date-from"
            placeholder="date_from"
            value={value.dateFrom ? dayjs(value.dateFrom) : null}
            onChange={(d) => onChange({ ...value, dateFrom: d ? d.format('YYYY-MM-DD') : null })}
          />
        )}
        {info.requiresDateTo && (
          <DatePicker
            aria-label="param-date-to"
            placeholder="date_to"
            value={value.dateTo ? dayjs(value.dateTo) : null}
            onChange={(d) => onChange({ ...value, dateTo: d ? d.format('YYYY-MM-DD') : null })}
          />
        )}
      </Space>
      <Typography.Text type="secondary" style={{ display: 'block', marginTop: 4, fontSize: 12 }}>
        The snapshot is built with these values, and they are stored with the dataset —
        Refresh re-runs the query with the same period unless you pick new dates.
      </Typography.Text>
    </div>
  )
}

/** Заполнены ли обязательные для этого запроса даты. */
export function sqlParamsComplete(info: SqlParamsInfo, value: SqlDateParamsValue): boolean {
  if (info.error) return false
  if (info.requiresDateFrom && !value.dateFrom) return false
  if (info.requiresDateTo && !value.dateTo) return false
  return true
}

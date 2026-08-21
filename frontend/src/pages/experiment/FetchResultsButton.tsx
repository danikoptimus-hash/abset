import { useState } from 'react'
import { Alert, Button, DatePicker, Modal, Progress, Typography } from 'antd'
import { CloudDownloadOutlined } from '@ant-design/icons'
import dayjs from 'dayjs'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'
import { useJobPolling } from '../../api/useJobPolling'

/**
 * «Fetch results dataset» — замыкает цикл жизни теста (ТЗ п.3).
 *
 * Дизайн считали на базовом окне, тест отработал; результаты собираются ТЕМ ЖЕ
 * запросом, но за период теста. Раньше это означало «скопировать запрос, руками
 * поправить две даты» — с тихой ошибкой, если поправил одну.
 *
 * Кнопки НЕТ ВОВСЕ, когда собрать нечего (тест не завершён, датасет дизайна не
 * из SQL, в запросе нет плейсхолдеров, у теста нет дат). Задизейбленная кнопка
 * с загадкой хуже отсутствующей — прямое требование ТЗ. Решает сервер
 * (GET .../results-dataset): условие составное и целиком серверное.
 */
export function FetchResultsButton({
  experimentName,
  onFetched,
}: {
  experimentName: string
  // Готовый датасет сразу выбирается в форме анализа — иначе пользователь
  // остался бы искать его в списке, что и есть та ручная работа, которую эта
  // кнопка убирает.
  onFetched: (datasetId: string) => void
}) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [dateFrom, setDateFrom] = useState<string | null>(null)
  const [dateTo, setDateTo] = useState<string | null>(null)
  const { phase, stage, error, poll, reset } = useJobPolling<{
    dataset_id: string
    dataset_name: string
    n_rows: number
  }>()

  const { data: info } = useQuery({
    queryKey: queryKeys.experimentResultsFetchInfo(experimentName),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/results-dataset', {
        params: { path: { name: experimentName } },
      })
      if (error) return null
      return data
    },
  })

  if (!info?.available) return null

  const openDialog = () => {
    // Предзаполняем датами теста — но оставляем редактируемыми (ТЗ п.3):
    // период сбора данных не всегда совпадает с формальными датами теста
    // (лаг атрибуции, неполный последний день).
    setDateFrom(info.date_from ?? null)
    setDateTo(info.date_to ?? null)
    reset()
    setOpen(true)
  }

  const run = async () => {
    if (!dateFrom || !dateTo) return
    const { data, error: startError } = await apiClient.POST(
      '/api/v1/experiments/{name}/results-dataset',
      {
        params: { path: { name: experimentName } },
        body: { date_from: dateFrom, date_to: dateTo },
      },
    )
    if (startError) return
    const result = await poll(data.job_id)
    if (result) {
      setOpen(false)
      queryClient.invalidateQueries({ queryKey: queryKeys.datasetsAll() })
      queryClient.invalidateQueries({ queryKey: queryKeys.datasetsForSelect() })
      onFetched(result.dataset_id)
    }
  }

  const running = phase === 'running'

  return (
    <>
      <Button icon={<CloudDownloadOutlined />} onClick={openDialog} block style={{ marginBottom: 12 }}>
        Fetch results dataset
      </Button>
      <Modal
        title="Fetch results dataset"
        open={open}
        onCancel={() => (running ? undefined : setOpen(false))}
        okText={running ? 'Fetching…' : 'Fetch'}
        okButtonProps={{ disabled: !dateFrom || !dateTo || running, loading: running }}
        onOk={run}
      >
        <Typography.Paragraph type="secondary">
          Runs the same query your design dataset uses, with the test period substituted for{' '}
          <code>{'{{date_from}}'}</code> and <code>{'{{date_to}}'}</code>. The result is saved as a
          new dataset and selected below.
        </Typography.Paragraph>
        <div style={{ display: 'flex', gap: 12, marginBottom: 12 }}>
          <DatePicker
            aria-label="results-date-from"
            value={dateFrom ? dayjs(dateFrom) : null}
            onChange={(d) => setDateFrom(d ? d.format('YYYY-MM-DD') : null)}
            disabled={running}
          />
          <DatePicker
            aria-label="results-date-to"
            value={dateTo ? dayjs(dateTo) : null}
            onChange={(d) => setDateTo(d ? d.format('YYYY-MM-DD') : null)}
            disabled={running}
          />
        </div>
        {running && (
          <div style={{ marginBottom: 12 }}>
            <Progress percent={undefined} status="active" showInfo={false} />
            <Typography.Text>{stage ?? 'Starting…'}</Typography.Text>
          </div>
        )}
        {phase === 'failed' && error && <Alert type="error" showIcon message={error} />}
      </Modal>
    </>
  )
}

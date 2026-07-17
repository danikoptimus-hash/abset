import { useParams, Navigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { Spin, Result } from 'antd'
import { apiClient } from '../../api/client'
import { queryKeys } from '../../api/queryKeys'

// Точка входа permalink'а (кнопка Share): /experiments/by-id/:id -> резолв
// текущего имени -> redirect на канонический /experiments/<name>.
//
// Существует потому, что тест адресуется мутабельным именем, и ссылка на
// /experiments/<name> ломается при переименовании (CLAUDE.md, "Известный
// техдолг"). Это НЕ миграция адресации на uuid — остальные маршруты не
// тронуты; здесь ровно один дополнительный вход, который ее переживает.
export function ExperimentByIdRedirect() {
  const { id } = useParams<{ id: string }>()

  const { data, isLoading, error } = useQuery({
    queryKey: queryKeys.experimentById(id),
    queryFn: async () => {
      const { data, error } = await apiClient.GET('/api/v1/experiments/by-id/{experiment_id}', {
        params: { path: { experiment_id: id! } },
      })
      if (error) throw new Error('not found')
      return data
    },
    enabled: !!id,
    retry: false,
  })

  if (isLoading) return <Spin size="large" />
  // Тот же экран, что у самой страницы теста при 404: получатель ссылки без
  // доступа (например, на чужой черновик) должен увидеть внятное "не
  // найдено", а не пустой экран. Сервер намеренно отвечает 404, а не 403 —
  // существование чужого черновика не подтверждается даже отказом.
  if (error || !data) return <Result status="404" title="Experiment not found" />

  return <Navigate to={`/experiments/${encodeURIComponent(data.name)}`} replace />
}

import { apiClient } from '../../api/client'
import type { AnalysisResultsOut } from './analyzeTypes'

// Shared query key/fetcher for GET .../results — both the Analysis tab
// (AnalyzeSection) and the Results tab (ResultsSection) subscribe to the
// same react-query cache entry, so whichever mounts first fetches and a
// fresh analyze run (invalidateQueries) refreshes both at once.
export function experimentResultsQueryKey(experimentName: string) {
  return ['experiment-results', experimentName]
}

export async function fetchExperimentResults(experimentName: string): Promise<AnalysisResultsOut | null> {
  const { data, error } = await apiClient.GET('/api/v1/experiments/{name}/results', {
    params: { path: { name: experimentName } },
  })
  if (error) return null
  return data as unknown as AnalysisResultsOut
}

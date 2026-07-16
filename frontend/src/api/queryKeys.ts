// UX contract, part B: every TanStack Query key used anywhere in the app is
// defined here, as a factory function, instead of an inline array literal
// at each call site. Every `useQuery` reads its key from here, and every
// mutation that should invalidate a query MUST invalidate it via the same
// factory (not a hand-typed literal that can silently drift out of sync
// with the read side — this is exactly how the "created tag doesn't show
// up in the filter without reload" bug happened: the read side used
// ['tags-typeahead', search] and nothing, anywhere, ever invalidated it).
//
// When adding a new useQuery, add its key here first. When adding a
// mutation, check this file for every key your change could affect and
// invalidate all of them — a mutation with no invalidation call is a
// defect, not a shortcut (see CLAUDE.md, "Правило: свежесть данных после
// мутаций").
export const queryKeys = {
  // Tags
  tagsTypeahead: (search: string) => ['tags-typeahead', search] as const,
  // Prefix-only (no `search` segment) — TanStack Query's invalidateQueries
  // matches any cached query whose key STARTS WITH the given array, so this
  // invalidates every tagsTypeahead(...) entry regardless of what the user
  // had typed into the search box, in one call.
  tagsTypeaheadAll: () => ['tags-typeahead'] as const,
  adminTags: (q: string) => ['admin-tags', q] as const,
  adminTagsAll: () => ['admin-tags'] as const,

  // Datasets
  datasets: (page: number, q: string, source: string | undefined) => ['datasets', page, q, source] as const,
  datasetsAll: () => ['datasets'] as const,
  datasetPreview: (id: string | null | undefined) => ['dataset-preview', id] as const,
  datasetsForSelect: () => ['datasets-for-select'] as const,
  datasetUsage: (id: string | undefined) => ['dataset-usage', id] as const,
  datasetsBulkUsage: (ids: string[]) => ['datasets-bulk-usage', ids] as const,
  datasetColumnValues: (id: string | undefined, groupColumn: string | undefined) =>
    ['dataset-column-values', id, groupColumn] as const,
  datasetDuplicateCheck: (id: string | undefined, unitCol: string | undefined) =>
    ['dataset-duplicate-check', id, unitCol] as const,

  // Database connections
  adminDbConnections: () => ['admin-db-connections'] as const,
  dbConnectionsForSqlDataset: () => ['db-connections-for-sql-dataset'] as const,
  dbConnectionSchemas: (connectionId: string | undefined) => ['db-connection-schemas', connectionId] as const,
  dbConnectionTables: (connectionId: string | undefined, schema: string | undefined) =>
    ['db-connection-tables', connectionId, schema] as const,

  // Experiments (list, detail, sub-resources)
  experiments: (filters: unknown) => ['experiments', filters] as const,
  experimentsAll: () => ['experiments'] as const,
  experiment: (name: string) => ['experiment', name] as const,
  experimentBlocks: (name: string) => ['experiment-blocks', name] as const,
  experimentProperties: (name: string | null) => ['experiment-properties', name] as const,
  experimentAudit: (name: string, page: number) => ['experiment-audit', name, page] as const,
  experimentResults: (name: string) => ['experiment-results', name] as const,
  experimentDesignDataset: (name: string | undefined) => ['experiment-design-dataset', name] as const,
  experimentDesignDatasetPreview: (id: string | undefined) => ['experiment-design-dataset-preview', id] as const,
  experimentSamples: (name: string) => ['experiment-samples', name] as const,
  flowImages: (name: string) => ['flow-images', name] as const,

  // Validation
  experimentsForValidation: () => ['experiments-for-validation'] as const,
  experimentForValidation: (name: string | undefined) => ['experiment-for-validation', name] as const,

  // Design wizard
  activeExperimentsForIsolation: () => ['active-experiments-for-isolation'] as const,

  // Users / admin
  adminUsers: () => ['admin-users'] as const,
  usersPicker: () => ['users-picker'] as const,
  audit: (filters: unknown) => ['audit', filters] as const,

  // Admin monitoring panel
  monitoringCurrent: () => ['monitoring-current'] as const,
  monitoringHistory: (from: string, to: string, resolution: string) =>
    ['monitoring-history', from, to, resolution] as const,

  // App chrome
  version: () => ['version'] as const,
  authConfig: () => ['auth-config'] as const,
}

import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { apiClient, errorMessage } from '../api/client'
import type { components } from '../api/schema'

export type CurrentUser = components['schemas']['UserOut']

/** Per-user UI-настройки (пакет share+folders — пока одна). Новая настройка
 * добавляется полем сюда и в UpdatePreferencesRequest на бэке. */
export type PreferencePatch = Partial<
  Pick<CurrentUser, 'folders_panel_collapsed' | 'strata_balance_expanded' | 'strata_power_expanded'>
>

interface AuthContextValue {
  user: CurrentUser | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
  updatePreferences: (patch: PreferencePatch) => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)
  const queryClient = useQueryClient()

  const refresh = useCallback(async () => {
    const { data } = await apiClient.GET('/api/v1/auth/me')
    setUser(data ?? null)
  }, [])

  useEffect(() => {
    refresh().finally(() => setLoading(false))
  }, [refresh])

  const login = useCallback(async (email: string, password: string) => {
    const { data, error } = await apiClient.POST('/api/v1/auth/login', {
      body: { email, password },
    })
    if (error) {
      throw new Error(errorMessage(error, 'Invalid email or password'))
    }
    setUser(data)
  }, [])

  /** UI-настройки текущего пользователя.
   *
   * Настройки живут ЗДЕСЬ, а не в локальном состоянии компонента, именно
   * потому, что AuthProvider смонтирован выше роутера (main.tsx): компонент,
   * который развернет панель и уйдет на другую страницу, размонтируется, а
   * это состояние — нет. Плюс UserOut приезжает с /login и /me, так что после
   * перезагрузки значение уже на месте, без отдельного запроса.
   *
   * Оптимистично: переключатель должен срабатывать мгновенно, а не через
   * round-trip (тот же принцип, что у publication-toggle — UX-контракт (б)).
   * При ошибке откатываемся и пробрасываем ее наверх: показать тост — дело
   * вызывающего компонента, AuthContext про UI ничего не знает.
   */
  const updatePreferences = useCallback(
    async (patch: PreferencePatch) => {
      const previous = user
      if (!previous) return
      setUser({ ...previous, ...patch })
      const { data, error } = await apiClient.PATCH('/api/v1/auth/me/preferences', {
        body: patch,
      })
      if (error) {
        setUser(previous)
        throw new Error(errorMessage(error, 'Could not save your preference'))
      }
      setUser(data)
    },
    [user],
  )

  const logout = useCallback(async () => {
    await apiClient.POST('/api/v1/auth/logout')
    setUser(null)
    // B.1 audit gap: without this, a second user signing in on the same
    // browser session could see the first user's cached experiments/
    // datasets/admin data for a moment (or indefinitely, for staleTime:
    // Infinity queries like `version`) before any of it happens to refetch.
    queryClient.clear()
  }, [queryClient])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh, updatePreferences }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}

const ROLE_ORDER: Record<string, number> = { viewer: 0, editor: 1, admin: 2 }

export function hasMinRole(user: CurrentUser | null, minRole: string): boolean {
  if (!user) return false
  return ROLE_ORDER[user.role] >= ROLE_ORDER[minRole]
}

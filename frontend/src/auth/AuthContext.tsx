import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { apiClient, errorMessage } from '../api/client'
import type { components } from '../api/schema'

export type CurrentUser = components['schemas']['UserOut']

interface AuthContextValue {
  user: CurrentUser | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => Promise<void>
  refresh: () => Promise<void>
}

const AuthContext = createContext<AuthContextValue | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<CurrentUser | null>(null)
  const [loading, setLoading] = useState(true)

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

  const logout = useCallback(async () => {
    await apiClient.POST('/api/v1/auth/logout')
    setUser(null)
  }, [])

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refresh }}>
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

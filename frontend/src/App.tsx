import { Navigate, Route, createRoutesFromElements } from 'react-router-dom'
import { AppLayout } from './components/AppLayout'
import { RequireAuth } from './auth/RequireAuth'
import { LoginPage } from './pages/Login'
import { ExperimentsListPage } from './pages/ExperimentsList'
import { DesignWizardPage } from './pages/DesignWizard'
import { ExperimentPage } from './pages/experiment/ExperimentPage'
import { ExperimentByIdRedirect } from './pages/experiment/ExperimentByIdRedirect'
import { DatasetsPage } from './pages/Datasets'
import { ValidationPage } from './pages/Validation'
import { AdminPage } from './pages/Admin'
import { AuditPage } from './pages/Audit'
import { ProfilePage } from './pages/Profile'
import { DatabaseConnectionsPage } from './pages/admin/DatabaseConnections'
import { TagsAdminPage } from './pages/admin/Tags'
import { MonitoringPage } from './pages/settings/MonitoringPage'

// UX contract (unsaved-changes guard, part A): createRoutesFromElements lets
// this stay ordinary JSX <Route> elements (same as the <Routes> tree this
// replaces) while producing the route OBJECTS a data router needs —
// react-router's `useBlocker` (in-app route-change interception, including
// browser back/forward) only works under a data router
// (createBrowserRouter + <RouterProvider>, wired in main.tsx), not the
// plain <BrowserRouter> this app used before. Nothing below actually
// changed shape, just how it's wired up in main.tsx.
export const routes = createRoutesFromElements(
  <>
    <Route path="/login" element={<LoginPage />} />
    <Route
      element={
        <RequireAuth>
          <AppLayout />
        </RequireAuth>
      }
    >
      <Route path="/" element={<Navigate to="/experiments" replace />} />
      <Route path="/experiments" element={<ExperimentsListPage />} />
      <Route
        path="/experiments/new"
        element={
          <RequireAuth minRole="editor">
            <DesignWizardPage />
          </RequireAuth>
        }
      />
      {/* Permalink кнопки Share: резолвится в текущее имя и редиректит на
          него, поэтому ссылка переживает переименование теста. С
          /experiments/:name не конфликтует — у того другое число сегментов
          (и react-router в любом случае ранжирует статический сегмент выше
          динамического). */}
      <Route path="/experiments/by-id/:id" element={<ExperimentByIdRedirect />} />
      <Route path="/experiments/:name" element={<ExperimentPage />} />
      <Route
        path="/experiments/:name/redesign"
        element={
          <RequireAuth minRole="editor">
            <DesignWizardPage />
          </RequireAuth>
        }
      />
      <Route path="/datasets" element={<DatasetsPage />} />
      {/* 6-part package pt.11: Validation moved into Settings > Tools;
          the old top-level URL redirects so existing links/bookmarks
          still work. */}
      <Route path="/validation" element={<Navigate to="/settings/validation" replace />} />
      <Route
        path="/settings/validation"
        element={
          <RequireAuth minRole="editor">
            <ValidationPage />
          </RequireAuth>
        }
      />
      <Route path="/profile" element={<ProfilePage />} />
      <Route
        path="/admin"
        element={
          <RequireAuth minRole="admin">
            <AdminPage />
          </RequireAuth>
        }
      />
      <Route
        path="/audit"
        element={
          <RequireAuth minRole="admin">
            <AuditPage />
          </RequireAuth>
        }
      />
      <Route
        path="/admin/db-connections"
        element={
          <RequireAuth minRole="admin">
            <DatabaseConnectionsPage />
          </RequireAuth>
        }
      />
      <Route
        path="/settings/tags"
        element={
          <RequireAuth minRole="admin">
            <TagsAdminPage />
          </RequireAuth>
        }
      />
      <Route
        path="/settings/monitoring"
        element={
          <RequireAuth minRole="admin">
            <MonitoringPage />
          </RequireAuth>
        }
      />
    </Route>
  </>,
)

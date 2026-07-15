import React from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import { AuthProvider, useAuth } from './AuthContext'
import LoginPage from './pages/LoginPage'
import Sidebar from './components/Sidebar'
import DashboardPage from './pages/DashboardPage'
import ErrorsPage from './pages/ErrorsPage'
import KnowledgeBasePage from './pages/KnowledgeBasePage'
import RunHistoryPage from './pages/RunHistoryPage'
import GeneratedFilesPage from './pages/GeneratedFilesPage'
import UsersPage from './pages/UsersPage'
import McpCallLogPage from './pages/McpCallLogPage'
import ProfilePage from './pages/ProfilePage'
import './index.css'

const ProtectedLayout: React.FC = () => {
  const { isAuthenticated } = useAuth()
  if (!isAuthenticated) return <Navigate to="/login" replace />
  return (
    <div className="admin-shell">
      <Sidebar />
      <Routes>
        <Route path="/" element={<DashboardPage />} />
        <Route path="/knowledge-base" element={<KnowledgeBasePage />} />
        <Route path="/errors" element={<ErrorsPage />} />
        <Route path="/runs" element={<RunHistoryPage />} />
        <Route path="/files" element={<GeneratedFilesPage />} />
        <Route path="/mcp" element={<McpCallLogPage />} />
        <Route path="/users" element={<UsersPage />} />
        <Route path="/profile" element={<ProfilePage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </div>
  )
}

const App: React.FC = () => {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginRouteGuard />} />
          <Route path="/*" element={<ProtectedLayout />} />
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}

// Redirect to dashboard if already logged in
const LoginRouteGuard: React.FC = () => {
  const { isAuthenticated } = useAuth()
  if (isAuthenticated) return <Navigate to="/" replace />
  return <LoginPage />
}

export default App

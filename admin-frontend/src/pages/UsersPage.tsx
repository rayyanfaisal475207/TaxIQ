import React, { useEffect, useState } from 'react'
import api from '../api'

interface AdminUser {
  id: string
  email: string
  company_name: string | null
  plan: string
  is_admin: boolean
  created_at: string
}

function fmtDate(iso: string) {
  return new Date(iso).toLocaleString('en-PK', { dateStyle: 'medium', timeStyle: 'short', hour12: true })
}

const PLAN_COLORS: Record<string, string> = {
  free: 'badge-unknown',
  pro: 'badge-rag',
  firm: 'badge-sql',
}

const UsersPage: React.FC = () => {
  const [users, setUsers] = useState<AdminUser[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  useEffect(() => {
    const fetchUsers = async () => {
      setLoading(true)
      setError('')
      try {
        const res = await api.get('/users', { params: { limit: 100 } })
        setUsers(res.data)
      } catch {
        setError('Failed to load users')
      } finally {
        setLoading(false)
      }
    }
    fetchUsers()
  }, [])

  return (
    <div className="main-content">
      <div className="page-header">
        <div>
          <div className="page-title">Users</div>
          <div className="page-subtitle">{users.length} registered account{users.length !== 1 ? 's' : ''}</div>
        </div>
      </div>

      <div className="page-body">
        <div className="table-wrapper">
          {loading && <div className="loading-state"><div className="spinner"/><span>Loading…</span></div>}
          {error && <div className="loading-state" style={{ color: 'var(--error)' }}>{error}</div>}

          {!loading && !error && (
            users.length === 0 ? (
              <div className="empty-state">No registered users yet.</div>
            ) : (
              <table>
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Company</th>
                    <th>Plan</th>
                    <th>Admin</th>
                    <th>Joined</th>
                  </tr>
                </thead>
                <tbody>
                  {users.map(u => (
                    <tr key={u.id}>
                      <td style={{ fontWeight: 500, color: 'var(--text-primary)' }}>{u.email}</td>
                      <td>{u.company_name || <span style={{ color: 'var(--text-muted)' }}>—</span>}</td>
                      <td>
                        <span className={`badge ${PLAN_COLORS[u.plan] || 'badge-unknown'}`}>
                          {u.plan}
                        </span>
                      </td>
                      <td>
                        {u.is_admin ? (
                          <span className="badge badge-sql">Yes</span>
                        ) : (
                          <span style={{ color: 'var(--text-muted)', fontSize: 12 }}>No</span>
                        )}
                      </td>
                      <td style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                        {u.created_at ? fmtDate(u.created_at) : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )
          )}
        </div>
      </div>
    </div>
  )
}

export default UsersPage

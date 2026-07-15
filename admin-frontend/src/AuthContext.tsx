import React, { createContext, useContext, useState } from 'react'

interface AuthContextType {
  isAuthenticated: boolean
  login: (user: string, pass: string) => Promise<boolean>
  logout: () => void
}

const AuthContext = createContext<AuthContextType | null>(null)

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isAuthenticated, setIsAuthenticated] = useState(() => {
    return localStorage.getItem('taxiq_admin_auth') === 'true'
  })

  const login = async (user: string, pass: string) => {
    try {
      const res = await fetch('/api/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ email: user, password: pass })
      });

      if (!res.ok) return false;

      // The login endpoint accepts ANY valid user — verify the account is
      // actually an admin before opening the admin UI, otherwise a regular
      // user sees the whole shell with every request failing 403.
      const meRes = await fetch('/api/auth/me', { credentials: 'include' });
      if (!meRes.ok) return false;
      const me = await meRes.json();
      if (!me.is_admin) {
        // Not an admin — drop the session we just created.
        await fetch('/api/auth/logout', { method: 'POST', credentials: 'include' }).catch(() => {});
        return false;
      }

      setIsAuthenticated(true);
      localStorage.setItem('taxiq_admin_auth', 'true');
      return true;
    } catch (err) {
      console.error(err);
    }
    return false;
  }

  const logout = () => {
    // Invalidate the HttpOnly cookie server-side — clearing localStorage
    // alone left a valid 7-day session behind.
    const match = document.cookie.match(new RegExp('(^| )csrf_token=([^;]+)'))
    fetch('/api/auth/logout', {
      method: 'POST',
      credentials: 'include',
      headers: match ? { 'X-CSRF-Token': match[2] } : undefined,
    }).catch(() => {})
    setIsAuthenticated(false)
    localStorage.removeItem('taxiq_admin_auth')
  }

  return (
    <AuthContext.Provider value={{ isAuthenticated, login, logout }}>
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be inside AuthProvider')
  return ctx
}

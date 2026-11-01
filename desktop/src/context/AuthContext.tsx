import React, { createContext, useContext, useEffect, useState } from 'react';
import { api, ApiError } from '../api/client';
import { User } from '../types';

interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  error: string | null;
  login: (credentials: { email: string; password: string; tenant_slug?: string }) => Promise<void>;
  logout: () => Promise<void>;
  clearError: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    async function restoreSession() {
      try {
        if (window.mediaforge) {
          const session = await window.mediaforge.auth.getSession();
          if (session && session.accessToken && session.user) {
            api.setAccessToken(session.accessToken);
            setUser(session.user);
            // Verify session is active
            try {
              const freshMe = await api.getMe();
              setUser(freshMe);
            } catch (err: any) {
              if (err instanceof ApiError && err.status === 401) {
                // Token invalid and refresh failed
                await logout();
              }
            }
          }
        }
      } catch (err) {
        console.error('Failed to restore session:', err);
      } finally {
        setIsLoading(false);
      }
    }

    restoreSession();
  }, []);

  const login = async (credentials: { email: string; password: string; tenant_slug?: string }) => {
    setIsLoading(true);
    setError(null);
    try {
      if (window.mediaforge) {
        // Main-process login: refresh token vaulted exclusively in Electron main process
        const res = await window.mediaforge.auth.login(credentials);
        if (!res.success || !res.accessToken) {
          throw new Error(res.error || 'Authentication failed');
        }
        api.setAccessToken(res.accessToken);
        setUser(res.user || null);
      } else {
        // Fallback for standalone web renderer if used outside Electron
        const tokenResp = await api.login(credentials);
        api.setAccessToken(tokenResp.access_token);
        const meResp = await api.getMe();
        setUser(meResp);
      }
    } catch (err: any) {
      const msg = err instanceof ApiError ? err.detail : err.message || 'Authentication failed';
      setError(msg);
      throw err;
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    setIsLoading(true);
    try {
      if (window.mediaforge) {
        await window.mediaforge.auth.logout();
      } else {
        await api.logout();
      }
    } catch {
      // Ignore
    }
    api.setAccessToken(null);
    setUser(null);
    setIsLoading(false);
  };

  const clearError = () => {
    setError(null);
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        error,
        login,
        logout,
        clearError,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = (): AuthContextType => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};

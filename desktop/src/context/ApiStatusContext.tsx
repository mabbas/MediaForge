import React, { createContext, useContext, useEffect, useState } from 'react';
import { api } from '../api/client';

export type ApiConnectionStatus = 'connected' | 'checking' | 'disconnected';

interface ApiStatusContextType {
  status: ApiConnectionStatus;
  version: string | null;
  error: string | null;
  checkConnection: () => Promise<void>;
}

const ApiStatusContext = createContext<ApiStatusContextType | undefined>(undefined);

export const ApiStatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [status, setStatus] = useState<ApiConnectionStatus>('checking');
  const [version, setVersion] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const checkConnection = async () => {
    try {
      const res = await api.checkHealth();
      if (res && res.status === 'healthy') {
        setStatus('connected');
        setVersion(res.version);
        setError(null);
      } else {
        setStatus('disconnected');
        setError('Server reported unhealthy status');
      }
    } catch (err: any) {
      setStatus('disconnected');
      setError(err?.detail || err?.message || 'Cannot reach API backend');
    }
  };

  useEffect(() => {
    checkConnection();
    const interval = setInterval(checkConnection, 10000);
    return () => clearInterval(interval);
  }, []);

  return (
    <ApiStatusContext.Provider
      value={{
        status,
        version,
        error,
        checkConnection,
      }}
    >
      {children}
    </ApiStatusContext.Provider>
  );
};

export function useApiStatus() {
  const context = useContext(ApiStatusContext);
  if (!context) {
    throw new Error('useApiStatus must be used within an ApiStatusProvider');
  }
  return context;
}

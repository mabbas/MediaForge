import { contextBridge, ipcRenderer } from 'electron';

// Strict Context Bridge Interface
// No Node.js globals, no fs/child_process, no unrestricted ipcRenderer exposed to renderer.
// Refresh token remains strictly confined to the Electron main process safeStorage vault.
contextBridge.exposeInMainWorld('mediaforge', {
  getAppVersion: (): Promise<string> => ipcRenderer.invoke('app:version'),
  getPlatform: (): Promise<string> => ipcRenderer.invoke('app:platform'),

  // Safe Auth Bridge:
  // - Main process handles login and credential dispatch
  // - Refresh token is NEVER returned or exposed to renderer
  // - Main process owns the single-flight refresh lock
  auth: {
    login: (credentials: {
      email: string;
      password: string;
      tenant_slug?: string;
    }): Promise<{
      success: boolean;
      accessToken?: string;
      user?: any;
      error?: string;
    }> => ipcRenderer.invoke('auth:login', credentials),

    getSession: (): Promise<{
      accessToken: string | null;
      user: any | null;
    }> => ipcRenderer.invoke('auth:get-session'),

    refreshToken: (): Promise<{
      accessToken: string;
      expiresIn: number;
    } | null> => ipcRenderer.invoke('auth:refresh-token'),

    logout: (): Promise<boolean> => ipcRenderer.invoke('auth:logout'),

    clearSession: (): Promise<boolean> => ipcRenderer.invoke('auth:clear-session'),
  },

  // Media Transfer & Per-Account Isolated Cache Bridge
  media: {
    download: (params: {
      artifactId: string;
      downloadUrl: string;
      filename: string;
      expectedSha256?: string;
    }): Promise<{ success: boolean; mediaUrl: string; filename: string }> =>
      ipcRenderer.invoke('media:download', params),

    checkCache: (
      artifactId: string
    ): Promise<{ isCached: boolean; mediaUrl?: string; sizeBytes?: number }> =>
      ipcRenderer.invoke('media:check-cache', { artifactId }),

    clearCache: (): Promise<boolean> => ipcRenderer.invoke('media:clear-cache'),

    onDownloadProgress: (
      callback: (data: {
        artifactId: string;
        percent: number;
        bytesReceived: number;
        totalBytes: number;
      }) => void
    ) => {
      const subscription = (_event: any, data: any) => callback(data);
      ipcRenderer.on('media:download-progress', subscription);
      return () => {
        ipcRenderer.removeListener('media:download-progress', subscription);
      };
    },
  },

  // Safe external links via validated OS handler
  shell: {
    openExternal: (url: string): Promise<boolean> =>
      ipcRenderer.invoke('shell:open-external', url),
  },

  // Safe backend configuration & diagnostics bridge
  backend: {
    getConfig: (): Promise<{ url: string; isLoopback: boolean; isHttps: boolean }> =>
      ipcRenderer.invoke('backend:get-config'),
    setUrl: (url: string): Promise<{ success: boolean; url?: string; sessionInvalidated?: boolean; error?: string }> =>
      ipcRenderer.invoke('backend:set-url', url),
    testConnection: (url?: string): Promise<{ online: boolean; status: number; latencyMs: number; version?: string; error?: string }> =>
      ipcRenderer.invoke('backend:test-connection', url),
  },
});

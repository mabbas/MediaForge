/**
 * Browser API client for MediaForge web interface.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:58000/api/v1";

export async function apiClient<T = any>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = endpoint.startsWith("http") ? endpoint : `${API_BASE}${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
  };

  const response = await fetch(url, {
    ...options,
    headers,
  });

  if (!response.ok) {
    let errorDetail = response.statusText;
    try {
      const data = await response.json();
      errorDetail = data.detail || data.message || errorDetail;
    } catch {
      // ignore
    }
    throw new Error(errorDetail);
  }

  return response.json();
}

export const api = {
  get: <T = any>(endpoint: string, options?: RequestInit) => apiClient<T>(endpoint, { method: "GET", ...options }),
  post: <T = any>(endpoint: string, body?: any, options?: RequestInit) =>
    apiClient<T>(endpoint, { method: "POST", body: body ? JSON.stringify(body) : undefined, ...options }),
  put: <T = any>(endpoint: string, body?: any, options?: RequestInit) =>
    apiClient<T>(endpoint, { method: "PUT", body: body ? JSON.stringify(body) : undefined, ...options }),
  delete: <T = any>(endpoint: string, options?: RequestInit) =>
    apiClient<T>(endpoint, { method: "DELETE", ...options }),

  // Specific domain helpers
  history: (params: { page_size?: number; page?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.page_size) q.set("page_size", String(params.page_size));
    if (params.page) q.set("page", String(params.page));
    return apiClient<{ entries: any[]; total: number }>(`/history?${q.toString()}`);
  },

  listLanguages: (url: string) => {
    return apiClient<{ languages: Record<string, string[]> }>(`/transcripts/languages?url=${encodeURIComponent(url)}`);
  },
};

export default api;

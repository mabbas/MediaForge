import {
  ArtifactListResponse,
  ClipExtractRequest,
  DownloadJob,
  DownloadSubmitResponse,
  ExtractTranscriptRequest,
  JobDetail,
  JobListResponse,
  JobSubmitResponse,
  MediaArtifact,
  MergeRequest,
  SubmitDownloadRequest,
  TokenResponse,
  TranscriptItem,
  TranscriptListResponse,
  User,
  TenantCommercialSubscription,
} from '../types';

export class ApiError extends Error {
  status: number;
  detail: string;
  correlationId?: string;

  constructor(status: number, detail: string, correlationId?: string) {
    super(`API Error ${status}: ${detail}`);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.correlationId = correlationId;
  }
}

class ApiClient {
  private baseUrl = 'http://127.0.0.1:58000/api/v1';
  private accessToken: string | null = null;
  private isRefreshing = false;
  private refreshPromise: Promise<string | null> | null = null;

  constructor() {
    const savedUrl = localStorage.getItem('mediaforge_backend_url');
    if (savedUrl) {
      try {
        this.setBaseUrl(savedUrl, false);
      } catch {
        // Fallback to default on invalid stored URL
        this.baseUrl = 'http://127.0.0.1:58000/api/v1';
      }
    }
  }

  getBaseUrl(): string {
    return this.baseUrl;
  }

  setBaseUrl(url: string, invalidateSessionOnOriginChange = true): void {
    const trimmed = url.replace(/\/+$/, '');
    const parsed = new URL(trimmed);
    const isLoopback =
      parsed.hostname === 'localhost' ||
      parsed.hostname === '127.0.0.1' ||
      parsed.hostname === '::1';

    if (parsed.protocol === 'http:' && !isLoopback) {
      throw new Error('Insecure HTTP is only permitted on loopback addresses (127.0.0.1/localhost).');
    }
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      throw new Error('Unsupported protocol. Use HTTP or HTTPS.');
    }

    const oldOrigin = new URL(this.baseUrl).origin;
    const newOrigin = parsed.origin;

    if (invalidateSessionOnOriginChange && oldOrigin !== newOrigin) {
      // Invalidate existing access token if origin switched
      this.accessToken = null;
      if (window.mediaforge) {
        window.mediaforge.auth.clearSession().catch(() => {});
      }
    }

    this.baseUrl = trimmed;
    localStorage.setItem('mediaforge_backend_url', trimmed);
  }

  setAccessToken(token: string | null): void {
    this.accessToken = token;
  }

  getAccessToken(): string | null {
    return this.accessToken;
  }

  private async request<T>(
    endpoint: string,
    options: RequestInit = {},
    timeoutMs = 15000,
    retryOn401 = true
  ): Promise<T> {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

    const correlationId = 'mf-desk-' + Math.random().toString(36).substring(2, 10);
    const headers = new Headers(options.headers || {});
    headers.set('Accept', 'application/json');
    headers.set('X-Correlation-ID', correlationId);

    if (this.accessToken && !headers.has('Authorization')) {
      headers.set('Authorization', `Bearer ${this.accessToken}`);
    }

    if (options.body && !(options.body instanceof FormData) && !headers.has('Content-Type')) {
      headers.set('Content-Type', 'application/json');
    }

    try {
      const url = `${this.baseUrl}${endpoint.startsWith('/') ? endpoint : '/' + endpoint}`;
      const response = await fetch(url, {
        ...options,
        headers,
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      const respCorrelationId = response.headers.get('x-correlation-id') || correlationId;

      if (response.status === 401 && retryOn401 && window.mediaforge) {
        // Concurrency-safe token refresh mutex
        const newToken = await this.refreshToken();
        if (newToken) {
          this.setAccessToken(newToken);
          return this.request<T>(endpoint, options, timeoutMs, false);
        }
      }

      if (!response.ok) {
        let errorDetail = `Request failed with status ${response.status}`;
        try {
          const errorJson = await response.json();
          if (errorJson && typeof errorJson.detail === 'string') {
            errorDetail = errorJson.detail;
          } else if (errorJson && Array.isArray(errorJson.detail)) {
            errorDetail = errorJson.detail.map((d: any) => d.msg || JSON.stringify(d)).join('; ');
          }
        } catch {
          // Non-JSON error body
        }
        throw new ApiError(response.status, errorDetail, respCorrelationId);
      }

      if (response.status === 204) {
        return {} as T;
      }

      return (await response.json()) as T;
    } catch (err: any) {
      clearTimeout(timeoutId);
      if (err.name === 'AbortError') {
        throw new ApiError(408, `Request timed out after ${timeoutMs}ms`, correlationId);
      }
      if (err instanceof ApiError) {
        throw err;
      }
      throw new ApiError(503, err?.message || 'Network connection failed / backend unavailable', correlationId);
    }
  }

  // Mutex-protected token refresh: simultaneous 401s reuse single in-flight promise
  private async refreshToken(): Promise<string | null> {
    if (this.isRefreshing && this.refreshPromise) {
      return this.refreshPromise;
    }

    this.isRefreshing = true;
    this.refreshPromise = (async () => {
      try {
        if (!window.mediaforge) return null;
        const res = await window.mediaforge.auth.refreshToken();
        if (res && res.accessToken) {
          this.accessToken = res.accessToken;
          return res.accessToken;
        }
        return null;
      } catch {
        return null;
      } finally {
        this.isRefreshing = false;
        this.refreshPromise = null;
      }
    })();

    return this.refreshPromise;
  }

  // API Methods
  async checkHealth(): Promise<{ status: string; version: string; uptime_seconds: number }> {
    return this.request<{ status: string; version: string; uptime_seconds: number }>('/health', { method: 'GET' }, 5000);
  }

  async login(credentials: { email: string; password: string; tenant_slug?: string }): Promise<TokenResponse> {
    return this.request<TokenResponse>('/auth/login', {
      method: 'POST',
      body: JSON.stringify(credentials),
    });
  }

  async getMe(): Promise<User> {
    return this.request<User>('/auth/me', { method: 'GET' });
  }

  async logout(): Promise<void> {
    try {
      await this.request('/auth/logout', { method: 'POST' });
    } finally {
      this.accessToken = null;
    }
  }

  // Downloads Endpoints
  async submitDownload(body: SubmitDownloadRequest): Promise<DownloadSubmitResponse> {
    const data = await this.request<DownloadSubmitResponse>('/downloads', {
      method: 'POST',
      body: JSON.stringify({
        url: body.url,
        mode: body.mode || 'video',
        quality: body.quality || '1080p',
        priority: body.priority || 'normal',
        start: body.start !== undefined ? body.start : true,
      }),
    });
    if (data && data.job) {
      const id = data.job.job_id || (data.job as any).id;
      data.job.job_id = id;
      data.job.id = id;
    }
    return data;
  }

  async listDownloads(): Promise<{ success: boolean; jobs: DownloadJob[]; total: number }> {
    const data = await this.request<{ success: boolean; jobs: DownloadJob[]; total: number }>('/downloads', { method: 'GET' });
    if (data && Array.isArray(data.jobs)) {
      data.jobs = data.jobs.map((j: any) => ({
        ...j,
        id: j.id || j.job_id,
        job_id: j.job_id || j.id,
      }));
    }
    return data;
  }

  getDownloadFileUrl(jobId: string): string {
    return `${this.baseUrl}/downloads/${jobId}/file`;
  }

  async startDownload(jobId: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/downloads/${jobId}/start`, { method: 'POST' });
  }

  async pauseDownload(jobId: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/downloads/${jobId}/pause`, { method: 'POST' });
  }

  async resumeDownload(jobId: string): Promise<DownloadSubmitResponse> {
    return this.request<DownloadSubmitResponse>(`/downloads/${jobId}/resume`, { method: 'POST' });
  }

  async cancelDownload(jobId: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/downloads/${jobId}/cancel`, { method: 'POST' });
  }

  async moveDownloadUp(jobId: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/downloads/${jobId}/move-up`, { method: 'POST' });
  }

  async moveDownloadDown(jobId: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(`/downloads/${jobId}/move-down`, { method: 'POST' });
  }

  // Media Artifacts
  async listArtifacts(params?: { artifact_type?: string; status?: string; limit?: number; offset?: number }): Promise<ArtifactListResponse> {
    const query = new URLSearchParams();
    if (params?.artifact_type) query.set('artifact_type', params.artifact_type);
    if (params?.status) query.set('status', params.status);
    if (params?.limit) query.set('limit', params.limit.toString());
    if (params?.offset) query.set('offset', params.offset.toString());
    const qs = query.toString() ? `?${query.toString()}` : '';
    return this.request<ArtifactListResponse>(`/artifacts${qs}`, { method: 'GET' });
  }

  async getArtifact(id: string): Promise<MediaArtifact> {
    return this.request<MediaArtifact>(`/artifacts/${id}`, { method: 'GET' });
  }

  getArtifactDownloadUrl(id: string): string {
    return `${this.baseUrl}/artifacts/${id}/download`;
  }

  // Background Media Jobs (Clips & Merges)
  async createClipJob(data: ClipExtractRequest, idempotencyKey?: string): Promise<JobSubmitResponse> {
    const headers: Record<string, string> = {};
    if (idempotencyKey) {
      headers['X-Idempotency-Key'] = idempotencyKey;
    }
    return this.request<JobSubmitResponse>('/jobs/clips', {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
  }

  async createMergeJob(data: MergeRequest, idempotencyKey?: string): Promise<JobSubmitResponse> {
    const headers: Record<string, string> = {};
    if (idempotencyKey) {
      headers['X-Idempotency-Key'] = idempotencyKey;
    }
    return this.request<JobSubmitResponse>('/jobs/merges', {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
  }

  async listJobs(params?: { status?: string; operation_type?: string; limit?: number; offset?: number }): Promise<JobListResponse> {
    const query = new URLSearchParams();
    if (params?.status) query.set('status', params.status);
    if (params?.operation_type) query.set('operation_type', params.operation_type);
    if (params?.limit) query.set('limit', params.limit.toString());
    if (params?.offset) query.set('offset', params.offset.toString());
    const qs = query.toString() ? `?${query.toString()}` : '';
    return this.request<JobListResponse>(`/jobs${qs}`, { method: 'GET' });
  }

  async getJobDetail(jobId: string): Promise<JobDetail> {
    return this.request<JobDetail>(`/jobs/${jobId}`, { method: 'GET' });
  }

  async cancelJob(jobId: string): Promise<{ success: boolean; job_id: string; status: string }> {
    return this.request<{ success: boolean; job_id: string; status: string }>(`/jobs/${jobId}/cancel`, {
      method: 'POST',
    });
  }

  // Transcripts Endpoints
  async extractTranscript(body: ExtractTranscriptRequest): Promise<{ content: string; format: string }> {
    return this.request<{ content: string; format: string }>('/transcripts/extract', {
      method: 'POST',
      body: JSON.stringify(body),
    });
  }

  async listTranscripts(params?: { language?: string; page?: number; page_size?: number }): Promise<TranscriptListResponse> {
    const query = new URLSearchParams();
    if (params?.language) query.set('language', params.language);
    if (params?.page) query.set('page', params.page.toString());
    if (params?.page_size) query.set('page_size', params.page_size.toString());
    const qs = query.toString() ? `?${query.toString()}` : '';
    return this.request<TranscriptListResponse>(`/transcripts${qs}`, { method: 'GET' });
  }

  async listTranscriptLanguages(url: string): Promise<{ success: boolean; languages: Record<string, string[]> }> {
    return this.request<{ success: boolean; languages: Record<string, string[]> }>(
      `/transcripts/languages?url=${encodeURIComponent(url)}`,
      { method: 'GET' }
    );
  }

  async getTranscript(transcriptId: string): Promise<TranscriptItem> {
    return this.request<TranscriptItem>(`/transcripts/${transcriptId}`, { method: 'GET' });
  }

  // Commercial Subscription & Quota Visibility (STEP 04-A1)
  async getSubscription(): Promise<TenantCommercialSubscription> {
    return this.request<TenantCommercialSubscription>('/usage/subscription', { method: 'GET' });
  }
}

export const api = new ApiClient();

export interface User {
  user_id: string;
  tenant_id: string;
  email: string | null;
  display_name: string | null;
  role: string;
  tier: string;
  auth_method: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  refresh_token?: string;
  session_id?: string;
}

export interface MediaArtifact {
  id: string;
  tenant_id: string;
  user_id?: string | null;
  api_key_id?: string | null;
  artifact_type: 'clip' | 'merge' | string;
  source_download_id?: string | null;
  parent_artifact_id?: string | null;
  storage_path: string;
  filename: string;
  format: string;
  duration_seconds: number;
  file_size_bytes: number;
  file_size_human: string;
  sha256_checksum?: string | null;
  status: 'pending' | 'processing' | 'ready' | 'failed' | 'deleted' | string;
  error_message?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ArtifactListResponse {
  items: MediaArtifact[];
  total: number;
  limit: number;
  offset: number;
}

export interface DownloadJob {
  id: string;
  job_id?: string;
  url: string;
  title: string;
  status: string;
  progress: number;
  format?: string;
  filename?: string;
  file_size?: number;
  file_size_human?: string;
  duration?: number;
  error?: string;
  created_at?: string;
}

export interface SubmitDownloadRequest {
  url: string;
  mode?: 'video' | 'audio';
  quality?: 'best' | '1080p' | '720p' | '480p' | 'audio';
  priority?: 'high' | 'normal' | 'low';
  start?: boolean;
}

export interface DownloadJobSummary {
  job_id: string;
  id?: string;
  status: string;
  url?: string;
  progress_percent?: number;
  priority?: string;
  title?: string;
}

export interface DownloadSubmitResponse {
  success: boolean;
  message: string;
  job: DownloadJobSummary;
}

export interface JobDetail {
  job_id: string;
  tenant_id: string;
  operation_type: 'clip_extract' | 'merge' | string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled' | string;
  progress_percent: number;
  attempt_count: number;
  max_attempts: number;
  claimed_by?: string | null;
  cancellation_requested: boolean;
  result_artifact_id?: string | null;
  result_artifact?: MediaArtifact | null;
  error_code?: string | null;
  error_message?: string | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface JobListResponse {
  success: boolean;
  tenant_id: string;
  total: number;
  page: number;
  page_size: number;
  jobs: JobDetail[];
}

export interface JobSubmitResponse {
  success: boolean;
  message: string;
  job_id: string;
  status: string;
  status_url: string;
}

export interface ClipExtractRequest {
  source: string;
  start_time: string;
  end_time: string;
  mode: 'precise' | 'fast';
  output_format?: 'mp4' | 'mkv' | 'webm';
  resolution?: string;
  video_bitrate?: string;
  audio_bitrate?: string;
}

export interface MergeRequest {
  clip_ids?: string[];
  clips?: string[];
  output_format?: 'mp4' | 'mkv' | 'webm';
  mode?: 'auto' | 'concat' | 'reencode';
  resolution?: string;
  transition?: string;
}

export interface TranscriptItem {
  id: string;
  url: string;
  language: string;
  source: string;
  format: string;
  word_count: number;
  segment_count: number;
  duration_seconds: number;
  created_at?: string;
}

export interface ExtractTranscriptRequest {
  url: string;
  language?: string;
  format?: 'srt' | 'vtt' | 'txt' | 'json';
  script?: 'default' | 'roman' | 'native' | string;
}

export interface TranscriptListResponse {
  success: boolean;
  transcripts: TranscriptItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface QuotaCheck {
  metric: string;
  unit: string;
  allowed: boolean;
  current_value: number;
  limit_value: number | null;
  remaining_value: number | null;
  is_unlimited: boolean;
  is_exceeded: boolean;
  reason_code: string;
  message: string;
}

export interface TenantCommercialSubscription {
  tenant_id: string;
  plan_code: string;
  plan_name: string;
  plan_version: number;
  tier_type: string;
  subscription_status: string;
  is_trial: boolean;
  trial_started_at: string | null;
  trial_expires_at: string | null;
  trial_days_remaining: number | null;
  current_period_start: string;
  current_period_end: string | null;
  period_reset_at: string | null;
  grace_period_expires_at?: string | null;
  grace_period_remaining_days?: number | null;
  scheduled_transition?: {
    id: string;
    transition_type: string;
    scheduled_at: string;
    target_plan_id: string | null;
    target_plan_code?: string | null;
    target_plan_name?: string | null;
    reason?: string;
  } | null;
  is_suspended?: boolean;
  suspension_reason?: string | null;
  is_advisory: boolean;
  checks: Record<string, QuotaCheck>;
  consumption: Record<string, any>;
  effective_entitlements: Record<string, any>;
}

declare global {
  interface Window {
    mediaforge?: {
      getAppVersion: () => Promise<string>;
      getPlatform: () => Promise<string>;
      auth: {
        login: (credentials: {
          email: string;
          password: string;
          tenant_slug?: string;
        }) => Promise<{
          success: boolean;
          accessToken?: string;
          user?: User;
          error?: string;
        }>;
        getSession: () => Promise<{ accessToken: string | null; user: User | null }>;
        clearSession: () => Promise<boolean>;
        refreshToken: () => Promise<{ accessToken: string; expiresIn: number } | null>;
        logout: () => Promise<boolean>;
      };
      media: {
        download: (params: {
          artifactId: string;
          downloadUrl: string;
          filename: string;
          tenantId?: string;
          expectedSha256?: string;
        }) => Promise<{ success: boolean; mediaUrl: string; filename: string }>;
        checkCache: (
          artifactId: string,
          tenantId?: string
        ) => Promise<{ isCached: boolean; mediaUrl?: string; sizeBytes?: number }>;
        clearCache: () => Promise<boolean>;
        onDownloadProgress: (
          callback: (data: { artifactId: string; percent: number; bytesReceived: number; totalBytes: number }) => void
        ) => () => void;
      };
      shell: {
        openExternal: (url: string) => Promise<boolean>;
      };
      backend: {
        getConfig: () => Promise<{ url: string; isLoopback: boolean; isHttps: boolean }>;
        setUrl: (url: string) => Promise<{ success: boolean; url?: string; sessionInvalidated?: boolean; error?: string }>;
        testConnection: (url?: string) => Promise<{ online: boolean; status: number; latencyMs: number; version?: string; error?: string }>;
      };
    };
  }
}

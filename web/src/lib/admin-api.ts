/**
 * Admin API Client for MediaForge Super Admin Portal.
 * Interacts with Next.js BFF proxy routes (/api/admin/...) and auth routes (/api/auth/...).
 * Automatically includes CSRF tokens for mutating operations.
 */

function getCsrfToken(): string | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp("(^|;\\s*)mf_admin_csrf=([^;]+)"));
  return match ? decodeURIComponent(match[2]) : null;
}

export async function adminFetch<T = any>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const url = endpoint.startsWith("http") ? endpoint : `/api/admin${endpoint.startsWith("/") ? "" : "/"}${endpoint}`;
  const method = (options.method || "GET").toUpperCase();

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(options.headers as Record<string, string> || {}),
  };

  if (["POST", "PUT", "DELETE", "PATCH"].includes(method)) {
    const csrf = getCsrfToken();
    if (csrf) {
      headers["X-CSRF-Token"] = csrf;
      headers["X-Admin-CSRF-Token"] = csrf;
    }
  }

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

export const adminApi = {
  // Authentication
  login: async (email: string, password: string) => {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({ detail: "Login failed" }));
      throw new Error(data.detail || "Login failed");
    }
    return res.json();
  },

  getSession: async () => {
    const res = await fetch("/api/auth/session");
    if (!res.ok) {
      return { authenticated: false, user: null };
    }
    return res.json();
  },

  logout: async () => {
    const res = await fetch("/api/auth/logout", { method: "POST" });
    return res.json();
  },

  // Dashboard stats
  getDashboard: () => adminFetch<{
    total_tenants: number;
    free_tenants: number;
    trial_tenants: number;
    paid_tenants: number;
    legacy_tenants: number;
    suspended_tenants: number;
    grace_period_tenants: number;
    pending_transitions: number;
    active_plans: number;
    enforcement: Record<string, any>;
    scheduler: Record<string, any>;
  }>("/dashboard"),

  // Plans catalog
  listPlans: () => adminFetch<any[]>("/plans"),
  createPlan: (data: any) => adminFetch("/plans", { method: "POST", body: JSON.stringify(data) }),
  publishPlan: (planId: string, reason: string) =>
    adminFetch(`/plans/${planId}/publish`, { method: "POST", body: JSON.stringify({ reason }) }),
  updatePricing: (planId: string, data: { price_cents: number; pricing_status: string; reason: string }) =>
    adminFetch(`/plans/${planId}/pricing`, { method: "PUT", body: JSON.stringify(data) }),
  retirePlan: (planId: string, reason: string) =>
    adminFetch(`/plans/${planId}/retire`, { method: "POST", body: JSON.stringify({ reason }) }),

  // Tenants & Subscriptions
  listTenants: (params: { search?: string; status?: string; tier?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.search) q.set("search", params.search);
    if (params.status) q.set("status", params.status);
    if (params.tier) q.set("tier", params.tier);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return adminFetch<{ items: any[]; total: number; limit: number; offset: number }>(`/tenants?${q.toString()}`);
  },

  getTenantSubscription: (tenantId: string) => adminFetch<any>(`/tenants/${tenantId}/subscription`),
  previewPlanChange: (tenantId: string, targetPlanId: string) =>
    adminFetch<any>(`/tenants/${tenantId}/subscription/preview?target_plan_id=${targetPlanId}`),
  assignPlan: (tenantId: string, data: { plan_id: string; status?: string; trial_days?: number; reason?: string }) =>
    adminFetch(`/tenants/${tenantId}/subscription/assign`, { method: "POST", body: JSON.stringify(data) }),
  scheduleTransition: (tenantId: string, data: { target_plan_id?: string; transition_type: string; scheduled_at?: string; reason: string }) =>
    adminFetch(`/tenants/${tenantId}/subscription/schedule`, { method: "POST", body: JSON.stringify(data) }),
  cancelTransition: (tenantId: string, transitionId: string, reason: string) =>
    adminFetch(`/tenants/${tenantId}/subscription/schedule/${transitionId}?reason=${encodeURIComponent(reason)}`, { method: "DELETE" }),
  extendTrial: (tenantId: string, additionalDays: number, reason: string) =>
    adminFetch(`/tenants/${tenantId}/extend-trial`, { method: "POST", body: JSON.stringify({ additional_days: additionalDays, reason }) }),
  enterGracePeriod: (tenantId: string, graceDays: number, reason: string) =>
    adminFetch(`/tenants/${tenantId}/subscription/grace-period`, { method: "POST", body: JSON.stringify({ grace_days: graceDays, reason }) }),
  exitGracePeriod: (tenantId: string, newStatus: string, reason: string) =>
    adminFetch(`/tenants/${tenantId}/subscription/exit-grace`, { method: "POST", body: JSON.stringify({ new_status: newStatus, reason }) }),
  suspendSubscription: (tenantId: string, reason: string) =>
    adminFetch(`/tenants/${tenantId}/subscription/suspend`, { method: "POST", body: JSON.stringify({ reason }) }),
  unsuspendSubscription: (tenantId: string, reason: string) =>
    adminFetch(`/tenants/${tenantId}/subscription/unsuspend`, { method: "POST", body: JSON.stringify({ reason }) }),
  getTenantOverrides: (tenantId: string) => adminFetch<any[]>(`/tenants/${tenantId}/overrides`),
  setTenantOverride: (tenantId: string, data: any) =>
    adminFetch(`/tenants/${tenantId}/overrides`, { method: "POST", body: JSON.stringify(data) }),
  revokeTenantOverride: (tenantId: string, key: string, reason: string) =>
    adminFetch(`/tenants/${tenantId}/overrides/${key}?reason=${encodeURIComponent(reason)}`, { method: "DELETE" }),
  previewQuotation: (tenantId: string, data: any) =>
    adminFetch(`/tenants/${tenantId}/subscription/quote`, { method: "POST", body: JSON.stringify(data) }),

  // Transitions & Scheduler
  listTransitions: (params: { status?: string; tenant_id?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.status) q.set("status", params.status);
    if (params.tenant_id) q.set("tenant_id", params.tenant_id);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return adminFetch<{ items: any[]; total: number; limit: number; offset: number }>(`/transitions?${q.toString()}`);
  },
  getSchedulerStatus: () => adminFetch<any>("/scheduler/status"),
  pauseScheduler: (reason: string) =>
    adminFetch("/scheduler/pause", { method: "POST", body: JSON.stringify({ reason }) }),
  resumeScheduler: (reason: string) =>
    adminFetch("/scheduler/resume", { method: "POST", body: JSON.stringify({ reason }) }),
  triggerSchedulerScan: () =>
    adminFetch("/scheduler/trigger", { method: "POST" }),
  retryFailedTransitions: (reason: string) =>
    adminFetch("/scheduler/retry-failed", { method: "POST", body: JSON.stringify({ reason }) }),

  // Audit Logs
  getAuditLogs: (params: { tenant_id?: string; action?: string; limit?: number; offset?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.tenant_id) q.set("tenant_id", params.tenant_id);
    if (params.action) q.set("action", params.action);
    if (params.limit) q.set("limit", String(params.limit));
    if (params.offset) q.set("offset", String(params.offset));
    return adminFetch<any[]>(`/audit-logs?${q.toString()}`);
  },
};

export default adminApi;

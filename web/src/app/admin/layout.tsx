"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  Shield,
  LayoutDashboard,
  Layers,
  Users,
  CalendarClock,
  Activity,
  FileSpreadsheet,
  LogOut,
  Loader2,
  Lock,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import adminApi from "@/lib/admin-api";

interface AdminUser {
  id: string;
  email: string;
  displayName?: string;
  role: string;
  tenantId: string;
}

export default function AdminLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const isLoginPage = pathname === "/admin/login";

  const [loading, setLoading] = useState(!isLoginPage);
  const [user, setUser] = useState<AdminUser | null>(null);

  useEffect(() => {
    if (isLoginPage) return;

    let mounted = true;
    adminApi
      .getSession()
      .then((res) => {
        if (!mounted) return;
        if (res.authenticated && res.user) {
          setUser(res.user);
          setLoading(false);
        } else {
          router.replace("/admin/login");
        }
      })
      .catch(() => {
        if (mounted) router.replace("/admin/login");
      });

    return () => {
      mounted = false;
    };
  }, [pathname, isLoginPage, router]);

  const handleLogout = async () => {
    try {
      await adminApi.logout();
    } finally {
      router.replace("/admin/login");
    }
  };

  if (isLoginPage) {
    return <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">{children}</div>;
  }

  if (loading) {
    return (
      <div className="min-h-screen bg-slate-950 flex flex-col items-center justify-center text-slate-300">
        <Loader2 className="h-8 w-8 animate-spin text-blue-500 mb-4" />
        <p className="text-sm font-medium">Verifying Administrator Credentials...</p>
      </div>
    );
  }

  const navItems = [
    { href: "/admin", label: "Dashboard", icon: LayoutDashboard },
    { href: "/admin/plans", label: "Plan Catalog", icon: Layers },
    { href: "/admin/tenants", label: "Tenants & Subscriptions", icon: Users },
    { href: "/admin/scheduled-transitions", label: "Scheduled Transitions", icon: CalendarClock },
    { href: "/admin/scheduler", label: "Scheduler Observability", icon: Activity },
    { href: "/admin/audit", label: "Audit Trail", icon: FileSpreadsheet },
  ];

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 flex flex-col">
      {/* Top authoritative administrative navigation bar */}
      <header className="h-16 border-b border-slate-800 bg-slate-900/90 backdrop-blur px-6 flex items-center justify-between sticky top-0 z-40">
        <div className="flex items-center space-x-3">
          <div className="h-9 w-9 rounded-lg bg-blue-600/20 border border-blue-500/40 flex items-center justify-center text-blue-400">
            <Shield className="h-5 w-5" />
          </div>
          <div>
            <div className="flex items-center space-x-2">
              <span className="font-semibold text-base tracking-tight text-white">MediaForge</span>
              <Badge variant="outline" className="border-blue-500/40 bg-blue-500/10 text-blue-400 text-xs px-2 py-0.5">
                Super Admin
              </Badge>
            </div>
            <p className="text-xs text-slate-400">Commercial Operations & Entitlements Control</p>
          </div>
        </div>

        <div className="flex items-center space-x-4">
          <div className="hidden sm:flex items-center space-x-2 text-xs text-slate-400 bg-slate-800/60 px-3 py-1.5 rounded-md border border-slate-700/50">
            <Lock className="h-3.5 w-3.5 text-emerald-400" />
            <span>Session:</span>
            <span className="text-slate-200 font-mono">{user?.email}</span>
            <Badge variant="secondary" className="bg-slate-700 text-slate-300 text-[10px] uppercase">
              {user?.role}
            </Badge>
          </div>

          <Button
            variant="ghost"
            size="sm"
            onClick={handleLogout}
            className="text-slate-400 hover:text-white hover:bg-slate-800 border border-slate-800"
          >
            <LogOut className="h-4 w-4 mr-1.5" />
            Log Out
          </Button>
        </div>
      </header>

      {/* Main Workspace with Sidebar */}
      <div className="flex-1 flex overflow-hidden">
        {/* Sidebar */}
        <aside className="w-64 border-r border-slate-800 bg-slate-900/50 p-4 space-y-1 shrink-0 hidden md:block">
          <div className="px-3 py-2 text-xs font-semibold text-slate-400 uppercase tracking-wider">
            Commercial Management
          </div>
          <nav className="space-y-1">
            {navItems.map((item) => {
              const Icon = item.icon;
              const isActive = pathname === item.href;
              return (
                <Link
                  key={item.href}
                  href={item.href}
                  className={`flex items-center space-x-3 px-3 py-2 rounded-lg text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-blue-600/20 text-blue-400 border border-blue-500/30"
                      : "text-slate-300 hover:text-white hover:bg-slate-800/60"
                  }`}
                >
                  <Icon className="h-4 w-4 shrink-0" />
                  <span>{item.label}</span>
                </Link>
              );
            })}
          </nav>

          <div className="pt-6 mt-6 border-t border-slate-800">
            <div className="px-3 py-2 text-xs font-semibold text-slate-400 uppercase tracking-wider">
              Enforcement Mode
            </div>
            <div className="px-3 py-2 bg-slate-800/40 border border-slate-800 rounded-lg text-xs space-y-1">
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Global State:</span>
                <span className="font-semibold text-amber-400">DISABLED</span>
              </div>
              <div className="flex justify-between items-center">
                <span className="text-slate-400">Allowlist:</span>
                <span className="font-mono text-slate-300">0 tenants</span>
              </div>
            </div>
          </div>
        </aside>

        {/* Content Area */}
        <main className="flex-1 overflow-y-auto p-6 bg-slate-950">
          <div className="max-w-7xl mx-auto space-y-6">{children}</div>
        </main>
      </div>
    </div>
  );
}

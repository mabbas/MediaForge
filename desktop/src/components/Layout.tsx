import React from 'react';
import {
  LayoutDashboard,
  Download,
  Film,
  Scissors,
  Layers,
  FileText,
  ListTodo,
  Settings,
  LogOut,
  User as UserIcon,
} from 'lucide-react';
import { useAuth } from '../context/AuthContext';
import { useApiStatus } from '../context/ApiStatusContext';

export type NavView =
  | 'dashboard'
  | 'downloads'
  | 'media'
  | 'clip'
  | 'merge'
  | 'transcripts'
  | 'jobs'
  | 'settings';

interface LayoutProps {
  currentView: NavView;
  onSelectView: (view: NavView) => void;
  children: React.ReactNode;
}

export const Layout: React.FC<LayoutProps> = ({ currentView, onSelectView, children }) => {
  const { user, logout } = useAuth();
  const { status, version } = useApiStatus();

  const navItems = [
    { id: 'dashboard' as NavView, label: 'Dashboard', icon: LayoutDashboard },
    { id: 'downloads' as NavView, label: 'Downloads', icon: Download },
    { id: 'media' as NavView, label: 'Media Library', icon: Film },
    { id: 'clip' as NavView, label: 'Create Clip', icon: Scissors },
    { id: 'merge' as NavView, label: 'Merge Clips', icon: Layers },
    { id: 'transcripts' as NavView, label: 'Transcripts', icon: FileText },
    { id: 'jobs' as NavView, label: 'Background Jobs', icon: ListTodo },
    { id: 'settings' as NavView, label: 'Settings', icon: Settings },
  ];

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-slate-900 text-slate-100 font-sans">
      {/* Sidebar */}
      <aside className="w-64 bg-slate-950 border-r border-slate-800 flex flex-col shrink-0 select-none">
        {/* Brand */}
        <div className="h-16 flex items-center px-6 border-b border-slate-800/80 space-x-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-emerald-500 to-teal-400 flex items-center justify-center font-bold text-slate-950 shadow-md shadow-emerald-500/20">
            MF
          </div>
          <div>
            <h1 className="font-bold text-base tracking-wide text-white">MediaForge</h1>
            <p className="text-[10px] text-emerald-400 font-medium">Desktop v0.1.0</p>
          </div>
        </div>

        {/* Navigation */}
        <nav className="flex-1 px-3 py-4 space-y-1 overflow-y-auto">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive = currentView === item.id;
            return (
              <button
                key={item.id}
                onClick={() => onSelectView(item.id)}
                className={`w-full flex items-center space-x-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                  isActive
                    ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50 border border-transparent'
                }`}
              >
                <Icon className={`w-4 h-4 ${isActive ? 'text-emerald-400' : 'text-slate-400'}`} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>

        {/* System & Connection Status in Sidebar Footer */}
        <div className="p-3 border-t border-slate-800/80 bg-slate-950/60">
          <div className="px-3 py-2 rounded-lg bg-slate-900/60 border border-slate-800 flex items-center justify-between">
            <div className="flex items-center space-x-2">
              <span className="relative flex h-2 w-2">
                {status === 'connected' && (
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                )}
                <span
                  className={`relative inline-flex rounded-full h-2 w-2 ${
                    status === 'connected'
                      ? 'bg-emerald-500'
                      : status === 'checking'
                      ? 'bg-amber-500'
                      : 'bg-rose-500'
                  }`}
                ></span>
              </span>
              <span className="text-xs text-slate-300 font-medium capitalize">
                {status === 'connected' ? 'API Online' : status === 'checking' ? 'Checking...' : 'API Offline'}
              </span>
            </div>
            {version && <span className="text-[10px] text-slate-500">v{version}</span>}
          </div>
        </div>
      </aside>

      {/* Main Container */}
      <div className="flex-1 flex flex-col h-full overflow-hidden bg-slate-900">
        {/* Header */}
        <header className="h-16 border-b border-slate-800 bg-slate-950/40 backdrop-blur px-6 flex items-center justify-between shrink-0">
          <div className="flex items-center space-x-3">
            <h2 className="text-lg font-semibold text-white capitalize">
              {navItems.find((n) => n.id === currentView)?.label || 'MediaForge'}
            </h2>
          </div>

          {/* User Profile & Actions */}
          <div className="flex items-center space-x-4">
            {user && (
              <div className="flex items-center space-x-3 bg-slate-800/60 border border-slate-700/60 rounded-full py-1.5 px-3.5">
                <div className="w-6 h-6 rounded-full bg-slate-700 flex items-center justify-center text-slate-300">
                  <UserIcon className="w-3.5 h-3.5" />
                </div>
                <div className="text-left text-xs">
                  <div className="font-medium text-slate-200 truncate max-w-[150px]">
                    {user.email || user.display_name || 'Authenticated User'}
                  </div>
                  <div className="text-[10px] text-slate-400 flex items-center space-x-1">
                    <span className="text-emerald-400 uppercase font-semibold">{user.role}</span>
                    <span>•</span>
                    <span className="capitalize">{user.tier}</span>
                  </div>
                </div>
              </div>
            )}

            <button
              onClick={() => logout()}
              title="Sign Out"
              className="p-2 rounded-lg text-slate-400 hover:text-rose-400 hover:bg-rose-500/10 border border-slate-800 hover:border-rose-500/30 transition-colors"
            >
              <LogOut className="w-4 h-4" />
            </button>
          </div>
        </header>

        {/* Dynamic Content */}
        <main className="flex-1 overflow-y-auto p-6 bg-gradient-to-b from-slate-900 to-slate-950">
          {children}
        </main>
      </div>
    </div>
  );
};

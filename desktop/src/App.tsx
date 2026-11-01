import React, { useState } from 'react';
import { AuthProvider, useAuth } from './context/AuthContext';
import { ApiStatusProvider } from './context/ApiStatusContext';
import { Layout, NavView } from './components/Layout';
import { LoginView } from './views/LoginView';
import { DashboardView } from './views/DashboardView';
import { DownloadsView } from './views/DownloadsView';
import { MediaLibraryView } from './views/MediaLibraryView';
import { ClipWorkflowView } from './views/ClipWorkflowView';
import { MergeWorkflowView } from './views/MergeWorkflowView';
import { TranscriptView } from './views/TranscriptView';
import { JobsView } from './views/JobsView';
import { SettingsView } from './views/SettingsView';
import { Loader2 } from 'lucide-react';

const MainApp: React.FC = () => {
  const { isAuthenticated, isLoading } = useAuth();
  const [currentView, setCurrentView] = useState<NavView>('dashboard');
  const [clipSource, setClipSource] = useState<{ sourcePath: string; duration?: number }>({
    sourcePath: '',
    duration: 0,
  });
  const [mergeClipIds, setMergeClipIds] = useState<string[]>([]);
  const [selectedJobId, setSelectedJobId] = useState<string | undefined>(undefined);

  if (isLoading) {
    return (
      <div className="h-screen w-screen flex flex-col items-center justify-center bg-slate-950 text-slate-400 space-y-3">
        <Loader2 className="w-8 h-8 animate-spin text-emerald-500" />
        <span className="text-xs font-medium">Initializing MediaForge Session...</span>
      </div>
    );
  }

  if (!isAuthenticated) {
    return <LoginView />;
  }

  const handleSelectForClip = (sourcePath: string, duration?: number) => {
    setClipSource({ sourcePath, duration });
    setCurrentView('clip');
  };

  const handleSelectForMerge = (clipIds: string[]) => {
    setMergeClipIds(clipIds);
    setCurrentView('merge');
  };

  const handleViewJob = (jobId: string) => {
    setSelectedJobId(jobId);
    setCurrentView('jobs');
  };

  return (
    <Layout currentView={currentView} onSelectView={setCurrentView}>
      {currentView === 'dashboard' && <DashboardView onNavigate={setCurrentView} />}
      {currentView === 'downloads' && (
        <DownloadsView onNavigate={setCurrentView} onSelectForClip={handleSelectForClip} />
      )}
      {currentView === 'media' && (
        <MediaLibraryView
          onSelectForClip={handleSelectForClip}
          onSelectForMerge={handleSelectForMerge}
          onNavigate={setCurrentView}
        />
      )}
      {currentView === 'clip' && (
        <ClipWorkflowView
          initialSource={clipSource.sourcePath}
          initialDuration={clipSource.duration}
          onNavigate={setCurrentView}
          onViewJob={handleViewJob}
        />
      )}
      {currentView === 'merge' && (
        <MergeWorkflowView
          initialSelectedClipIds={mergeClipIds}
          onNavigate={setCurrentView}
          onViewJob={handleViewJob}
        />
      )}
      {currentView === 'transcripts' && <TranscriptView />}
      {currentView === 'jobs' && <JobsView highlightJobId={selectedJobId} />}
      {currentView === 'settings' && <SettingsView />}
    </Layout>
  );
};

export const App: React.FC = () => {
  return (
    <AuthProvider>
      <ApiStatusProvider>
        <MainApp />
      </ApiStatusProvider>
    </AuthProvider>
  );
};

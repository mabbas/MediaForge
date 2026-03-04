import { create } from "zustand";

interface ConnectionState {
  apiOnline: boolean;
  wsConnected: boolean;
  reconnectAttempts: number;
  setApiOnline: (v: boolean) => void;
  setWsConnected: (v: boolean) => void;
  incrementReconnect: () => void;
  resetReconnect: () => void;
}

export const useConnectionStore = create<ConnectionState>((set) => ({
  apiOnline: true,
  wsConnected: false,
  reconnectAttempts: 0,
  setApiOnline: (v) => set({ apiOnline: v }),
  setWsConnected: (v) => set({ wsConnected: v }),
  incrementReconnect: () =>
    set((s) => ({ reconnectAttempts: s.reconnectAttempts + 1 })),
  resetReconnect: () => set({ reconnectAttempts: 0 }),
}));

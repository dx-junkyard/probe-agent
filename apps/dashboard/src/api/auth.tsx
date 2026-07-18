import { createContext, useContext, useState, useCallback, useEffect, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { setSystemId, getSystemId, api } from "./client";
import type { MeResponse, UserOut, SystemOut } from "./types";

interface AuthState {
  user: UserOut | null;
  isAdmin: boolean;
  loading: boolean;
  systemId: number | null;
  systems: SystemOut[];
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  selectSystem: (id: number) => void;
  refreshSystems: () => Promise<void>;
}

const AuthContext = createContext<AuthState>(null!);

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [user, setUser] = useState<UserOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [systemId, setSystemIdState] = useState<number | null>(getSystemId());
  const [systems, setSystems] = useState<SystemOut[]>([]);

  const fetchMe = useCallback(async () => {
    try {
      const me = await api.get<MeResponse>("/auth/me");
      setUser(me.user);
    } catch {
      setUser(null);
    }
  }, []);

  const refreshSystems = useCallback(async () => {
    try {
      const s = await api.get<SystemOut[]>("/systems");
      setSystems(s);
      if (s.length > 0 && !getSystemId()) {
        setSystemId(s[0].id);
        setSystemIdState(s[0].id);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    (async () => {
      await fetchMe();
      try {
        await refreshSystems();
      } catch { /* unauthenticated */ }
      setLoading(false);
    })();
  }, [fetchMe, refreshSystems]);

  const login = useCallback(async (username: string, password: string) => {
    await api.post<{ expires_at: number }>("/auth/login", { username, password });
    await fetchMe();
    await refreshSystems();
  }, [fetchMe, refreshSystems]);

  const logout = useCallback(async () => {
    try { await api.post("/auth/logout"); } catch { /* ignore */ }
    setSystemId(null);
    setUser(null);
    setSystems([]);
    setSystemIdState(null);
    qc.clear();
  }, [qc]);

  const selectSystem = useCallback((id: number) => {
    setSystemId(id);
    setSystemIdState(id);
    qc.invalidateQueries();
  }, [qc]);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAdmin: user?.role === "admin",
        loading,
        systemId,
        systems,
        login,
        logout,
        selectSystem,
        refreshSystems,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  return useContext(AuthContext);
}

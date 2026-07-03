import { useAuth } from "@/api/auth";
import { Select } from "@/components/ui/select";
import { Button } from "@/components/ui/button";
import { LogOut, Plus, Moon, Sun } from "lucide-react";
import { useState, useCallback } from "react";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useCreateSystem } from "@/api/hooks";
import { DiagnosticsBadge } from "@/components/diagnostics-badge";
import { toast } from "sonner";

function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "dark") {
    document.documentElement.classList.add("dark");
    return true;
  }
  return document.documentElement.classList.contains("dark");
}

const initialDark = initTheme();

function ThemeToggle() {
  const [dark, setDark] = useState(initialDark);
  const toggle = useCallback(() => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle("dark", next);
    localStorage.setItem("theme", next ? "dark" : "light");
  }, [dark]);

  return (
    <Button variant="ghost" size="icon" onClick={toggle} title="Toggle theme">
      {dark ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
    </Button>
  );
}

export function Header() {
  const { user, systems, systemId, selectSystem, logout, refreshSystems } = useAuth();
  const [showCreate, setShowCreate] = useState(false);
  const createSystem = useCreateSystem();

  const handleCreate = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    try {
      const sys = await createSystem.mutateAsync({
        name: fd.get("name") as string,
        environment: (fd.get("environment") as string) || undefined,
        description: (fd.get("description") as string) || undefined,
      });
      await refreshSystems();
      selectSystem(sys.id);
      setShowCreate(false);
      toast.success("System created");
    } catch (err) {
      toast.error(String(err));
    }
  };

  return (
    <header className="flex items-center justify-between border-b bg-card px-6 h-14">
      <div className="flex items-center gap-3">
        {systems.length > 0 && (
          <Select
            value={String(systemId ?? "")}
            onChange={(e) => selectSystem(Number(e.target.value))}
            className="w-60"
          >
            {systems.map((s) => (
              <option key={s.id} value={s.id}>
                {s.name}{s.environment ? ` / ${s.environment}` : ""}
              </option>
            ))}
          </Select>
        )}
        <Button variant="ghost" size="icon" onClick={() => setShowCreate(true)} title="New system">
          <Plus className="h-4 w-4" />
        </Button>
      </div>

      <div className="flex items-center gap-2">
        <DiagnosticsBadge />
        <ThemeToggle />
        {user && (
          <span className="text-sm text-muted-foreground">
            {user.username}
            {user.role === "admin" && (
              <span className="ml-1 text-xs bg-primary text-primary-foreground px-1.5 py-0.5 rounded">
                admin
              </span>
            )}
          </span>
        )}
        <Button variant="ghost" size="icon" onClick={logout} title="Logout">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>

      <Dialog open={showCreate} onOpenChange={setShowCreate}>
        <DialogHeader>
          <DialogTitle>Create System</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleCreate} className="space-y-4">
          <div>
            <Label htmlFor="name">Name *</Label>
            <Input id="name" name="name" required />
          </div>
          <div>
            <Label htmlFor="environment">Environment</Label>
            <Input id="environment" name="environment" placeholder="production" />
          </div>
          <div>
            <Label htmlFor="description">Description</Label>
            <Input id="description" name="description" />
          </div>
          <Button type="submit" disabled={createSystem.isPending} className="w-full">
            {createSystem.isPending ? "Creating..." : "Create"}
          </Button>
        </form>
      </Dialog>
    </header>
  );
}

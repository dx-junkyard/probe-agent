import { useState } from "react";
import { useAuth } from "@/api/auth";
import { useUpdateSystem } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { toast } from "sonner";

export default function SettingsPage() {
  const { systems, systemId, refreshSystems } = useAuth();
  const system = systems.find(s => s.id === systemId);
  const updateSystem = useUpdateSystem();

  const [formKey, setFormKey] = useState(systemId);
  if (formKey !== systemId) {
    setFormKey(systemId);
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>

      {systems.length === 0 ? (
        // Issue #265: this used to be a heading-only blank screen when no
        // System exists yet -- there was nothing to configure and nothing
        // saying why. Same guidance pattern as connect-sdk.tsx's
        // no-System reason text.
        <p className="text-sm text-muted-foreground" data-testid="settings-no-systems-reason">
          System が作成されていません。ヘッダーの「System を作成」から
          System を作成すると、ここでその設定を編集できます。
        </p>
      ) : system ? (
        <SettingsForm
          key={system.id}
          system={system}
          onSave={async (data) => {
            await updateSystem.mutateAsync({ id: system.id, ...data });
            await refreshSystems();
            toast.success("Settings saved");
          }}
          isPending={updateSystem.isPending}
        />
      ) : (
        // systems.length > 0 but none selected yet (e.g. still loading the
        // selection) -- distinct from the zero-System case above.
        <p className="text-sm text-muted-foreground" data-testid="settings-no-system-selected-reason">
          ヘッダーから System を選択してください。
        </p>
      )}
    </div>
  );
}

function SettingsForm({ system, onSave, isPending }: {
  system: { name: string; environment: string; description: string };
  onSave: (data: { name: string; environment: string; description: string }) => Promise<void>;
  isPending: boolean;
}) {
  const [name, setName] = useState(system.name);
  const [env, setEnv] = useState(system.environment ?? "");
  const [desc, setDesc] = useState(system.description ?? "");

  const handleSave = async () => {
    try {
      await onSave({ name, environment: env, description: desc });
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">System Configuration</CardTitle>
        <CardDescription>Update the current system's settings</CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="space-y-2">
          <Label>System Name</Label>
          <Input value={name} onChange={e => setName(e.target.value)} />
        </div>
        <div className="space-y-2">
          <Label>Environment</Label>
          <Input value={env} onChange={e => setEnv(e.target.value)} placeholder="production" />
        </div>
        <div className="space-y-2">
          <Label>Description</Label>
          <Textarea value={desc} onChange={e => setDesc(e.target.value)} rows={3} />
        </div>
        <Button onClick={handleSave} disabled={isPending}>
          {isPending ? "Saving..." : "Save Settings"}
        </Button>
      </CardContent>
    </Card>
  );
}

import { useState } from "react";
import { useAuth } from "@/api/auth";
import { systemsKnownEmpty } from "@/lib/systems-state";
import { useUpdateSystem } from "@/api/hooks";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormField } from "@/components/ui/form-field";
import { toast } from "sonner";

export default function SettingsPage() {
  const auth = useAuth();
  const { systems, systemId, refreshSystems } = auth;
  const system = systems.find(s => s.id === systemId);
  const updateSystem = useUpdateSystem();

  const [formKey, setFormKey] = useState(systemId);
  if (formKey !== systemId) {
    setFormKey(systemId);
  }

  return (
    <div className="space-y-6 max-w-2xl">
      <h1 className="text-2xl font-bold tracking-tight">Settings</h1>

      {systems.length === 0 && !systemsKnownEmpty(auth) ? (
        // Issue #466 (UX-06): 取得失敗の空配列を「System 未作成」と言わない。
        <p className="text-sm text-muted-foreground" data-testid="settings-systems-unknown">
          System 一覧を取得できていません。上の「System 一覧を再取得」を試してください。
        </p>
      ) : systems.length === 0 ? (
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

  // 保存前に空の System 名を止める。エラーは入力欄に関連付けて読み上げる。
  const [attempted, setAttempted] = useState(false);
  const nameError = attempted && !name.trim() ? "System 名を入力してください。" : undefined;

  const handleSave = async () => {
    setAttempted(true);
    if (!name.trim()) return;
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
        <FormField
          label="System Name"
          required
          hint="ヘッダーの System 選択や token の対象 System 表示に使われる名前です。"
          error={nameError}
        >
          {(field) => <Input {...field} value={name} onChange={e => setName(e.target.value)} />}
        </FormField>
        <FormField
          label="Environment"
          hint="例: production / staging / development。"
        >
          {(field) => (
            <Input {...field} value={env} onChange={e => setEnv(e.target.value)} placeholder="production" />
          )}
        </FormField>
        <FormField label="Description" hint="この System の用途や担当範囲を記録します(任意)。">
          {(field) => <Textarea {...field} value={desc} onChange={e => setDesc(e.target.value)} rows={3} />}
        </FormField>
        <Button onClick={handleSave} disabled={isPending}>
          {isPending ? "Saving..." : "Save Settings"}
        </Button>
      </CardContent>
    </Card>
  );
}

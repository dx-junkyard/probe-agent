import { useState } from "react";
import { useMyTokens, useIssueToken, useRevokeMyToken } from "@/api/hooks";
import { useAuth } from "@/api/auth";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { Copy, Key, Trash2 } from "lucide-react";
import { getClientServerUrl } from "@/lib/env";
import { CodeBlock } from "@/components/code-block";

export default function ConnectSdkPage() {
  const { systemId } = useAuth();
  const { data: tokens, isLoading } = useMyTokens();
  const issueToken = useIssueToken();
  const revokeToken = useRevokeMyToken();
  const [newTokenName, setNewTokenName] = useState("");
  const [expDays, setExpDays] = useState(90);
  const [issuedToken, setIssuedToken] = useState<string | null>(null);

  const handleIssue = async () => {
    if (!newTokenName.trim() || !systemId) return;
    try {
      const t = await issueToken.mutateAsync({
        name: newTokenName.trim(),
        system_id: systemId,
        expires_in_days: expDays,
      });
      setIssuedToken(t.token ?? null);
      setNewTokenName("");
      toast.success("Token issued");
    } catch (err) { toast.error(String(err)); }
  };

  const serverUrl = getClientServerUrl();

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Connect SDK</h1>

      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">1. Install the SDK</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock lang="bash">{`pip install probe-agent`}</CodeBlock>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">2. Configure Environment</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock lang="bash">{`export PROBE_SERVER_URL="${serverUrl}"
export PROBE_API_KEY="<your-token>"`}</CodeBlock>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle className="text-base">3. Use the @probe Decorator</CardTitle>
          </CardHeader>
          <CardContent>
            <CodeBlock lang="python">{`from probe_agent import probe

@probe(component_id="my-component")
def summarize(text: str) -> str:
    # Your function logic here
    return result`}</CodeBlock>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Key className="h-4 w-4" />
            Access Tokens
          </CardTitle>
          <CardDescription>Manage API tokens for SDK authentication</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-end gap-3">
            <div className="flex-1 space-y-2">
              <Label>Token Name</Label>
              <Input value={newTokenName} onChange={e => setNewTokenName(e.target.value)} placeholder="my-service" />
            </div>
            <div className="w-28 space-y-2">
              <Label>Expires (days)</Label>
              <Input type="number" value={expDays} onChange={e => setExpDays(Number(e.target.value))} min={1} />
            </div>
            <Button onClick={handleIssue} disabled={issueToken.isPending || !newTokenName.trim()}>
              Issue Token
            </Button>
          </div>

          {issuedToken && (
            <div className="rounded-md border border-emerald-200 bg-emerald-50 dark:bg-emerald-950/20 dark:border-emerald-800 p-4">
              <p className="text-sm font-medium text-emerald-800 dark:text-emerald-200 mb-2">
                Token issued — copy it now, it won't be shown again.
              </p>
              <div className="flex items-center gap-2">
                <code className="flex-1 rounded bg-background px-3 py-2 text-xs font-mono break-all border">
                  {issuedToken}
                </code>
                <Button
                  size="icon" variant="outline"
                  onClick={() => { navigator.clipboard.writeText(issuedToken); toast.success("Copied"); }}
                >
                  <Copy className="h-4 w-4" />
                </Button>
              </div>
            </div>
          )}

          {isLoading ? (
            <div className="space-y-2">{[1,2].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
          ) : !tokens?.length ? (
            <p className="text-sm text-muted-foreground text-center py-4">No tokens issued yet</p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b text-left">
                    <th className="pb-2 font-medium text-muted-foreground">Name</th>
                    <th className="pb-2 font-medium text-muted-foreground">Kind</th>
                    <th className="pb-2 font-medium text-muted-foreground">Created</th>
                    <th className="pb-2 font-medium text-muted-foreground">Expires</th>
                    <th className="pb-2 font-medium text-muted-foreground">Status</th>
                    <th className="pb-2 font-medium text-muted-foreground text-right">Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {tokens.map(t => (
                    <tr key={t.id} className="border-b last:border-0">
                      <td className="py-2">{t.name}</td>
                      <td className="py-2"><Badge variant="outline">{t.kind}</Badge></td>
                      <td className="py-2 text-xs text-muted-foreground">{formatTimestamp(t.created_at)}</td>
                      <td className="py-2 text-xs text-muted-foreground">{formatTimestamp(t.expires_at)}</td>
                      <td className="py-2">
                        <Badge variant={t.revoked ? "destructive" : "success"}>
                          {t.revoked ? "revoked" : "active"}
                        </Badge>
                      </td>
                      <td className="py-2 text-right">
                        {!t.revoked && (
                          <Button
                            variant="ghost" size="icon" className="h-7 w-7"
                            onClick={() => revokeToken.mutateAsync(t.id).then(() => toast.success("Revoked")).catch(e => toast.error(String(e)))}
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
                          </Button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}


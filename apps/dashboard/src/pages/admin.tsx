import { useState } from "react";
import { useUsers, useCreateUser, useAllTokens } from "@/api/hooks";
import { api } from "@/api/client";
import { useAuth } from "@/api/auth";
import { Card, CardHeader, CardTitle, CardContent, CardDescription } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Select } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Dialog, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { toast } from "sonner";
import { formatTimestamp } from "@/lib/utils";
import { TOKEN_STATUS_VARIANT, isTokenUsable } from "@/lib/token-display";
import { UserPlus, Key, Trash2, Shield, RotateCcw } from "lucide-react";
import { Navigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";

export default function AdminPage() {
  const { isAdmin } = useAuth();
  const { data: users, isLoading: usersLoading } = useUsers();
  const createUser = useCreateUser();
  const { data: tokens, isLoading: tokensLoading } = useAllTokens();
  const qc = useQueryClient();

  const [showCreateUser, setShowCreateUser] = useState(false);
  const [showResetPw, setShowResetPw] = useState<number | null>(null);

  if (!isAdmin) return <Navigate to="/" replace />;

  const handleCreateUser = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    try {
      await createUser.mutateAsync({
        username: fd.get("username") as string,
        password: fd.get("password") as string,
        role: fd.get("role") as string,
      });
      setShowCreateUser(false);
      toast.success("User created");
    } catch (err) { toast.error(String(err)); }
  };

  const handleResetPassword = async (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const fd = new FormData(e.currentTarget);
    try {
      await api.post(`/users/${showResetPw}/password`, { password: fd.get("password") as string });
      setShowResetPw(null);
      toast.success("Password reset");
    } catch (err) { toast.error(String(err)); }
  };

  const handleChangeRole = async (userId: number, role: string) => {
    try {
      await api.put(`/users/${userId}/role`, { role });
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.success("Role updated");
    } catch (err) { toast.error(String(err)); }
  };

  const handleDeactivate = async (userId: number) => {
    try {
      await api.post(`/users/${userId}/deactivate`);
      qc.invalidateQueries({ queryKey: ["users"] });
      toast.success("User deactivated");
    } catch (err) { toast.error(String(err)); }
  };

  const handleRevokeToken = async (tokenId: number) => {
    try {
      await api.post(`/tokens/${tokenId}/revoke`);
      qc.invalidateQueries({ queryKey: ["allTokens"] });
      toast.success("Token revoked");
    } catch (err) { toast.error(String(err)); }
  };

  return (
    <div className="space-y-6">
      <h1 className="text-2xl font-bold tracking-tight">Administration</h1>

      <Tabs defaultValue="users">
        <TabsList>
          <TabsTrigger value="users">Users</TabsTrigger>
          <TabsTrigger value="tokens">All Tokens</TabsTrigger>
        </TabsList>

        <TabsContent value="users">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="text-base">User Management</CardTitle>
                <CardDescription>Manage user accounts and roles</CardDescription>
              </div>
              <Button size="sm" onClick={() => setShowCreateUser(true)}>
                <UserPlus className="h-4 w-4 mr-1" />
                Create User
              </Button>
            </CardHeader>
            <CardContent>
              {usersLoading ? (
                <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left">
                        <th className="pb-2 font-medium text-muted-foreground">ID</th>
                        <th className="pb-2 font-medium text-muted-foreground">Username</th>
                        <th className="pb-2 font-medium text-muted-foreground">Role</th>
                        <th className="pb-2 font-medium text-muted-foreground">Status</th>
                        <th className="pb-2 font-medium text-muted-foreground">Created</th>
                        <th className="pb-2 font-medium text-muted-foreground text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {users?.map(u => (
                        <tr key={u.id} className="border-b last:border-0">
                          <td className="py-2">{u.id}</td>
                          <td className="py-2 font-medium">{u.username}</td>
                          <td className="py-2">
                            <Select
                              className="w-24 h-7 text-xs"
                              value={u.role}
                              onChange={e => handleChangeRole(u.id, e.target.value)}
                            >
                              <option value="user">user</option>
                              <option value="admin">admin</option>
                            </Select>
                          </td>
                          <td className="py-2">
                            <Badge variant={u.is_active ? "success" : "destructive"}>
                              {u.is_active ? "active" : "inactive"}
                            </Badge>
                          </td>
                          <td className="py-2 text-xs text-muted-foreground">{formatTimestamp(u.created_at)}</td>
                          <td className="py-2 text-right">
                            <div className="flex justify-end gap-1">
                              <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => setShowResetPw(u.id)} title="Reset password">
                                <RotateCcw className="h-4 w-4" />
                              </Button>
                              {u.is_active && (
                                <Button variant="ghost" size="icon" className="h-7 w-7" onClick={() => handleDeactivate(u.id)} title="Deactivate">
                                  <Shield className="h-4 w-4 text-destructive" />
                                </Button>
                              )}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="tokens">
          <Card>
            <CardHeader>
              <CardTitle className="text-base flex items-center gap-2">
                <Key className="h-4 w-4" />
                All Tokens
              </CardTitle>
            </CardHeader>
            <CardContent>
              {tokensLoading ? (
                <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-12 w-full" />)}</div>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-sm">
                    <thead>
                      <tr className="border-b text-left">
                        <th className="pb-2 font-medium text-muted-foreground">ID</th>
                        <th className="pb-2 font-medium text-muted-foreground">Name</th>
                        <th className="pb-2 font-medium text-muted-foreground">Kind</th>
                        <th className="pb-2 font-medium text-muted-foreground">User</th>
                        <th className="pb-2 font-medium text-muted-foreground">System</th>
                        <th className="pb-2 font-medium text-muted-foreground">Status</th>
                        <th className="pb-2 font-medium text-muted-foreground text-right">Actions</th>
                      </tr>
                    </thead>
                    <tbody>
                      {tokens?.map(t => (
                        <tr key={t.id} className="border-b last:border-0">
                          <td className="py-2">{t.id}</td>
                          <td className="py-2">{t.name}</td>
                          <td className="py-2"><Badge variant="outline">{t.kind}</Badge></td>
                          <td className="py-2">{t.user_id ?? "—"}</td>
                          <td className="py-2">{t.system_id ?? "—"}</td>
                          <td className="py-2">
                            {/* Issue #368: status comes from the server (revoked +
                                expires_at + clock); an expired token must not
                                render green. The finite enum keeps its canonical
                                English spelling on this English screen. */}
                            <Badge
                              variant={TOKEN_STATUS_VARIANT[t.status] ?? "outline"}
                              data-testid={`admin-token-status-${t.id}`}
                            >
                              {t.status}
                            </Badge>
                          </td>
                          <td className="py-2 text-right">
                            {isTokenUsable(t.status) && (
                              <Button
                                variant="ghost" size="icon" className="h-7 w-7"
                                aria-label={`Revoke token ${t.name ?? `#${t.id}`}`}
                                onClick={() => handleRevokeToken(t.id)}
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
        </TabsContent>
      </Tabs>

      <Dialog open={showCreateUser} onOpenChange={setShowCreateUser}>
        <DialogHeader>
          <DialogTitle>Create User</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleCreateUser} className="space-y-4">
          <div><Label>Username</Label><Input name="username" required /></div>
          <div><Label>Password</Label><Input name="password" type="password" required /></div>
          <div>
            <Label>Role</Label>
            <Select name="role"><option value="user">user</option><option value="admin">admin</option></Select>
          </div>
          <Button type="submit" disabled={createUser.isPending} className="w-full">
            {createUser.isPending ? "Creating..." : "Create User"}
          </Button>
        </form>
      </Dialog>

      <Dialog open={showResetPw !== null} onOpenChange={() => setShowResetPw(null)}>
        <DialogHeader>
          <DialogTitle>Reset Password</DialogTitle>
        </DialogHeader>
        <form onSubmit={handleResetPassword} className="space-y-4">
          <div><Label>New Password</Label><Input name="password" type="password" required /></div>
          <Button type="submit" className="w-full">Reset Password</Button>
        </form>
      </Dialog>
    </div>
  );
}

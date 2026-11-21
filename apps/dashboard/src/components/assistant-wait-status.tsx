import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";

/** Elapsed time describes the local wait, never the server's progress. */
export function AssistantWaitStatus({ startedAt }: { startedAt: number }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);
  const seconds = Math.max(0, Math.floor((now - startedAt) / 1000));
  return <div className="space-y-1 rounded border p-2 text-xs text-muted-foreground" data-testid="assistant-wait-status">
    <p className="flex items-center gap-2"><Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />回答を待っています · 経過 {seconds} 秒</p>
    {seconds >= 30 && <p role="status">回答に時間がかかっています。応答を待っており、処理が停止したかどうかはまだ確認できません。</p>}
    <p>閉じても回答の受信を続けます。この画面で開き直すと確認できます。再読み込みすると受信状況を引き継げない場合があります。</p>
  </div>;
}

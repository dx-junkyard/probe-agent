import { useState } from "react";
import { useDiscussionJointUnderstanding } from "@/api/hooks";
import { JointUnderstandingPanel } from "@/components/system-understanding/joint-understanding-panel";
import { Button } from "@/components/ui/button";

/** Reuse the existing investigation and decision gates for discussion-owned JU.
 * The server link survives reload and supplies premise/provisional status. */
export function DiscussionInvestigationPanel({ threadId }: { threadId: number }) {
  const links = useDiscussionJointUnderstanding(threadId);
  const [opened, setOpened] = useState<number | null>(null);
  if (links.isError) return <div role="status" className="p-3 text-xs">
    共同調査を取得できませんでした。
    <Button variant="outline" size="sm" onClick={() => void links.refetch()}>再取得する</Button>
  </div>;
  if (!links.data?.links.length) return null;
  return <section className="border-t p-3 space-y-3" aria-label="この検討の共同調査">
    {links.data.links.map((link) => <div key={link.session.id} className="space-y-2">
      <p className="text-xs">{link.hypothesis.statement}</p>
      {link.reconfirmation_required && <p role="status" className="text-xs">根拠が変わっています。調査結果の再確認が必要です。</p>}
      {link.session.outcome_is_provisional && <p className="text-xs">この結果は暫定仮説です。事実として確定していません。</p>}
      <Button size="sm" variant="outline" aria-expanded={opened === link.session.id}
        onClick={() => setOpened(opened === link.session.id ? null : link.session.id)}>
        {opened === link.session.id ? "共同調査を閉じる" : "共同調査・結果を開く"}
      </Button>
      {opened === link.session.id && <JointUnderstandingPanel key={link.session.id} sessionId={null} juId={link.session.id} />}
    </div>)}
    <p className="text-xs text-muted-foreground">調査結果はこの会話の変更候補を生成するときに参照されます。保存・確定は別の操作です。</p>
  </section>;
}

// Issue #289: 自然文一括修正 -> 構造化 diff -> preview -> 選択適用.
//
// 開発者が複数の理解項目にまたがる修正を自由文でまとめて入力すると、サーバー
// (reasoning LLM, fail-closed)が構造化された change item に変換する。自由文
// はそのまま状態に反映されることは一切なく、必ずこのプレビュー画面で
// フィールド単位の diff(変更前 -> 変更後)と影響件数を確認し、ユーザーが選
// 択したものだけを適用する。resolution_state は決定的な検証結果で、
// 'resolved' 以外はチェックボックスが既定で外れておりかつ操作不能 —
// あいまい/競合/古い前提/変更不可の項目を誤って選択できてしまうことはない。
//
// 生の enum 文字列(resolution_state など)はこのファイル内の単一マッピング
// テーブルだけを通して日本語ラベルに変換する(Issue #266 UI言語規約)。

import { useState } from "react";
import { toast } from "sonner";
import { Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { RefreshStatusChip } from "@/components/system-understanding/refresh-status-chip";
import {
  useApplyChangeSet,
  useChangeSet,
  useCreateChangeSet,
  useDiscardChangeSet,
} from "@/api/hooks";
import type { ChangeResolutionState, ChangeSetItemOut } from "@/api/types";

type BadgeVariant = "default" | "secondary" | "destructive" | "outline" | "success" | "warning";

const RESOLUTION_LABELS: Record<ChangeResolutionState, string> = {
  resolved: "適用可能",
  ambiguous: "あいまい",
  conflict: "競合",
  stale: "古い前提",
  forbidden: "変更不可",
};

const RESOLUTION_VARIANTS: Record<ChangeResolutionState, BadgeVariant> = {
  resolved: "success",
  ambiguous: "warning",
  conflict: "destructive",
  stale: "warning",
  forbidden: "outline",
};

function resolutionLabel(state: string): string {
  return RESOLUTION_LABELS[state as ChangeResolutionState] ?? "要確認";
}

const TARGET_KIND_LABELS: Record<string, string> = {
  intent_item: "意図(Intent Brief)",
  understanding_claim: "現在の理解",
};

function targetKindLabel(kind: string): string {
  return TARGET_KIND_LABELS[kind] ?? kind;
}

function ChangeItemRow({
  item, checked, disabled, onToggle,
}: {
  item: ChangeSetItemOut;
  checked: boolean;
  disabled: boolean;
  onToggle: (checked: boolean) => void;
}) {
  return (
    <div
      className="rounded-md border p-3 space-y-2"
      data-testid={`change-set-item-${item.id}`}
      data-resolution-state={item.resolution_state}
    >
      <div className="flex items-start gap-2">
        <input
          type="checkbox"
          className="mt-1"
          checked={checked}
          disabled={disabled}
          onChange={e => onToggle(e.target.checked)}
          data-testid={`change-set-item-checkbox-${item.id}`}
          aria-label="この変更を適用する"
        />
        <div className="min-w-0 flex-1 space-y-1 text-xs">
          <div className="flex flex-wrap items-center gap-1">
            <Badge variant="outline">{targetKindLabel(item.target_kind)}</Badge>
            <Badge variant={RESOLUTION_VARIANTS[item.resolution_state] ?? "outline"} data-testid={`change-set-item-state-${item.id}`}>
              {resolutionLabel(item.resolution_state)}
            </Badge>
            {item.applied && (
              <Badge variant="success" data-testid={`change-set-item-applied-${item.id}`}>
                適用済み
              </Badge>
            )}
            {item.affected_items.length > 0 && (
              <span className="text-muted-foreground" data-testid={`change-set-item-affected-${item.id}`}>
                関連するレビュー項目 {item.affected_items.length} 件
              </span>
            )}
          </div>
          <p className="break-words">
            <span className="font-semibold text-muted-foreground">変更前:</span>{" "}
            {item.before_value ?? "(なし)"}
          </p>
          <p className="break-words">
            <span className="font-semibold">変更後:</span> {item.after_value}
          </p>
          <p className="break-words text-muted-foreground">
            <span className="font-semibold">理由:</span> {item.reason}
          </p>
        </div>
      </div>
    </div>
  );
}

export function ChangeSetPanel({ sessionId }: { sessionId: number }) {
  const [text, setText] = useState("");
  const [changeSetId, setChangeSetId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Record<number, boolean>>({});

  const create = useCreateChangeSet(sessionId);
  const { data: detail } = useChangeSet(changeSetId);
  const apply = useApplyChangeSet(sessionId);
  const discard = useDiscardChangeSet();

  // resolved (and not yet applied) items default to checked; every other
  // state defaults to unchecked and stays disabled (see ChangeItemRow), so
  // it can never actually be selected. This is a pure derivation, not
  // effect-synced state, so a user's own toggle (once recorded in
  // `selected`) always wins over the default.
  const isSelected = (item: ChangeSetItemOut): boolean =>
    selected[item.id] ?? (item.resolution_state === "resolved" && !item.applied);

  const handleSubmit = () => {
    if (!text.trim()) {
      toast.error("修正内容を入力してください");
      return;
    }
    create.mutate(text, {
      onSuccess: result => {
        setChangeSetId(result.change_set.id);
        setSelected({});
        toast.success(`${result.items.length} 件の変更案を作成しました`);
      },
      onError: e => toast.error(String(e)),
    });
  };

  const handleApply = () => {
    if (!detail) return;
    const itemIds = detail.items
      .filter(item => isSelected(item) && item.resolution_state === "resolved" && !item.applied)
      .map(item => item.id);
    if (itemIds.length === 0) {
      toast.error("適用する変更を選択してください");
      return;
    }
    apply.mutate({ changeSetId: detail.change_set.id, itemIds }, {
      onSuccess: result => {
        if (result.skipped.length === 0) {
          toast.success(`${result.applied_item_ids.length} 件の変更を適用しました`);
        } else {
          toast.success(
            `${result.applied_item_ids.length} 件を適用し、${result.skipped.length} 件はスキップしました`,
          );
        }
      },
      onError: e => toast.error(String(e)),
    });
  };

  const handleDiscard = () => {
    if (!detail) return;
    discard.mutate(detail.change_set.id, {
      onSuccess: () => {
        toast.info("変更案を破棄しました");
        setChangeSetId(null);
        setText("");
        setSelected({});
      },
      onError: e => toast.error(String(e)),
    });
  };

  const isTerminal = detail?.change_set.status === "discarded" || detail?.change_set.status === "applied";
  const selectedCount = detail
    ? detail.items.filter(item => isSelected(item) && item.resolution_state === "resolved" && !item.applied).length
    : 0;

  return (
    <Card data-testid="change-set-panel">
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <div>
            <CardTitle className="text-sm flex items-center gap-2">
              まとめて修正
              <RefreshStatusChip sessionId={sessionId} />
            </CardTitle>
            <CardDescription>
              複数の項目にまたがる修正を自由文でまとめて入力すると、変更案として構造化してプレビューします。選択した変更だけが適用されます。
            </CardDescription>
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-3">
        <Textarea
          value={text}
          onChange={e => setText(e.target.value)}
          placeholder="例: 実はゴールはAではなくBです。あとトレース管理の説明も実態に合わせて直してください"
          rows={3}
          data-testid="change-set-textarea"
        />
        <Button
          size="sm"
          onClick={handleSubmit}
          disabled={create.isPending}
          data-testid="change-set-submit"
        >
          <Sparkles className="h-4 w-4 mr-1" />
          {create.isPending ? "解析中..." : "変更案を作成"}
        </Button>

        {detail && (
          <div className="space-y-2 pt-2 border-t" data-testid="change-set-preview">
            {detail.items.length === 0 ? (
              <p className="text-xs text-muted-foreground" data-testid="change-set-empty">
                この内容から提案できる変更はありませんでした。
              </p>
            ) : (
              <>
                <p className="text-xs text-muted-foreground" data-testid="change-set-rebuild-note">
                  {detail.rebuild_note}
                </p>
                {detail.items.map(item => (
                  <ChangeItemRow
                    key={item.id}
                    item={item}
                    checked={isSelected(item)}
                    disabled={item.resolution_state !== "resolved" || item.applied}
                    onToggle={value => setSelected(prev => ({ ...prev, [item.id]: value }))}
                  />
                ))}
                {!isTerminal && (
                  <div className="flex flex-wrap gap-2 pt-2">
                    <Button
                      size="sm"
                      onClick={handleApply}
                      disabled={apply.isPending || selectedCount === 0}
                      data-testid="change-set-apply-button"
                    >
                      選択した変更を適用({selectedCount})
                    </Button>
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={handleDiscard}
                      disabled={discard.isPending}
                      data-testid="change-set-discard-button"
                    >
                      破棄
                    </Button>
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

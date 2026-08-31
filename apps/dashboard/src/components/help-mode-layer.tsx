// 機能解説モードのオーバーレイ (Issue #440, Epic #436).
//
// `HelpModeProvider` の `active` が true のときだけイベントリスナーを張り、
// false に戻ると `useEffect` の cleanup で全て外す -- 「解除後は通常操作へ
// 完全に戻る」を、リスナーを条件付きで張ることそのもので保証する
// (`docs/assistant-discussion.md` §3)。
//
// - hover (`pointerover` / `mouseover`) は ~150ms デバウンスする。
// - `focusin` はキーボード操作向けに即座に選択する。
// - `click` は capture フェーズで奪う: 解説モード中は要素の通常動作
//   (リンク遷移・ボタンの実行など) を `preventDefault` + `stopPropagation`
//   で止め、その要素を選択するだけにする。これがタップでしか選べない端末の
//   対応でもある。
// - `Escape` はモードを終了する。
// - target が変わるたびに `useUiHelpEntry` のクエリキーが変わるので、古い
//   target への遅延応答は React Query が新しい key の表示へ混ぜない
//   (stale response の破棄)。

import { useEffect, useMemo, useRef } from "react";
import { HelpCircle, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { useHelpMode } from "@/lib/help-mode";
import { useUiHelpEntry } from "@/api/hooks";
import type { HelpId } from "@/lib/ui-help";

const HOVER_DEBOUNCE_MS = 150;

function resolveHelpId(target: EventTarget | null): HelpId | null {
  if (!(target instanceof Element)) return null;
  const el = target.closest<HTMLElement>("[data-help-id]");
  const id = el?.getAttribute("data-help-id");
  return id ? (id as HelpId) : null;
}

function usePrefersReducedMotion(): boolean {
  return useMemo(() => {
    try {
      return typeof window !== "undefined" && !!window.matchMedia
        ? window.matchMedia("(prefers-reduced-motion: reduce)").matches
        : false;
    } catch {
      return false;
    }
  }, []);
}

const SCOPE_LABEL: Record<string, string> = {
  screen: "画面全体",
  section: "セクション",
  element: "要素",
};

export function HelpModeLayer() {
  const { active, target, setTarget, exit } = useHelpMode();
  const debounceRef = useRef<number | null>(null);
  const reducedMotion = usePrefersReducedMotion();

  // イベントリスナーは `active` の間だけ存在する。これ自体が「解除後は
  // 通常操作へ完全に戻る」の実装で、フラグを見て早期リターンする形にはしない
  // -- 早期リターンだけだとリスナー自体は残り続け、無効化のたびに handler の
  // 中で分岐が増える。
  useEffect(() => {
    if (!active) return undefined;

    function clearDebounce() {
      if (debounceRef.current !== null) {
        window.clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
    }

    function onHover(event: Event) {
      const id = resolveHelpId(event.target);
      if (id === null) return;
      clearDebounce();
      debounceRef.current = window.setTimeout(() => {
        setTarget(id);
      }, HOVER_DEBOUNCE_MS);
    }

    function onFocusIn(event: FocusEvent) {
      const id = resolveHelpId(event.target);
      if (id === null) return;
      clearDebounce();
      setTarget(id);
    }

    function onClickCapture(event: MouseEvent) {
      const id = resolveHelpId(event.target);
      if (id === null) return;
      // 解説モード中はこの要素の通常動作 (遷移・実行) を止め、選択だけを行う。
      event.preventDefault();
      event.stopPropagation();
      clearDebounce();
      setTarget(id);
    }

    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        exit();
      }
    }

    document.addEventListener("pointerover", onHover);
    document.addEventListener("mouseover", onHover);
    document.addEventListener("focusin", onFocusIn);
    document.addEventListener("click", onClickCapture, true);
    document.addEventListener("keydown", onKeyDown);

    return () => {
      clearDebounce();
      document.removeEventListener("pointerover", onHover);
      document.removeEventListener("mouseover", onHover);
      document.removeEventListener("focusin", onFocusIn);
      document.removeEventListener("click", onClickCapture, true);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [active, setTarget, exit]);

  const { data: entry, isLoading, isError } = useUiHelpEntry(active ? target : null);

  if (!active) return null;

  return (
    <div
      className="fixed inset-x-0 bottom-20 z-50 flex justify-center px-4 pointer-events-none md:bottom-6"
      data-testid="help-mode-layer"
    >
      <div
        role="region"
        aria-label="機能解説"
        aria-live="polite"
        className={cn(
          "pointer-events-auto w-full max-w-xl rounded-lg border bg-card p-4 shadow-lg",
          !reducedMotion && "transition-opacity duration-150",
        )}
        data-testid="help-mode-panel"
      >
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <HelpCircle className="h-4 w-4 text-primary" aria-hidden="true" />
            解説モード
          </div>
          <button
            type="button"
            onClick={exit}
            className="inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs hover:bg-accent"
            data-testid="help-mode-exit"
          >
            <X className="h-3 w-3" aria-hidden="true" />
            解説モードを終了
          </button>
        </div>

        <div className="mt-3">
          {target === null ? (
            <p className="text-sm text-muted-foreground" data-testid="help-mode-empty">
              画面上の要素にカーソルを合わせるか、タップして選択してください。
            </p>
          ) : isLoading ? (
            <p className="text-sm text-muted-foreground" data-testid="help-mode-loading">
              読み込み中です。
            </p>
          ) : isError || !entry ? (
            <p className="text-sm text-destructive" data-testid="help-mode-error">
              この要素の解説を取得できませんでした。
            </p>
          ) : (
            <div className="space-y-2" data-testid="help-mode-entry" data-help-scope={entry.scope}>
              <div className="flex flex-wrap items-center gap-2">
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                  {SCOPE_LABEL[entry.scope] ?? entry.scope}
                </span>
                <h2 className="text-sm font-semibold">{entry.title}</h2>
              </div>
              <p className="text-sm">{entry.summary}</p>
              <p className="text-sm text-muted-foreground">{entry.usage}</p>

              {entry.doc_refs.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground">出典</p>
                  <ul className="mt-1 space-y-0.5" data-testid="help-mode-doc-refs">
                    {entry.doc_refs.map((doc, i) => (
                      <li key={i} className="text-xs text-muted-foreground">
                        {doc.title}
                        <code className="ml-1 rounded bg-muted px-1 py-0.5 font-mono text-[11px]">
                          {doc.doc_path}
                          {doc.anchor ? `#${doc.anchor}` : ""}
                        </code>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {entry.related_actions.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground">関連する操作</p>
                  <ul className="mt-1 space-y-0.5" data-testid="help-mode-related-actions">
                    {entry.related_actions.map((action, i) => (
                      <li key={i} className="text-xs">
                        {action.label}
                        <span className="ml-1 text-muted-foreground">({action.kind})</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

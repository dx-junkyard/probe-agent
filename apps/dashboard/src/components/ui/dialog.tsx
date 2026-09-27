import { createContext, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { cn } from "@/lib/utils";
import { lockBodyScroll, useModalSurface } from "@/lib/modal-surface";
import { X } from "lucide-react";

// Issue #466 (UX-02): 共通 Dialog はアシスタントパネル・モバイル Drawer と
// 同じ `useModalSurface` に載せる。承認・失効・System 作成といった重要操作が
// この部品を通るので、ここが dialog ロール / タイトル参照 / 初期フォーカス /
// フォーカストラップ / Escape / フォーカス復帰を持たないと、キーボードと
// 読み上げの利用者はそれらの操作に到達できない。
//
// `DialogTitle` と `DialogDescription` は context 経由で自分の id を登録し、
// パネルの `aria-labelledby` / `aria-describedby` になる。呼び出し側が id を
// 配線し忘れても、タイトルを置けば読み上げでダイアログ名が伝わる。

const DialogIdsContext = createContext<{
  titleId: string;
  descriptionId: string;
  setHasDescription: (present: boolean) => void;
} | null>(null);

function Dialog({
  open,
  onOpenChange,
  children,
  closeOnOverlayClick = true,
  describedBy,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  /**
   * 背景クリックで閉じるか。長い入力を持つダイアログでは、背景を1回
   * クリックしただけで閉じるのは入力の誤破棄につながる (UX-03)。閉じても
   * 下書きを保持できない呼び出し側は `false` にする。
   */
  closeOnOverlayClick?: boolean;
  /** `DialogDescription` 以外の要素を説明として参照したいときの id。 */
  describedBy?: string;
}) {
  if (!open) return null;
  return (
    <DialogSurface
      onOpenChange={onOpenChange}
      closeOnOverlayClick={closeOnOverlayClick}
      describedBy={describedBy}
    >
      {children}
    </DialogSurface>
  );
}

// 開いている間だけマウントする。`useModalSurface` の effect が「開いた瞬間」と
// 「閉じた瞬間 (アンマウント)」に対応し、フォーカス復帰とスタックの整合が
// 取れる。
function DialogSurface({
  onOpenChange,
  children,
  closeOnOverlayClick,
  describedBy,
}: {
  onOpenChange: (open: boolean) => void;
  children: ReactNode;
  closeOnOverlayClick: boolean;
  describedBy?: string;
}) {
  const overlayRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const titleId = `${baseId}-title`;
  const descriptionId = `${baseId}-description`;

  useEffect(() => lockBodyScroll(), []);
  useModalSurface({ open: true, onClose: () => onOpenChange(false), panelRef });

  // 説明は任意なので、DialogDescription が実際に描画されたときだけ参照する
  // (存在しない id を aria-describedby に置かない)。タイトルは全ての呼び出し
  // 側が置く前提で常に参照する。
  const [hasDescription, setHasDescription] = useState(false);
  const describedByIds = [describedBy, hasDescription ? descriptionId : undefined]
    .filter(Boolean)
    .join(" ") || undefined;

  return (
    <div
      ref={overlayRef}
      data-testid="dialog-overlay"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
      onClick={(e) => {
        if (closeOnOverlayClick && e.target === overlayRef.current) onOpenChange(false);
      }}
    >
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={describedByIds}
        tabIndex={-1}
        className="relative w-full max-w-lg max-h-[85vh] overflow-auto rounded-xl border bg-background p-6 shadow-lg animate-in fade-in-0 zoom-in-95 focus-visible:outline-none"
      >
        <DialogIdsContext.Provider value={{ titleId, descriptionId, setHasDescription }}>
          {children}
        </DialogIdsContext.Provider>
        {/* 閉じるボタンは DOM の末尾に置き、初期フォーカスの対象から外す。
            先頭にあると、開いた直後の読み上げが「閉じる」から始まり、
            Enter 1 回で入力を捨てる位置にフォーカスが来る。見た目の位置は
            absolute で右上のまま。 */}
        <button
          type="button"
          aria-label="ダイアログを閉じる"
          data-modal-initial-skip=""
          className="absolute right-4 top-4 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring cursor-pointer"
          onClick={() => onOpenChange(false)}
        >
          <X className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function DialogHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("flex flex-col space-y-1.5 text-center sm:text-left mb-4 pr-6", className)} {...props} />;
}

function DialogTitle({ className, id, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  const ids = useContext(DialogIdsContext);
  return (
    <h2
      id={id ?? ids?.titleId}
      className={cn("text-lg font-semibold leading-none tracking-tight", className)}
      {...props}
    />
  );
}

function DialogDescription({ className, id, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  const ids = useContext(DialogIdsContext);
  const register = ids?.setHasDescription;
  useLayoutEffect(() => {
    if (!register || id) return;
    register(true);
    return () => register(false);
  }, [register, id]);
  return (
    <p
      id={id ?? ids?.descriptionId}
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}

export { Dialog, DialogHeader, DialogTitle, DialogDescription };

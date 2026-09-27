import { Button } from "@/components/ui/button";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { copyText } from "@/lib/clipboard";

export function CodeBlock({ children, lang }: { children: string; lang: string }) {
  return (
    <div className="relative">
      <pre className="rounded-md bg-muted p-4 overflow-x-auto text-sm font-mono">
        <code>{children}</code>
      </pre>
      <Button
        variant="ghost" size="icon" className="absolute top-2 right-2 h-7 w-7"
        onClick={async () => {
          // Issue #466 (UX-08): 書き込みが完了してから成功を伝える。
          if (await copyText(children)) toast.success("コピーしました");
          else toast.error("コピーできませんでした。コードを選択して手動でコピーしてください。");
        }}
        aria-label={`${lang}コードをコピー`}
        title={`${lang}コードをコピー`}
      >
        <Copy className="h-3 w-3" />
      </Button>
    </div>
  );
}

import { Button } from "@/components/ui/button";
import { Copy } from "lucide-react";
import { toast } from "sonner";

export function CodeBlock({ children, lang }: { children: string; lang: string }) {
  return (
    <div className="relative">
      <pre className="rounded-md bg-muted p-4 overflow-x-auto text-sm font-mono">
        <code>{children}</code>
      </pre>
      <Button
        variant="ghost" size="icon" className="absolute top-2 right-2 h-7 w-7"
        onClick={() => { navigator.clipboard.writeText(children); toast.success("Copied"); }}
        title={`Copy ${lang} code`}
      >
        <Copy className="h-3 w-3" />
      </Button>
    </div>
  );
}

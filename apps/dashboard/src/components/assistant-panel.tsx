import { useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useAssistantAsk, useAssistantScreenContext } from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DiagnosticSeverityIcon } from "@/components/diagnostics-badge";
import {
  ArrowRight, Bot, ExternalLink, Loader2, Send, Settings2, Wrench, X,
} from "lucide-react";
import type { AssistantAskOut, AssistantCitation } from "@/api/types";

// Per-screen assistant (Issue #102): floating agent button + right-side panel.
// Answers come from POST /assistant/ask and are grounded in screen context,
// static settings metadata, and deterministic diagnostics; fallback answers
// are visibly marked. No client-side heuristic decoration.

function screenIdFromPath(pathname: string): string {
  if (pathname === "/") return "overview";
  return pathname.split("/")[1] ?? "overview";
}

interface ChatMessage {
  role: "user" | "assistant" | "error";
  text: string;
  result?: AssistantAskOut;
}

function CitationChip({ citation }: { citation: AssistantCitation }) {
  if (citation.type === "setting") {
    return (
      <code
        className="text-[11px] bg-muted px-1.5 py-0.5 rounded font-mono"
        title={citation.title}
        data-testid="assistant-citation"
      >
        {citation.id}
      </code>
    );
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-[11px] bg-muted px-1.5 py-0.5 rounded"
      title={citation.detail || citation.id}
      data-testid="assistant-citation"
    >
      {citation.type === "diagnostic_check" ? (
        <Wrench className="h-3 w-3" />
      ) : (
        <ArrowRight className="h-3 w-3" />
      )}
      {citation.title || citation.id}
    </span>
  );
}

function AnswerMessage({ result }: { result: AssistantAskOut }) {
  const navigate = useNavigate();
  return (
    <div className="rounded-lg border bg-card p-3 space-y-2" data-testid="assistant-answer">
      <p className="text-sm whitespace-pre-wrap">{result.answer}</p>
      <div className="flex flex-wrap items-center gap-1.5">
        {result.used_fallback ? (
          <Badge
            variant="secondary"
            className="text-[10px]"
            title={result.fallback_reason ?? undefined}
            data-testid="assistant-fallback-badge"
          >
            rule-based fallback (no LLM)
          </Badge>
        ) : (
          <Badge variant="outline" className="text-[10px]" title={`${result.provider}/${result.model}`}>
            reasoning_llm · {result.model}
          </Badge>
        )}
      </div>
      {result.citations.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] font-medium text-muted-foreground">Based on</p>
          <div className="flex flex-wrap gap-1">
            {result.citations.map((c, i) => (
              <CitationChip key={`${c.type}-${c.id}-${i}`} citation={c} />
            ))}
          </div>
        </div>
      )}
      {result.suggested_actions.length > 0 && (
        <div className="space-y-1">
          <p className="text-[11px] font-medium text-muted-foreground">Next actions</p>
          <div className="flex flex-col gap-1">
            {result.suggested_actions.map((a, i) => (
              <div key={`${a.kind}-${a.target}-${i}`} className="flex items-start gap-1.5">
                {a.kind === "configure" ? (
                  <span
                    className="inline-flex items-center gap-1 text-xs"
                    data-testid="assistant-action"
                    title={a.detail}
                  >
                    <Settings2 className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    {a.label}:{" "}
                    <code className="bg-muted px-1 py-0.5 rounded font-mono text-[11px]">{a.target}</code>
                  </span>
                ) : a.target.startsWith("/") ? (
                  // navigate/operate targets are in-app routes (validated
                  // server-side against the known route set).
                  <button
                    className="inline-flex items-center gap-1 text-xs text-primary hover:underline cursor-pointer"
                    onClick={() => navigate(a.target)}
                    title={a.detail || a.target}
                    data-testid="assistant-action"
                  >
                    <ExternalLink className="h-3.5 w-3.5 shrink-0" />
                    {a.label}
                  </button>
                ) : (
                  <span
                    className="inline-flex items-center gap-1 text-xs"
                    data-testid="assistant-action"
                    title={a.detail}
                  >
                    <Wrench className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                    {a.label}
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function AssistantPanel() {
  const location = useLocation();
  const screenId = screenIdFromPath(location.pathname);
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  // Threads are kept per screen so switching pages keeps each conversation.
  const [threads, setThreads] = useState<Record<string, ChatMessage[]>>({});
  const messages = threads[screenId] ?? [];
  const listRef = useRef<HTMLDivElement>(null);

  const { data: ctx } = useAssistantScreenContext(screenId, open);
  const ask = useAssistantAsk();

  const failingChecks = useMemo(
    () => (ctx?.screen_checks ?? []).filter((c) => c.severity !== "ok"),
    [ctx],
  );

  const appendMessages = (id: string, msgs: ChatMessage[]) => {
    setThreads((prev) => ({ ...prev, [id]: [...(prev[id] ?? []), ...msgs] }));
    requestAnimationFrame(() => {
      listRef.current?.scrollTo({ top: listRef.current.scrollHeight });
    });
  };

  const submit = async (q: string) => {
    const trimmed = q.trim();
    if (!trimmed || ask.isPending) return;
    setQuestion("");
    appendMessages(screenId, [{ role: "user", text: trimmed }]);
    try {
      const result = await ask.mutateAsync({
        screen_id: screenId,
        question: trimmed,
        visible_check_ids: failingChecks.map((c) => c.check_id),
      });
      appendMessages(screenId, [{ role: "assistant", text: result.answer, result }]);
    } catch (err) {
      appendMessages(screenId, [{ role: "error", text: String(err) }]);
    }
  };

  if (!open) {
    return (
      <Button
        size="icon"
        onClick={() => setOpen(true)}
        title="Ask the assistant about this screen"
        data-testid="assistant-button"
        className="fixed bottom-6 right-6 z-40 h-11 w-11 rounded-full shadow-lg"
      >
        <Bot className="h-5 w-5" />
      </Button>
    );
  }

  return (
    <div
      className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-xl"
      data-testid="assistant-panel"
    >
      <div className="flex items-start justify-between gap-2 border-b p-4">
        <div className="flex items-start gap-2 min-w-0">
          <Bot className="h-5 w-5 mt-0.5 shrink-0" />
          <div className="min-w-0">
            <p className="text-sm font-semibold">{ctx?.title ?? screenId}</p>
            {ctx && (
              <p className="text-xs text-muted-foreground">{ctx.purpose}</p>
            )}
          </div>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={() => setOpen(false)}
          title="Close assistant"
          data-testid="assistant-close"
        >
          <X className="h-4 w-4" />
        </Button>
      </div>

      <div ref={listRef} className="flex-1 overflow-y-auto p-4 space-y-3">
        {ctx && (
          <div className="rounded-lg border bg-card p-3 space-y-2" data-testid="assistant-state-summary">
            <div className="flex items-center gap-1.5">
              <DiagnosticSeverityIcon severity={ctx.state_severity} />
              <p className="text-xs font-medium">
                {failingChecks.length === 0
                  ? "All checks related to this screen are passing."
                  : `${failingChecks.length} check(s) need attention on this screen.`}
              </p>
            </div>
            {failingChecks.map((c) => (
              <div key={c.check_id} className="flex items-start gap-1.5 pl-1">
                <DiagnosticSeverityIcon severity={c.severity} className="h-3.5 w-3.5 mt-0.5 shrink-0" />
                <p className="text-[11px] text-muted-foreground">
                  <span className="font-medium text-foreground">{c.title}</span> — {c.detail}
                </p>
              </div>
            ))}
          </div>
        )}

        {ctx && ctx.suggested_questions.length > 0 && messages.length === 0 && (
          <div className="space-y-1">
            <p className="text-[11px] font-medium text-muted-foreground">Suggested questions</p>
            <div className="flex flex-col items-start gap-1">
              {ctx.suggested_questions.slice(0, 6).map((q) => (
                <button
                  key={q.question}
                  className="text-left text-xs text-primary hover:underline cursor-pointer"
                  onClick={() => submit(q.question)}
                  data-testid="assistant-suggested-question"
                >
                  {q.question}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="ml-8 rounded-lg bg-secondary p-3">
              <p className="text-sm whitespace-pre-wrap">{m.text}</p>
            </div>
          ) : m.role === "assistant" && m.result ? (
            <AnswerMessage key={i} result={m.result} />
          ) : (
            <p key={i} className="text-xs text-destructive" data-testid="assistant-error">
              {m.text}
            </p>
          ),
        )}
        {ask.isPending && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" /> Thinking…
          </div>
        )}
      </div>

      <form
        className="flex items-center gap-2 border-t p-3"
        onSubmit={(e) => {
          e.preventDefault();
          void submit(question);
        }}
      >
        <Input
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="Ask about this screen or a setting…"
          data-testid="assistant-question-input"
        />
        <Button
          type="submit"
          size="icon"
          disabled={ask.isPending || !question.trim()}
          title="Send"
          data-testid="assistant-send"
        >
          <Send className="h-4 w-4" />
        </Button>
      </form>
    </div>
  );
}

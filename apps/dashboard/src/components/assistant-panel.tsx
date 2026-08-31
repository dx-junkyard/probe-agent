import { useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import {
  useAssistantAsk, useAssistantDiscussionThread, useAssistantScreenContext,
} from "@/api/hooks";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { DiagnosticSeverityIcon } from "@/components/diagnostics-badge";
import { AssistantVoice } from "@/components/assistant-voice";
import {
  ArrowRight, Bot, ExternalLink, Loader2, Mic, Send, Settings2, Wrench, X,
} from "lucide-react";
import type {
  AssistantAskOut, AssistantCitation, AssistantDiscussionTargetIn,
  AssistantDiscussionThread, AssistantDiscussionThreadDetailOut, DiscussionTargetState,
  SystemStateItem,
} from "@/api/types";
import { systemStateTarget } from "@/components/system-state";
import { useModalSurface } from "@/lib/modal-surface";
import { useHelpMode } from "@/lib/help-mode";
import { voicePrerequisite, VOICE_ERROR_MESSAGES, type VoiceErrorReason } from "@/lib/voice-adapter";
import {
  OPEN_ASSISTANT_EVENT,
  type OpenAssistantDetail,
} from "@/lib/assistant-control";

// Per-screen assistant (Issue #102): floating agent button + right-side panel.
// Answers come from POST /assistant/ask and are grounded in screen context,
// static settings metadata, and deterministic diagnostics; fallback answers
// are visibly marked. No client-side heuristic decoration.
//
// Issue #438 (Epic #436): on the 4 discussion-enabled screens, the
// conversation is keyed by the canonical target identity
// (screen_id|scope|target_kind|target_ref), NOT by screen_id alone -- a
// Requirement A discussion and a Requirement B discussion never share
// history. Any other screen, or a failure of the thread endpoints, keeps the
// pre-#438 client-only per-screen conversation (the safe migration path).

function screenIdFromPath(pathname: string): string {
  if (pathname === "/") return "overview";
  return pathname.split("/")[1] ?? "overview";
}

const DISCUSSION_SCREEN_IDS = ["overview", "interview", "ux-design-studio", "journey-blueprint"] as const;

function isDiscussionScreen(screenId: string): boolean {
  return (DISCUSSION_SCREEN_IDS as readonly string[]).includes(screenId);
}

// Issue #441 (Epic #436), Phase 1: turn-based voice mode.
//
// The route-param key the currently hovered/selected help-mode element (if
// any) is threaded through as, for the SAME `/assistant/ask` call every text
// question already uses -- §4 deliberately does not add a new
// `DiscussionTargetKind` for this ("the finite set is closed"); route_params
// is the existing, already-arbitrary channel the server reads screen data
// providers from.
const VOICE_ELEMENT_HELP_ID_PARAM = "voice_element_help_id";

const VOICE_PREREQUISITE_MESSAGE: Record<string, string> = {
  insecure_context: "音声対話にはマイクを利用できる安全な接続 (HTTPS) が必要です。",
  unsupported: "このブラウザは音声対話 (マイクの利用) に対応していません。",
};

/**
 * 1 回の発話 (turn) が「何についてのものか」のスナップショット。turn 開始の
 * 瞬間に `captureVoiceTurnTarget` が作り、`AssistantVoice` がその turn の
 * 間じゅう ref に保持する -- 発話の途中で画面や選択対象が変わっても、この
 * turn の `/assistant/ask` 呼び出しは書き換わらない (§4)。
 */
interface VoiceTurnTarget {
  screenId: string;
  useLegacy: boolean;
  thread: AssistantDiscussionThread | null;
  routeParams: Record<string, string>;
  /** hover/選択中の help-mode target。null なら画面全体スコープ。 */
  helpId: string | null;
}

interface DiscussionCandidate {
  target: AssistantDiscussionTargetIn;
  label: string;
}

/** Derive the most specific selectable non-screen target from the route, if
 * any -- purely from `screen_id` + query params, so it needs no page-level
 * wiring beyond what each page already writes to the URL. Returns `null`
 * when nothing more specific than "the whole screen" is selected. */
function deriveDiscussionCandidate(screenId: string, search: string): DiscussionCandidate | null {
  const params = new URLSearchParams(search);
  if (screenId === "interview") {
    const session = params.get("session");
    if (session) {
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "interview_session", target_ref: session },
        label: `セッション #${session}`,
      };
    }
    return null;
  }
  if (screenId === "ux-design-studio") {
    const tab = params.get("tab") || "journeys";
    const journey = params.get("journey");
    const step = params.get("step");
    const requirement = params.get("requirement");
    const design = params.get("design");
    if (journey && step) {
      return {
        target: {
          scope: "element", screen_id: screenId, target_kind: "ux_journey_step",
          target_ref: `${journey}#${step}`,
        },
        label: `ステップ「${step}」`,
      };
    }
    if (tab === "requirements" && requirement) {
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "ux_requirement", target_ref: requirement },
        label: `Requirement「${requirement}」`,
      };
    }
    if (tab === "solutions" && design) {
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "solution_design", target_ref: design },
        label: `Solution Design「${design}」`,
      };
    }
    if (tab === "journeys" && journey) {
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "ux_journey", target_ref: journey },
        label: `Journey「${journey}」`,
      };
    }
    return null;
  }
  if (screenId === "journey-blueprint") {
    const journey = params.get("journey");
    const step = params.get("step");
    const lane = params.get("lane");
    if (journey && step && lane) {
      return {
        target: {
          scope: "element", screen_id: screenId, target_kind: "blueprint_lane_cell",
          target_ref: `${journey}#${step}#${lane}`,
        },
        label: `${lane}(「${step}」)`,
      };
    }
    if (journey && step) {
      return {
        target: {
          scope: "element", screen_id: screenId, target_kind: "ux_journey_step",
          target_ref: `${journey}#${step}`,
        },
        label: `ステップ「${step}」`,
      };
    }
    if (journey) {
      return {
        target: { scope: "entity", screen_id: screenId, target_kind: "ux_journey", target_ref: journey },
        label: `Journey「${journey}」`,
      };
    }
    return null;
  }
  return null;
}

function targetKeyOf(target: AssistantDiscussionTargetIn | null): string | null {
  if (!target) return null;
  return `${target.screen_id}|${target.scope}|${target.target_kind}|${target.target_ref}`;
}

function staleBannerText(state: DiscussionTargetState): string {
  if (state === "unresolvable") {
    return "この対象は見つかりませんでした。これより前のやり取りは履歴として残りますが、最新の回答の前提には使われません。";
  }
  return "この対象の内容は前回の会話から変わりました。これより前のやり取りは履歴として残りますが、最新の回答の前提には使われません。";
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

interface AssistantPanelProps {
  focusedStateItem?: SystemStateItem | null;
  snapshotNotice?: string | null;
  onSnapshotNoticeClick?: () => void;
}

export function AssistantPanel({ focusedStateItem, snapshotNotice, onSnapshotNoticeClick }: AssistantPanelProps = {}) {
  const location = useLocation();
  const navigate = useNavigate();
  const screenId = screenIdFromPath(location.pathname);
  const [open, setOpen] = useState(false);
  const [question, setQuestion] = useState("");
  // Threads are kept per screen so switching pages keeps each conversation.
  // This stays the ENTIRE mechanism for non-discussion screens, and is the
  // fallback for discussion screens whose thread endpoints fail (#438).
  const [threads, setThreads] = useState<Record<string, ChatMessage[]>>({});
  const listRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  useModalSurface({ open, onClose: () => setOpen(false), panelRef });

  // --- Issue #441: turn-based voice mode ------------------------------------
  // `voicePrerequisite()` reads only static browser/context capabilities
  // (secure context + Web Speech API presence), so it is safe to compute once
  // per mount rather than re-checking on every render.
  const voicePrereq = useMemo(() => voicePrerequisite(), []);
  const voiceReady = voicePrereq === "ready";
  const [voiceActive, setVoiceActive] = useState(false);
  const [voiceFallbackNotice, setVoiceFallbackNotice] = useState<string | null>(null);
  // The element (if any) currently hovered/selected in help mode (#440) is
  // reused verbatim as the voice scope signal: an element target means "this
  // question is about that element", no target means "the whole screen"
  // (docs/assistant-discussion.md §3/§4). `useHelpMode()` falls back to an
  // inert no-op context when no `HelpModeProvider` is mounted, so this is
  // safe on any screen.
  const helpMode = useHelpMode();
  const voiceScopeLabel = helpMode.target ? `要素「${helpMode.target}」` : "画面全体";
  // A voice turn sends `input_mode: "voice"`, which the server records on the
  // USER turn (`assistant_discussion_turn.input_mode`, §1.4). The assistant
  // turn keeps `text`: it did not speak into a microphone, and whether its
  // answer was read aloud is this client's playback choice, not a fact about
  // the turn.

  // --- Issue #438: target-scoped discussion threads ------------------------
  const discussionEnabled = isDiscussionScreen(screenId);
  const candidate = useMemo(
    () => (discussionEnabled ? deriveDiscussionCandidate(screenId, location.search) : null),
    [discussionEnabled, screenId, location.search],
  );
  // Which of the two separable threads (whole screen vs. the selected
  // entity/element) is active. Explicit user choice wins; otherwise default
  // to the more specific one when there is one to focus on.
  //
  // Both this reset and the thread mirror below adjust state DURING RENDER
  // rather than in an effect (the pattern `cockpit/detail-panel.tsx` already
  // uses, and the rule #423 established): an effect would first paint one
  // frame showing the previous screen's or target's conversation, which is
  // precisely the mixing #438 exists to prevent.
  const [manualScope, setManualScope] = useState<"screen" | "focus" | null>(null);
  const [scopeScreenId, setScopeScreenId] = useState(screenId);
  if (scopeScreenId !== screenId) {
    setScopeScreenId(screenId);
    setManualScope(null);
  }
  const effectiveScope: "screen" | "focus" =
    (scopeScreenId === screenId ? manualScope : null) ?? (candidate ? "focus" : "screen");
  const screenTarget: AssistantDiscussionTargetIn = {
    scope: "screen", screen_id: screenId, target_kind: "screen", target_ref: screenId,
  };
  const activeTarget: AssistantDiscussionTargetIn | null = !discussionEnabled
    ? null
    : effectiveScope === "focus" && candidate
      ? candidate.target
      : screenTarget;
  const activeTargetKey = targetKeyOf(activeTarget);

  const threadQuery = useAssistantDiscussionThread(activeTarget);

  /** One persisted turn rendered as a panel message. */
  function turnMessages(detail: AssistantDiscussionThreadDetailOut): ChatMessage[] {
    return detail.turns.map((turn) => ({
      role: turn.role,
      text: turn.content,
      ...(turn.role === "assistant"
        ? {
            result: {
              screen_id: detail.thread.screen_id,
              answer: turn.content,
              suggested_actions: [],
              citations: turn.citations,
              used_fallback: turn.used_fallback,
              decision_method:
                turn.decision_method === "manual" ? "deterministic" : turn.decision_method,
              provider: turn.provider,
              model: turn.model,
              prompt_version: turn.prompt_version,
              schema_version: turn.schema_version,
              generated_at: turn.created_at,
            } satisfies AssistantAskOut,
          }
        : {}),
    }));
  }

  // The local mirror of the persisted thread, keyed by the target identity it
  // belongs to. The query key (`useAssistantDiscussionThread`) is what
  // actually separates the two conversations -- a target change yields a
  // different cache entry, so no other target's turns can be read. Carrying
  // the key here as well means that stays true even if this query later gains
  // `placeholderData`, which would hand a render the previous key's data.
  interface ThreadMirror {
    targetKey: string | null;
    threadId: number | null;
    messages: ChatMessage[];
    targetState: DiscussionTargetState | null;
  }
  const [mirror, setMirror] = useState<ThreadMirror>({
    targetKey: null, threadId: null, messages: [], targetState: null,
  });
  const threadDetail = threadQuery.data ?? null;
  let view = mirror;
  if (view.targetKey !== activeTargetKey) {
    view = { targetKey: activeTargetKey, threadId: null, messages: [], targetState: null };
  }
  // Restoring turns from the server covers both the initial mount and a
  // reload. It runs once per resolved thread id, so the turns appended
  // locally during this session are not overwritten by a later refetch.
  if (threadDetail && threadDetail.thread.id !== view.threadId) {
    view = {
      targetKey: activeTargetKey,
      threadId: threadDetail.thread.id,
      messages: turnMessages(threadDetail),
      targetState: threadDetail.target_state,
    };
  }
  if (view !== mirror) setMirror(view);

  // A failed thread endpoint (network error, older server) must not break the
  // assistant -- and neither must one that has not answered yet. Until a
  // thread is actually resolved the panel behaves exactly as it did before
  // #438: an in-memory per-screen conversation whose turns the client sends
  // itself. That is the safe migration path, and it is also what every
  // non-discussion screen keeps permanently.
  const activeThread = view.threadId !== null ? threadDetail?.thread ?? null : null;
  const useLegacyConversation = !discussionEnabled || activeThread === null;
  const messages = useLegacyConversation ? (threads[screenId] ?? []) : view.messages;
  // `null` while the thread is unavailable: 「まだ分からない」 is not
  // 「current」 (#366), so no banner and no recheck claim in that case.
  const targetState: DiscussionTargetState | null = useLegacyConversation
    ? null
    : view.targetState;

  // 閉じたらフォーカスを開くボタンへ戻す。
  //
  // `useModalSurface` の既定 (開く直前にフォーカスしていた要素へ戻す) は
  // ここでは効かない: このボタンはパネルが開いている間アンマウントされて
  // いて、閉じたときに **別のノードとして** 描き直されるので、フックが
  // 覚えている要素は既に DOM から外れている。戻し先はこの新しいノードで
  // なければならない。
  const openButtonRef = useRef<HTMLButtonElement>(null);
  const wasOpen = useRef(false);
  useEffect(() => {
    if (!open && wasOpen.current) openButtonRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  const { data: ctx } = useAssistantScreenContext(screenId, open);
  const ask = useAssistantAsk();

  // System Brief and other in-page review affordances can open this existing
  // conversation surface with a contextual draft. The draft is never sent
  // automatically: the developer still reviews and submits it explicitly.
  useEffect(() => {
    const handleOpen = (event: Event) => {
      const detail = (event as CustomEvent<OpenAssistantDetail>).detail;
      setOpen(true);
      if (detail?.question) setQuestion(detail.question);
    };
    window.addEventListener(OPEN_ASSISTANT_EVENT, handleOpen);
    return () => window.removeEventListener(OPEN_ASSISTANT_EVENT, handleOpen);
  }, []);

  const failingChecks = useMemo(
    () => (ctx?.screen_checks ?? []).filter((c) => c.severity !== "ok"),
    [ctx],
  );
  const noticeText = focusedStateItem?.summary ?? snapshotNotice ?? null;
  const handleNoticeClick = onSnapshotNoticeClick ?? (() => {
    const target = focusedStateItem ? systemStateTarget(focusedStateItem) : null;
    if (target) navigate(target);
  });
  const focusedQuestion = focusedStateItem
    ? `What should I do about: ${focusedStateItem.summary}`
    : null;

  // One append, two stores: the persisted thread's local mirror when a
  // thread is driving this conversation, the legacy per-screen map
  // otherwise. Writing to both would show the same turn twice the moment a
  // thread resolves mid-conversation.
  const appendMessages = (msgs: ChatMessage[]) => {
    if (useLegacyConversation) {
      setThreads((prev) => ({ ...prev, [screenId]: [...(prev[screenId] ?? []), ...msgs] }));
    } else {
      setMirror((prev) => ({ ...prev, messages: [...prev.messages, ...msgs] }));
    }
    requestAnimationFrame(() => {
      const list = listRef.current;
      if (list && typeof list.scrollTo === "function") {
        list.scrollTo({ top: list.scrollHeight });
      }
    });
  };

  /**
   * The single `/assistant/ask` path for both text and voice questions
   * (Issue #441: "do not add a second ask path"). `voiceTurn`, when given,
   * is a snapshot captured at the START of a voice utterance (see
   * `captureVoiceTurnTarget` below) and overrides every ambient
   * screen/thread/route value this function would otherwise read -- so a
   * navigation or scope switch that happens while the utterance is still in
   * flight cannot silently re-point the question that is already being
   * asked. Returns the answer text (for voice playback) or `null` on
   * failure; the failure itself is still recorded in the conversation
   * history exactly as it always was.
   */
  const submit = async (q: string, voiceTurn?: VoiceTurnTarget): Promise<string | null> => {
    const trimmed = q.trim();
    if (!trimmed || ask.isPending) return null;
    setQuestion("");
    // The target this turn is about is captured HERE (or, for voice, was
    // already captured at listening-start and handed in) and used for the
    // whole exchange. Navigating mid-answer must not silently re-point the
    // question that is already in flight.
    const turnScreenId = voiceTurn?.screenId ?? screenId;
    const turnThread = voiceTurn
      ? (voiceTurn.useLegacy ? null : voiceTurn.thread)
      : (useLegacyConversation ? null : activeThread);
    // `appendMessages` still files into whichever store is AMBIENT right now
    // (it does not know about `voiceTurn`). The message list is hidden while
    // voice mode is active, so this cannot show a turn under the wrong
    // conversation while it is happening; it is a known simplification for
    // the rare case of navigating to a different discussion target mid
    // utterance, not a gap in the request itself (`turnThread` above is what
    // decides what actually gets asked and persisted server-side).
    appendMessages([{ role: "user", text: trimmed }]);
    try {
      // Keep a bounded multi-turn discussion context. The current question is
      // sent separately, so only turns that existed before this submit belong
      // here. Errors are UI state, never conversation evidence.
      //
      // With a thread the server derives that context from the persisted
      // turns instead, and sending our own would be a second source of truth
      // (the API rejects both together with 422).
      const conversation = turnThread
        ? []
        : messages
            .filter((message): message is ChatMessage & { role: "user" | "assistant" } =>
              message.role === "user" || message.role === "assistant",
            )
            .slice(-12)
            .map((message) => ({ role: message.role, content: message.text.slice(0, 4000) }));
      const baseRouteParams = voiceTurn?.routeParams ?? Object.fromEntries(new URLSearchParams(location.search));
      // The hovered/selected help-mode element (#440), when there is one, is
      // carried as an ordinary route param -- §4 deliberately does not add a
      // new `DiscussionTargetKind` for element-scoped voice questions (the
      // finite set in docs/assistant-discussion.md §1.1 is closed), and the
      // server already accepts arbitrary route params for screen data
      // providers.
      const routeParams = voiceTurn?.helpId
        ? { ...baseRouteParams, [VOICE_ELEMENT_HELP_ID_PARAM]: voiceTurn.helpId }
        : baseRouteParams;
      const result = await ask.mutateAsync({
        screen_id: turnScreenId,
        question: trimmed,
        route_params: routeParams,
        conversation,
        ...(voiceTurn ? { input_mode: "voice" as const } : {}),
        ...(turnThread ? { thread_id: turnThread.id } : {}),
        visible_check_ids: failingChecks.map((c) => c.check_id),
        ...(focusedStateItem ? {
          visible_state_ids: [focusedStateItem.state_id],
          focused_state_id: focusedStateItem.state_id,
        } : {}),
      });
      appendMessages([{ role: "assistant", text: result.answer, result }]);
      // The answer re-pins the thread to the content it was actually
      // produced against, so a resolved recheck stops being advertised.
      if (turnThread && result.target_state) {
        const answered = result.target_state;
        setMirror((prev) =>
          prev.threadId === turnThread.id ? { ...prev, targetState: answered } : prev,
        );
      }
      return result.answer;
    } catch (err) {
      appendMessages([{ role: "error", text: String(err) }]);
      return null;
    }
  };

  /** Issue #441: snapshot the whole discussion target at voice-turn start. */
  const captureVoiceTurnTarget = (): VoiceTurnTarget => ({
    screenId,
    useLegacy: useLegacyConversation,
    thread: useLegacyConversation ? null : activeThread,
    routeParams: Object.fromEntries(new URLSearchParams(location.search)),
    helpId: helpMode.target,
  });

  const handleVoiceAdapterError = (reason: VoiceErrorReason) => {
    // §4: a microphone denial or an STT/TTS failure shows the reason and
    // returns safely to text mode -- the panel stays fully usable, it does
    // not just go blank or get stuck in a broken voice UI.
    setVoiceActive(false);
    setVoiceFallbackNotice(VOICE_ERROR_MESSAGES[reason]);
  };

  if (!open) {
    return (
      // Issue #358 追補: 浮いているボタンは本文の上に重なるので、その分の
      // 余白を `<main>` が `pb-24` で確保している (`app-layout.tsx`)。両者は
      // 対で意味を持つ -- ここの `bottom-*` を大きくするなら、その余白も
      // 一緒に増やさないと画面の主操作を覆う (#102: 主操作を隠さない)。
      // 狭い画面では画面端へ寄せて、本文の実表示幅を削らない。
      <div className="fixed bottom-4 right-4 z-40 flex items-end gap-2 md:bottom-6 md:right-6">
        {noticeText && (
          <button
            type="button"
            onClick={handleNoticeClick}
            className="relative max-w-[min(18rem,calc(100vw-5.5rem))] rounded-lg border border-amber-300 bg-amber-50 px-3 py-2 text-left text-xs font-medium text-amber-900 shadow-lg transition-colors hover:bg-amber-100 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100 dark:hover:bg-amber-900"
            title="対応する画面へ移動"
            data-testid="assistant-snapshot-notice"
          >
            {noticeText}
            <span className="absolute -right-1 bottom-4 h-2 w-2 rotate-45 border-r border-t border-amber-300 bg-amber-50 dark:border-amber-800 dark:bg-amber-950" />
          </button>
        )}
        <Button
          ref={openButtonRef}
          size="icon"
          onClick={() => setOpen(true)}
          title="Ask the assistant about this screen"
          data-testid="assistant-button"
          className="h-11 w-11 rounded-full shadow-lg"
        >
          <Bot className="h-5 w-5" />
        </Button>
      </div>
    );
  }

  return (
    <>
      {/* 開いているパネルは本文の上に重なるモーダルな面。とくに 390px 幅では
          画面全体を覆うので、閉じる手段が右上のボタン 1 つだけだと逃げ場が
          無くなる。背景クリック・Escape・フォーカストラップは、サイドバー
          Drawer (#362) と同じ `useModalSurface` の規則で揃える。 */}
      <div
        className="fixed inset-0 z-40 bg-black/40"
        onClick={() => setOpen(false)}
        aria-hidden="true"
        data-testid="assistant-backdrop"
      />
      <div
        ref={panelRef}
        tabIndex={-1}
        role="dialog"
        aria-modal="true"
        aria-label="画面アシスタント"
        className="fixed inset-y-0 right-0 z-50 flex w-full max-w-md flex-col border-l bg-background shadow-xl focus-visible:outline-none"
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
        <div className="flex items-center gap-1 shrink-0">
          {/* Issue #441: a voice-mode toggle that is disabled (with a stated
              reason) rather than hidden when the browser/context cannot
              support it -- the developer sees WHY, not just its absence. */}
          <Button
            variant={voiceActive ? "default" : "ghost"}
            size="icon"
            disabled={!voiceReady}
            aria-pressed={voiceActive}
            onClick={() => {
              setVoiceFallbackNotice(null);
              setVoiceActive((prev) => !prev);
            }}
            title={voiceReady ? "音声で質問する" : VOICE_PREREQUISITE_MESSAGE[voicePrereq]}
            aria-label={voiceReady ? "音声で質問する" : VOICE_PREREQUISITE_MESSAGE[voicePrereq]}
            data-testid="assistant-voice-toggle"
          >
            <Mic className="h-4 w-4" />
          </Button>
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
      </div>
      {!voiceReady && (
        <p
          className="border-b bg-muted/50 px-4 py-1.5 text-[11px] text-muted-foreground"
          data-testid="voice-unavailable-notice"
        >
          {VOICE_PREREQUISITE_MESSAGE[voicePrereq]}
        </p>
      )}

      {/* Issue #438: the two threads a discussion screen can hold are
          separable, so which one the developer is talking in has to be
          visible and switchable. The switch changes the conversation's
          identity -- it never sends the other target's history. */}
      {discussionEnabled && candidate && (
        <div
          className="flex flex-wrap items-center gap-1.5 border-b px-4 py-2"
          data-testid="assistant-scope-switch"
        >
          <span className="text-[11px] text-muted-foreground">この会話の対象</span>
          <Button
            size="sm"
            variant={effectiveScope === "screen" ? "default" : "outline"}
            className="h-7 px-2 text-xs"
            aria-pressed={effectiveScope === "screen"}
            onClick={() => setManualScope("screen")}
            data-testid="assistant-scope-screen"
          >
            画面全体
          </Button>
          <Button
            size="sm"
            variant={effectiveScope === "focus" ? "default" : "outline"}
            className="h-7 px-2 text-xs"
            aria-pressed={effectiveScope === "focus"}
            onClick={() => setManualScope("focus")}
            data-testid="assistant-scope-focus"
          >
            {candidate.label}
          </Button>
        </div>
      )}

      {voiceActive ? (
        // Issue #441: while voice mode is active the message list is
        // replaced (not merely covered) by the voice surface -- history is
        // untouched in state and reappears exactly as it was the moment
        // voice mode exits.
        <AssistantVoice
          captureTurnTarget={captureVoiceTurnTarget}
          onTranscript={(text, target) => submit(text, target)}
          onAdapterError={handleVoiceAdapterError}
          onExit={() => setVoiceActive(false)}
          scopeLabel={voiceScopeLabel}
        />
      ) : (
        <>
      {voiceFallbackNotice && (
        <div
          className="mx-4 mt-3 rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
          role="status"
          data-testid="voice-fallback-notice"
        >
          <p className="text-xs">{voiceFallbackNotice}</p>
        </div>
      )}
      <div ref={listRef} className="flex-1 overflow-y-auto p-4 space-y-3" data-testid="assistant-message-list">
        {/* §1.3: history stays readable, but it is not a current fact. The
            server has already withheld it from the model's context; say so
            rather than letting the transcript imply it was used. */}
        {(targetState === "stale" || targetState === "unresolvable") && (
          <div
            className="rounded-lg border border-amber-300 bg-amber-50 p-3 dark:border-amber-800 dark:bg-amber-950"
            role="status"
            data-testid="assistant-target-stale"
          >
            <p className="text-xs">{staleBannerText(targetState)}</p>
          </div>
        )}
        {focusedStateItem && (
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3 space-y-2 dark:border-amber-800 dark:bg-amber-950" data-testid="assistant-current-issue">
            <p className="text-xs font-medium">Current issue</p>
            <p className="text-sm font-medium">{focusedStateItem.summary}</p>
            <p className="text-xs text-muted-foreground">{focusedStateItem.detail}</p>
            {focusedStateItem.remediation && <p className="text-xs">{focusedStateItem.remediation}</p>}
            {focusedStateItem.target_ui && (
              <Button size="sm" onClick={() => navigate(systemStateTarget(focusedStateItem) ?? focusedStateItem.target_ui!.route)} data-testid="assistant-current-issue-action">
                {focusedStateItem.target_ui.action_label || "対応する"}
              </Button>
            )}
          </div>
        )}
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
              {focusedQuestion && (
                <button
                  className="text-left text-xs text-primary hover:underline cursor-pointer"
                  onClick={() => submit(focusedQuestion)}
                  data-testid="assistant-focused-state-question"
                >
                  {focusedQuestion}
                </button>
              )}
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
          placeholder="この画面のデータ構造や目的について質問…"
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
        </>
      )}
      </div>
    </>
  );
}

// 音声対話 (Issue #441, Epic #436) Phase 1 の会話面。
//
// turn-based (発話 → 認識確定 → 応答 → (音声で読み上げ) → 待機) のみを扱う。
// Phase 2 (WebSocket ストリーミング / VAD / barge-in / reconnect / コスト
// 上限) は対象外 -- 着手する場合は transcript の可視性と保持方針を先に
// 決めること (`docs/assistant-discussion.md` §4)。
//
// このコンポーネント自身は「対象がどう決まるか」を一切知らない --
// `captureTurnTarget` が turn 開始の瞬間に呼ばれ、その戻り値 (`Target`) が
// `onTranscript` へそのまま運ばれるだけ。これにより「発話が始まった後に
// 選択 (対象・スコープ) が変わっても、その turn は開始時に捕まえた対象で
// 答える」という §4 の要件を、クロージャのタイミングに頼らず素朴な ref
// で満たす -- 呼び出し元 (`assistant-panel.tsx`) がその turn のためだけの
// スナップショットを作り、それ以降 assistant-panel 側の状態がどう変わって
// も、この ref の値は次の `captureTurnTarget()` 呼び出し (= 次の turn) まで
// 変わらない。

import { useMemo, useRef, useState } from "react";
import { Bot, Mic, PhoneOff, Volume2, VolumeX } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import {
  createBrowserVoiceAdapters,
  VOICE_ERROR_MESSAGES,
  type SpeechToTextAdapter,
  type TextToSpeechAdapter,
  type VoiceErrorReason,
} from "@/lib/voice-adapter";

export type VoiceState = "idle" | "listening" | "thinking" | "speaking" | "error";

const IDLE_STATUS_TEXT = "マイクのボタンを押して話しかけてください。";

const VOICE_STATE_LABEL: Record<VoiceState, string> = {
  idle: IDLE_STATUS_TEXT,
  listening: "聞いています…",
  thinking: "考えています…",
  speaking: "話しています…",
  error: "エラーが発生しました。",
};

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

export interface AssistantVoiceProps<Target> {
  /** この turn が「何についての発話か」を、開始した瞬間の状態から作る。 */
  captureTurnTarget: () => Target;
  /**
   * 認識確定後に呼ばれる。既存の `/assistant/ask` 経路 (`submit()`) を
   * そのまま呼び出す想定 -- ここで新しい ask 経路を作らない。返り値が
   * あれば読み上げる文面、`null` は「読み上げるものがない (失敗など、
   * 通常のエラー表示は既存の会話履歴側で行う)」。
   */
  onTranscript: (text: string, target: Target) => Promise<string | null>;
  /** マイク拒否 / STT / TTS の失敗。呼び出し元はテキストモードへ戻す。 */
  onAdapterError: (reason: VoiceErrorReason) => void;
  /** 「音声モードを終了」。 */
  onExit: () => void;
  /** 「この発話は画面全体について」か「この要素について」かの表示文言。 */
  scopeLabel: string;
  /** テスト用に差し込むための adapter。省略時はブラウザ実装を使う。 */
  adapters?: { stt: SpeechToTextAdapter; tts: TextToSpeechAdapter } | null;
}

export function AssistantVoice<Target>({
  captureTurnTarget,
  onTranscript,
  onAdapterError,
  onExit,
  scopeLabel,
  adapters,
}: AssistantVoiceProps<Target>) {
  const resolvedAdapters = adapters !== undefined ? adapters : createBrowserVoiceAdapters();
  const [state, setState] = useState<VoiceState>("idle");
  const [errorReason, setErrorReason] = useState<VoiceErrorReason | null>(null);
  const [muted, setMuted] = useState(false);
  const turnTargetRef = useRef<Target | null>(null);
  const reducedMotion = usePrefersReducedMotion();

  function fail(reason: VoiceErrorReason) {
    setState("error");
    setErrorReason(reason);
    // マイク拒否 / STT 失敗 / TTS 失敗 は、ここでエラー状態を示した後
    // テキストモードへ安全に戻す -- その決定は呼び出し元 (assistant-panel)
    // が持つ (フォールバック通知を出し、パネルを通常表示に戻す)。
    onAdapterError(reason);
  }

  function returnToIdle() {
    setState("idle");
  }

  function startTurn() {
    if (!resolvedAdapters) {
      fail("unsupported");
      return;
    }
    turnTargetRef.current = captureTurnTarget();
    setState("listening");
    resolvedAdapters.stt.start({
      onResult: (text) => {
        setState("thinking");
        const target = turnTargetRef.current as Target;
        onTranscript(text, target)
          .then((answer) => {
            if (!answer || muted) {
              returnToIdle();
              return undefined;
            }
            setState("speaking");
            return resolvedAdapters.tts.speak(answer).then(returnToIdle, () => fail("tts_failed"));
          })
          .catch(() => {
            // /assistant/ask 自体の失敗は既存の会話履歴 (エラーメッセージ)
            // 側で扱われる普通のエラーであり、adapter の障害ではない --
            // 音声モードを終了させず、次の発話へ戻るだけにする。
            returnToIdle();
          });
      },
      onError: (reason) => fail(reason),
      onEnd: () => {
        setState((prev) => (prev === "listening" ? "idle" : prev));
      },
    });
  }

  function stop() {
    resolvedAdapters?.stt.stop();
    resolvedAdapters?.tts.cancel();
    returnToIdle();
  }

  function toggleMute() {
    setMuted((prev) => !prev);
  }

  const statusText =
    state === "error" && errorReason ? VOICE_ERROR_MESSAGES[errorReason] : VOICE_STATE_LABEL[state];

  return (
    <div className="flex flex-1 flex-col" data-testid="assistant-voice">
      <div className="flex items-center justify-between gap-2 border-b px-4 py-2">
        <span className="text-[11px] text-muted-foreground" data-testid="voice-scope">
          対象: {scopeLabel}
        </span>
      </div>

      <div className="flex flex-1 flex-col items-center justify-center gap-4 p-6">
        <div
          data-testid="voice-bot"
          data-state={state}
          className={cn(
            "flex h-24 w-24 items-center justify-center rounded-full bg-primary/10 text-primary",
            "shadow-[0_0_28px_2px_rgba(99,102,241,0.35)]",
            !reducedMotion && "animate-pulse",
          )}
        >
          <Bot className="h-10 w-10" />
        </div>

        {/* 色だけでなく text + aria-live で状態を伝える (§4)。 */}
        <div
          role="status"
          aria-live="polite"
          data-testid="voice-state"
          data-state={state}
          className={cn(
            "text-sm font-medium",
            state === "error" ? "text-destructive" : "text-foreground",
          )}
        >
          {statusText}
        </div>

        {state === "idle" && (
          <Button onClick={startTurn} data-testid="voice-talk">
            <Mic className="mr-1.5 h-4 w-4" />
            話しかける
          </Button>
        )}
      </div>

      {/* stop / mute / exit はどの状態でも常に押せる -- thinking / speaking
          の最中でも利用者が必ず抜けられるようにするため (§4)。disabled は
          付けない。 */}
      <div className="flex items-center justify-center gap-2 border-t p-3">
        <Button variant="outline" size="sm" onClick={stop} data-testid="voice-stop">
          停止
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={toggleMute}
          aria-pressed={muted}
          data-testid="voice-mute"
        >
          {muted ? <VolumeX className="mr-1.5 h-3.5 w-3.5" /> : <Volume2 className="mr-1.5 h-3.5 w-3.5" />}
          {muted ? "読み上げ: オフ" : "読み上げ: オン"}
        </Button>
        <Button variant="outline" size="sm" onClick={onExit} data-testid="voice-exit">
          <PhoneOff className="mr-1.5 h-3.5 w-3.5" />
          音声モードを終了
        </Button>
      </div>
    </div>
  );
}

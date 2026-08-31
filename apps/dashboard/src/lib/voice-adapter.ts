// 音声対話 (Issue #441, Epic #436) Phase 1: provider-neutral な voice adapter。
//
// §0 / §4 の境界:
// - 音声バイナリはブラウザの外へ一切出さない。STT/TTS はどちらもブラウザ内
//   Web Speech API を直接叩くだけで、サーバへ音声を送る経路そのものが存在
//   しない -- 「音声は永続保存されない」は、ここにネットワーク送信コードを
//   一切書かないことで構造的に保証する (ポリシーではなく構造)。
// - 有限語彙 (Principle 6): `VoicePrerequisite` と `VoiceErrorReason` は
//   どちらも小さく明示的な集合。`permission_denied` (マイクを拒否された) と
//   `stt_failed` (認識処理自体が失敗した) は別の答えで、対応も異なるため
//   決して 1 つに丸めない。
// - このファイルは React 非依存。`assistant-voice.tsx` から状態機械として
//   利用され、fake object を差し込むだけで単体テストできる。
// - Phase 2 (WebSocket ストリーミング / VAD / barge-in / reconnect /
//   コスト上限) はここでは実装しない。turn-based (発話 → 認識確定 → 応答)
//   のみを扱う。

/** ブラウザが音声対話を実行できる状態にあるか。 */
export type VoicePrerequisite = "ready" | "insecure_context" | "unsupported";

/**
 * 有限のエラー理由。`permission_denied` (マイク拒否) と `stt_failed`
 * (認識処理の失敗) は原因も開発者が取るべき対応も異なるため区別する。
 */
export type VoiceErrorReason =
  | "permission_denied"
  | "no_speech"
  | "stt_failed"
  | "tts_failed"
  | "unsupported";

/** 各理由に対応する、利用者向けの日本語メッセージ (唯一の正本)。 */
export const VOICE_ERROR_MESSAGES: Record<VoiceErrorReason, string> = {
  permission_denied:
    "マイクの利用が許可されていません。ブラウザの設定でマイクへのアクセスを許可してください。",
  no_speech: "音声が聞き取れませんでした。もう一度お試しください。",
  stt_failed: "音声の認識に失敗しました。テキスト入力に切り替えます。",
  tts_failed: "音声の再生に失敗しました。テキスト入力に切り替えます。",
  unsupported: "このブラウザは音声対話に対応していません。",
};

export interface SpeechToTextAdapter {
  start(handlers: {
    onResult(text: string): void;
    onError(reason: VoiceErrorReason): void;
    onEnd(): void;
  }): void;
  stop(): void;
}

export interface TextToSpeechAdapter {
  speak(text: string): Promise<void>;
  cancel(): void;
}

/** `onTranscript`/`speak` の失敗理由を `VoiceErrorReason` のまま運ぶための Error。 */
export class VoiceAdapterError extends Error {
  readonly reason: VoiceErrorReason;
  constructor(reason: VoiceErrorReason) {
    super(VOICE_ERROR_MESSAGES[reason]);
    this.reason = reason;
    this.name = "VoiceAdapterError";
  }
}

// --- Web Speech API (STT) は標準 TypeScript の DOM lib に型が無いベンダー
// 拡張なので、ここで使う分だけの最小限のインターフェースを自前で持つ
// (`SpeechSynthesisUtterance` は標準 lib にあるので TTS 側は型を足さない)。

interface SpeechRecognitionResultLike {
  readonly length: number;
  readonly isFinal?: boolean;
  [index: number]: { transcript: string };
}

interface SpeechRecognitionEventLike extends Event {
  readonly resultIndex: number;
  readonly results: ArrayLike<SpeechRecognitionResultLike>;
}

interface SpeechRecognitionErrorEventLike extends Event {
  readonly error: string;
}

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorEventLike) => void) | null;
  onend: (() => void) | null;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

interface VoiceGlobalWindow {
  SpeechRecognition?: SpeechRecognitionCtor;
  webkitSpeechRecognition?: SpeechRecognitionCtor;
  speechSynthesis?: SpeechSynthesis;
  isSecureContext?: boolean;
}

function voiceWindow(): VoiceGlobalWindow | null {
  return typeof window === "undefined" ? null : (window as unknown as VoiceGlobalWindow);
}

function getSpeechRecognitionCtor(): SpeechRecognitionCtor | null {
  const w = voiceWindow();
  if (!w) return null;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

function hasSpeechSynthesis(): boolean {
  const w = voiceWindow();
  return !!w && typeof w.speechSynthesis !== "undefined";
}

/**
 * 現在のブラウザ/コンテキストが音声対話を実行できる状態かを判定する。
 * fail-closed: 何か 1 つでも欠けていれば `ready` にはしない。
 */
export function voicePrerequisite(): VoicePrerequisite {
  const w = voiceWindow();
  if (!w) return "unsupported";
  if (w.isSecureContext === false) return "insecure_context";
  const hasStt = !!getSpeechRecognitionCtor();
  const hasTts = hasSpeechSynthesis();
  if (!hasStt && !hasTts) return "unsupported";
  return "ready";
}

function mapRecognitionError(code: string): VoiceErrorReason {
  if (code === "not-allowed" || code === "permission-denied" || code === "service-not-allowed") {
    return "permission_denied";
  }
  if (code === "no-speech") return "no_speech";
  return "stt_failed";
}

function createSpeechToTextAdapter(): SpeechToTextAdapter {
  let recognition: SpeechRecognitionLike | null = null;

  return {
    start(handlers) {
      const Ctor = getSpeechRecognitionCtor();
      if (!Ctor) {
        // このブラウザに認識 API 自体が無い -- 開始すらできないので即座に
        // 安全側 (unsupported) へ倒す。呼び出し側は STT 失敗と区別できる。
        handlers.onError("unsupported");
        handlers.onEnd();
        return;
      }
      try {
        const instance = new Ctor();
        recognition = instance;
        instance.lang = "ja-JP";
        instance.interimResults = false;
        instance.continuous = false;
        instance.onresult = (event) => {
          const last = event.results[event.results.length - 1];
          const transcript = last?.[0]?.transcript ?? "";
          if (transcript.trim()) handlers.onResult(transcript);
        };
        instance.onerror = (event) => {
          handlers.onError(mapRecognitionError(event.error));
        };
        instance.onend = () => handlers.onEnd();
        instance.start();
      } catch {
        handlers.onError("stt_failed");
        handlers.onEnd();
      }
    },
    stop() {
      try {
        recognition?.stop();
      } catch {
        // stop() を呼べる状態でなければ何もしない -- 二重停止は無害。
      }
    },
  };
}

function createTextToSpeechAdapter(): TextToSpeechAdapter {
  return {
    speak(text) {
      return new Promise<void>((resolve, reject) => {
        const w = voiceWindow();
        if (!w?.speechSynthesis) {
          reject(new VoiceAdapterError("tts_failed"));
          return;
        }
        try {
          const utterance = new SpeechSynthesisUtterance(text);
          utterance.lang = "ja-JP";
          utterance.onend = () => resolve();
          utterance.onerror = () => reject(new VoiceAdapterError("tts_failed"));
          w.speechSynthesis.cancel();
          w.speechSynthesis.speak(utterance);
        } catch {
          reject(new VoiceAdapterError("tts_failed"));
        }
      });
    },
    cancel() {
      try {
        voiceWindow()?.speechSynthesis?.cancel();
      } catch {
        // 再生中でなければ何もしない。
      }
    },
  };
}

/**
 * ブラウザ実装の adapter ペアを作る。前提を満たさない環境では `null` を返し
 * (呼び出し側は音声トグルを無効化する)、Web Speech API を直接インスタンス化
 * しないので、この関数を呼ぶこと自体はどの環境でも安全 (副作用なし)。
 */
export function createBrowserVoiceAdapters(): {
  stt: SpeechToTextAdapter;
  tts: TextToSpeechAdapter;
} | null {
  if (voicePrerequisite() !== "ready") return null;
  return { stt: createSpeechToTextAdapter(), tts: createTextToSpeechAdapter() };
}

// 音声対話 (Issue #441, Epic #436) Phase 1: provider-neutral な voice adapter。
//
// §0 / §4 の境界:
// - STT はブラウザの Web Speech API のままなので、録音音声を Control Server
//   へ送信・保存しない。TTS は短いテキストだけを Control Server に送り、API
//   key をブラウザへ露出せず OpenAI Speech API で生成した音声を再生する。
// - 有限語彙 (Principle 6): `VoicePrerequisite` と `VoiceErrorReason` は
//   どちらも小さく明示的な集合。`permission_denied` (マイクを拒否された) と
//   `stt_failed` (認識処理自体が失敗した) は別の答えで、対応も異なるため
//   決して 1 つに丸めない。
// - このファイルは React 非依存。`assistant-voice.tsx` から状態機械として
//   利用され、fake object を差し込むだけで単体テストできる。
// - WebSocket / VAD は使わない。再生中の「話を挟む」は音声取得・再生を即時
//   cancel して次の turn の STT を始める、明示的な turn-based barge-in。

import { api } from "@/api/client";

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

/**
 * 現在のブラウザ/コンテキストが音声対話を実行できる状態かを判定する。
 * fail-closed: 何か 1 つでも欠けていれば `ready` にはしない。
 */
export function voicePrerequisite(): VoicePrerequisite {
  const w = voiceWindow();
  if (!w) return "unsupported";
  if (w.isSecureContext === false) return "insecure_context";
  const hasStt = !!getSpeechRecognitionCtor();
  // TTS is provided by the server-side OpenAI Speech API, so the browser only
  // needs its recognition half. Configuration/upstream failures are reported
  // by the TTS adapter after the answer is generated.
  if (!hasStt) return "unsupported";
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
  let controller: AbortController | null = null;
  let audio: HTMLAudioElement | null = null;
  let objectUrl: string | null = null;
  let rejectPlayback: ((reason: unknown) => void) | null = null;

  const release = () => {
    if (objectUrl) URL.revokeObjectURL(objectUrl);
    objectUrl = null;
    audio = null;
    controller = null;
    rejectPlayback = null;
  };

  const cancel = () => {
    controller?.abort();
    if (audio) {
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
    }
    rejectPlayback?.(new DOMException("Speech playback cancelled", "AbortError"));
    release();
  };

  return {
    async speak(text) {
      cancel();
      const ownController = new AbortController();
      controller = ownController;
      try {
        const blob = await api.postBlob(
          "/assistant/speech",
          { text },
          ownController.signal,
        );
        if (ownController.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        const player = new Audio(objectUrl);
        audio = player;
        await new Promise<void>((resolve, reject) => {
          rejectPlayback = reject;
          player.onended = () => {
            release();
            resolve();
          };
          player.onerror = () => {
            release();
            reject(new VoiceAdapterError("tts_failed"));
          };
          player.play().catch((error) => {
            release();
            reject(error);
          });
        });
      } catch (error) {
        if (ownController.signal.aborted) return;
        release();
        throw error instanceof VoiceAdapterError
          ? error
          : new VoiceAdapterError("tts_failed");
      }
    },
    cancel,
  };
}

/**
 * ブラウザ実装の adapter ペアを作る。前提を満たさない環境では `null` を返し
 * (呼び出し側は音声トグルを無効化する)。TTS adapter は生成時には通信せず、
 * `speak` の時だけ認証済みの Control Server endpoint を呼ぶ。
 */
export function createBrowserVoiceAdapters(): {
  stt: SpeechToTextAdapter;
  tts: TextToSpeechAdapter;
} | null {
  if (voicePrerequisite() !== "ready") return null;
  return { stt: createSpeechToTextAdapter(), tts: createTextToSpeechAdapter() };
}

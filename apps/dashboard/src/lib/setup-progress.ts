// Issue #466 (UX-19): Setup Guide の「実行形態」と手動の完了チェックを、
// 利用者 × System ごとにこのブラウザーへ保存する。token 発行など必要な画面
// 移動から戻っても進捗が消えないようにするため。
//
// 保存するのは「どのパターンを選んだか」と「どの項目にチェックしたか」だけ
// で、秘密値は含まない。キーに利用者と System を含めるので、別の利用者・
// 別の System の進捗は復元されない。localStorage は使えない環境 (プライベート
// ウィンドウ・ブロック設定) があるので、読み書きはすべて失敗しても画面が
// 動くようにする (その場合は保存されないだけ)。
//
// これは利用者の手動の記録であって、接続できたことの証明ではない。接続の
// 事実はサーバーの接続ステータス (`/connectivity`) が正本で、画面では両者を
// 別の表示にする。

const PREFIX = "probe-agent:setup-guide:v1";

export interface SetupProgress {
  pattern: string | null;
  /** `${pattern}:${item}` → true */
  checked: Record<string, boolean>;
}

export const EMPTY_SETUP_PROGRESS: SetupProgress = { pattern: null, checked: {} };

export function setupProgressKey(userId: number | null | undefined, systemId: number | null | undefined): string | null {
  if (userId == null || systemId == null) return null;
  return `${PREFIX}:user:${userId}:system:${systemId}`;
}

export function loadSetupProgress(key: string | null): SetupProgress {
  if (!key) return EMPTY_SETUP_PROGRESS;
  try {
    const raw = window.localStorage.getItem(key);
    if (!raw) return EMPTY_SETUP_PROGRESS;
    const parsed = JSON.parse(raw) as Partial<SetupProgress>;
    const checked: Record<string, boolean> = {};
    if (parsed.checked && typeof parsed.checked === "object") {
      for (const [k, v] of Object.entries(parsed.checked)) {
        if (v === true) checked[k] = true;
      }
    }
    return { pattern: typeof parsed.pattern === "string" ? parsed.pattern : null, checked };
  } catch {
    return EMPTY_SETUP_PROGRESS;
  }
}

export function saveSetupProgress(key: string | null, progress: SetupProgress): boolean {
  if (!key) return false;
  try {
    window.localStorage.setItem(key, JSON.stringify(progress));
    return true;
  } catch {
    return false;
  }
}

// 機能解説モード (Issue #440, Epic #436) の状態を Header とページで共有する
// 唯一の場所。`HelpModeProvider` を `AppLayout` に 1 つだけマウントし、
// `useHelpMode()` で読み書きする。
//
// 保持するのは「解説モードが有効か」と「いま選択されている help target」だけ
// で、解説文そのものはここには無い (取得は `HelpModeLayer` 側が
// `useUiHelpEntry` で行う)。#441 (音声モード) はこの `target` を
// 「hover 対象なし = screen scope、hover 対象あり = element scope」の判定に
// 再利用するための、安定した小さな公開 API として意図している。

import { createContext, useCallback, useContext, useMemo, useState, type ReactNode } from "react";
import type { HelpId } from "./ui-help";

export interface HelpModeContextValue {
  /** 解説モードが有効かどうか。 */
  active: boolean;
  /** 現在ホバー/フォーカス/タップで選択されている help target。未選択は null。 */
  target: HelpId | null;
  /** target を明示的に設定する (hover debounce / focus / tap のいずれかから)。 */
  setTarget: (id: HelpId | null) => void;
  /** ヘッダーのトグルボタン・再クリック用。 */
  toggle: () => void;
  /** Escape・「解説モードを終了」ボタン用。target も必ずクリアする。 */
  exit: () => void;
}

const HelpModeContext = createContext<HelpModeContextValue | null>(null);

export function HelpModeProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(false);
  const [target, setTargetState] = useState<HelpId | null>(null);

  const setTarget = useCallback((id: HelpId | null) => {
    setTargetState(id);
  }, []);

  const exit = useCallback(() => {
    setActive(false);
    setTargetState(null);
  }, []);

  const toggle = useCallback(() => {
    setActive((prev) => {
      const next = !prev;
      if (!next) setTargetState(null);
      return next;
    });
  }, []);

  const value = useMemo<HelpModeContextValue>(
    () => ({ active, target, setTarget, toggle, exit }),
    [active, target, setTarget, toggle, exit],
  );

  return <HelpModeContext.Provider value={value}>{children}</HelpModeContext.Provider>;
}

// Provider が無いツリー (単体レンダリングされる既存テストなど) でも壊れない
// よう、フォールバック値を返す。フォールバックは常に非活性で、setTarget /
// toggle / exit は何もしない no-op -- 呼び出し側は「解説モードは無効」として
// 扱われるだけで、例外にはしない。
const NOOP_HELP_MODE: HelpModeContextValue = {
  active: false,
  target: null,
  setTarget: () => {},
  toggle: () => {},
  exit: () => {},
};

export function useHelpMode(): HelpModeContextValue {
  const ctx = useContext(HelpModeContext);
  return ctx ?? NOOP_HELP_MODE;
}

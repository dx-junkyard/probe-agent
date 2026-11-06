import { act, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { AssistantWaitStatus } from "@/components/assistant-wait-status";

afterEach(() => vi.useRealTimers());

it("preserves elapsed time across closing/reopening and reports delay without claiming server progress", () => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-11-06T00:00:00Z"));
  const startedAt = Date.now();
  const first = render(<AssistantWaitStatus startedAt={startedAt} />);
  expect(screen.getByTestId("assistant-wait-status")).toHaveTextContent("経過 0 秒");
  expect(screen.getByText(/閉じても回答の受信を続けます/)).toBeInTheDocument();
  act(() => vi.advanceTimersByTime(31000));
  expect(screen.getByTestId("assistant-wait-status")).toHaveTextContent("経過 31 秒");
  expect(screen.getByRole("status")).toHaveTextContent("処理が停止したかどうかはまだ確認できません");
  first.unmount();
  act(() => vi.advanceTimersByTime(9000));
  render(<AssistantWaitStatus startedAt={startedAt} />);
  expect(screen.getByTestId("assistant-wait-status")).toHaveTextContent("経過 40 秒");
});

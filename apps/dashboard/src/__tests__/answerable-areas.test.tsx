/// <reference types="vitest/globals" />
// Issue #291: 回答可能領域チップのテスト。
//
// 1. 5領域が日本語ラベルで表示され、選択状態がトグルできること。
// 2. トグルすると PUT /interview/sessions/{id}/answerable-areas が呼ばれる
//    こと。
// 3. isOutOfArea の決定的ルール(空選択=フィルタなし、null領域=常に対象内)。

import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { vi } from "vitest";
import type { ReactNode } from "react";

const mockApi = {
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
};

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

vi.mock("sonner", () => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}));

function createWrapper() {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: 0 }, mutations: { retry: false } },
  });
  return ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe("AnswerableAreasControl", () => {
  test("renders all five areas in Japanese and toggles selection via PUT", async () => {
    mockApi.put.mockResolvedValue({ answerable_areas: ["implementation"] });

    const { AnswerableAreasControl } = await import(
      "@/components/system-understanding/answerable-areas"
    );
    render(
      <AnswerableAreasControl sessionId={1} answerableAreas={[]} />,
      { wrapper: createWrapper() },
    );

    expect(screen.getByText("事業・目的")).toBeInTheDocument();
    expect(screen.getByText("業務ルール")).toBeInTheDocument();
    expect(screen.getByText("運用")).toBeInTheDocument();
    expect(screen.getByText("実装")).toBeInTheDocument();
    expect(screen.getByText("セキュリティ")).toBeInTheDocument();

    const chip = screen.getByTestId("answerable-area-chip-implementation");
    expect(chip).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(chip);

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith(
        "/interview/sessions/1/answerable-areas",
        { areas: ["implementation"] },
      );
    });
  });

  test("deselecting an already-selected area sends it removed from the list", async () => {
    mockApi.put.mockResolvedValue({ answerable_areas: [] });

    const { AnswerableAreasControl } = await import(
      "@/components/system-understanding/answerable-areas"
    );
    render(
      <AnswerableAreasControl sessionId={1} answerableAreas={["security"]} />,
      { wrapper: createWrapper() },
    );

    const chip = screen.getByTestId("answerable-area-chip-security");
    expect(chip).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(chip);

    await waitFor(() => {
      expect(mockApi.put).toHaveBeenCalledWith(
        "/interview/sessions/1/answerable-areas",
        { areas: [] },
      );
    });
  });

  test("tolerates an undefined answerableAreas (defensive default)", async () => {
    const { AnswerableAreasControl } = await import(
      "@/components/system-understanding/answerable-areas"
    );
    render(
      <AnswerableAreasControl sessionId={1} answerableAreas={undefined} />,
      { wrapper: createWrapper() },
    );
    expect(screen.getByTestId("answerable-area-chip-security")).toHaveAttribute("aria-pressed", "false");
  });
});

describe("isOutOfArea", () => {
  test("empty selection means no filtering", async () => {
    const { isOutOfArea } = await import("@/components/system-understanding/answerable-areas");
    expect(isOutOfArea("security", [])).toBe(false);
  });

  test("null knowledge_area is never out of area", async () => {
    const { isOutOfArea } = await import("@/components/system-understanding/answerable-areas");
    expect(isOutOfArea(null, ["security"])).toBe(false);
  });

  test("a non-selected area is out of area", async () => {
    const { isOutOfArea } = await import("@/components/system-understanding/answerable-areas");
    expect(isOutOfArea("security", ["implementation"])).toBe(true);
  });

  test("a selected area is not out of area", async () => {
    const { isOutOfArea } = await import("@/components/system-understanding/answerable-areas");
    expect(isOutOfArea("implementation", ["implementation", "security"])).toBe(false);
  });
});

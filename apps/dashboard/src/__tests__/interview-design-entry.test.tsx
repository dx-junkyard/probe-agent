import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { expect, it, vi } from "vitest";
import { InterviewContributions } from "@/components/interview-discussion";
import { OPEN_ASSISTANT_EVENT, type OpenAssistantDetail } from "@/lib/assistant-control";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/api/client", () => ({ api, getSystemId: () => 1, ApiError: class extends Error {} }));
vi.mock("@/api/hooks", () => ({ sysKey: (key: string) => [key, 1] }));

it("prefills the lineage consultation in the originating session without submitting", async () => {
  api.get.mockImplementation(async (path: string) => path.includes("capabilities")
    ? { enabled: true } : { items: [], next_cursor: null });
  const received: OpenAssistantDetail[] = [];
  const listener = (event: Event) => received.push((event as CustomEvent<OpenAssistantDetail>).detail);
  window.addEventListener(OPEN_ASSISTANT_EVENT, listener);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(<QueryClientProvider client={client}><InterviewContributions sessionId={42} /></QueryClientProvider>);
  try {
    fireEvent.click(await screen.findByRole("button", { name: "Vision・UX・機能のつながりを相談する" }));
    expect(received).toHaveLength(1);
    expect(received[0].interviewSessionId).toBe(42);
    expect(received[0].question).toContain("つながりが未確認の箇所を一つずつ質問");
    expect(api.post).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener(OPEN_ASSISTANT_EVENT, listener);
    view.unmount();
    client.clear();
  }
});

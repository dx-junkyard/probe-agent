import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, expect, it, vi } from "vitest";
import { InterviewDiscussionReview } from "@/components/interview-discussion";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }));
vi.mock("@/api/client", () => ({ api, getSystemId: () => 1, ApiError: class extends Error {} }));
vi.mock("@/api/hooks", () => ({ sysKey: (key: string) => [key, 1] }));

const target = { session_id: 42, target_kind: "session" };
let freshness = "current";
let selection = "selected";

beforeEach(() => {
  freshness = "current";
  selection = "selected";
  api.get.mockReset(); api.post.mockReset();
  api.get.mockImplementation(async (path: string) => {
    if (path.includes("capabilities")) return { enabled: true };
    if (path === "/interview/sessions") return [{ id: 42, title: "受注業務", status: "open" }, { id: 73, title: "請求運用", status: "closed" }];
    if (path.includes("discussion-targets")) return { targets: [] };
    return { items: [{ id: 1, revision: 1, kind: "supplement", text: "受注の補足", decision: "pending", sources: [], targets: [{ target, title: "セッション全体", session_title: "受注業務", selection, freshness, reason: "同じ業務" }] }], next_cursor: null };
  });
  api.post.mockResolvedValue({});
});

function show() {
  const onOpenSaved = vi.fn();
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><InterviewDiscussionReview threadId={9} sessionId={42} throughTurn={2} onOpenSaved={onOpenSaved} /></MemoryRouter></QueryClientProvider>);
  return onOpenSaved;
}

it("explains a stale target beside the disabled save selection and lists sessions by name/status", async () => {
  freshness = "stale";
  show();
  const save = await screen.findByLabelText("この補足を保存する");
  expect(save).toBeDisabled();
  expect(save).toHaveAccessibleDescription(/参照先が更新されています/);
  fireEvent.click(screen.getByText(/補足 · 受注の補足/));
  const picker = await screen.findByLabelText("関連先のインタビュー");
  await waitFor(() => expect(picker).toHaveTextContent("請求運用 · 終了済み（保存には再開が必要）"));
  fireEvent.change(screen.getByLabelText("関連先を名前・番号で検索"), { target: { value: "請求" } });
  fireEvent.change(picker, { target: { value: "73" } });
  await waitFor(() => expect(api.get).toHaveBeenCalledWith("/interview/sessions/73/discussion-targets"));
});

it("explains an unselected destination without issuing a save", async () => {
  selection = "candidate";
  show();
  const save = await screen.findByLabelText("この補足を保存する");
  expect(save).toBeDisabled();
  expect(save).toHaveAccessibleDescription(/保存先が未選択です/);
  expect(api.post).not.toHaveBeenCalled();
});

it("links saved supplements to the selected Interview without applying or promoting", async () => {
  const onOpenSaved = show();
  fireEvent.click(await screen.findByLabelText("この補足を保存する"));
  fireEvent.click(screen.getByRole("button", { name: "選択した補足を保存" }));
  const link = await screen.findByRole("link", { name: "受注業務の補足を確認し、既存内容への反映へ進む" });
  expect(link).toHaveAttribute("href", "/interview?session=42#discussion-contributions");
  expect(screen.getByText(/既存内容への反映・正準理解への昇格はまだ行っていません/)).toBeInTheDocument();
  expect(api.post).toHaveBeenCalledTimes(1);
  expect(api.post).toHaveBeenCalledWith("/assistant/discussion-threads/9/interview-contributions", expect.objectContaining({ items: [{ candidate_id: 1, expected_revision: 1 }] }));
  fireEvent.click(link);
  expect(onOpenSaved).toHaveBeenCalledTimes(1);
});

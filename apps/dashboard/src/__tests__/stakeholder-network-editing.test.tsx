/// <reference types="vitest/globals" />
// UX audit 2026-11-17 C1: the Stakeholder Value Network screen can create,
// revise and decide Stakeholder / Need / Observation / Value Exchange, and
// the Journey Service Blueprint can link a Step to them.
//
// A small in-memory fake of `/stakeholder-network/*` backs the page so the
// acceptance path ("from an empty System, two Stakeholders and one Exchange
// between them, confirmed, visible on the graph and in the Blueprint") runs
// end to end through the real hooks. The fake never decides more than the
// real server would: design_status comes only from recorded decisions.

import { render, screen, fireEvent, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";
import type { ReactNode } from "react";
import type {
  BlueprintLaneCellOut, BlueprintOut, StakeholderDecisionKind, ValueNetworkDesignStatus,
  ValueNetworkEdgeOut, ValueNetworkNodeOut, ValueNetworkStakeholderKind,
} from "@/api/types";
import { allowedDecisions, describeWriteFailure } from "@/components/stakeholder-network/editing";

const mockApi = { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() };

class ApiError extends Error {
  status: number;
  detail: string;
  code?: string;
  constructor(status: number, detail: string, code?: string) {
    super(detail);
    this.status = status;
    this.detail = detail;
    this.code = code;
  }
}

vi.mock("@/api/client", () => ({
  api: mockApi,
  getSystemId: () => 1,
  setSystemId: vi.fn(),
  ApiError,
}));

vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() } }));

function wrapper(initial: string) {
  return function Wrapper({ children }: { children: ReactNode }) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    return (
      <MemoryRouter initialEntries={[initial]}>
        <QueryClientProvider client={qc}>{children}</QueryClientProvider>
      </MemoryRouter>
    );
  };
}

// --- fake server -------------------------------------------------------------

interface FakeStakeholder {
  key: string; display_name: string; kind: ValueNetworkStakeholderKind; description: string;
  context_note: string; revision: number;
}
interface FakeExchange {
  key: string; provider: string; receiver: string; kind: ValueNetworkEdgeOut["exchange_kind"];
  value_statement: string; consideration_state: "present" | "none" | "unknown";
  consideration_kind: ValueNetworkEdgeOut["exchange_kind"]; consideration_statement: string; revision: number;
}

function makeServer() {
  const stakeholders: FakeStakeholder[] = [];
  const exchanges: FakeExchange[] = [];
  const decisions = new Map<string, StakeholderDecisionKind>();
  const posts: { path: string; body: Record<string, unknown> }[] = [];

  function status(kind: string, key: string): ValueNetworkDesignStatus {
    const d = decisions.get(`${kind}|${key}`);
    if (d === "confirm") return "confirmed";
    if (d === "reject") return "rejected";
    if (d === "retire") return "retired";
    return "proposed";
  }
  const digest = (key: string, rev: number) => `d-${key}-${rev}`;

  function node(s: FakeStakeholder): ValueNetworkNodeOut {
    return {
      stakeholder_key: s.key, display_name: s.display_name, stakeholder_kind: s.kind, roles: [],
      design_status: status("stakeholder", s.key), recheck_state: "current",
      authored_by_kind: "developer", evidence_state: "missing",
    };
  }
  function edge(x: FakeExchange): ValueNetworkEdgeOut {
    return {
      exchange_key: x.key, provider_stakeholder_key: x.provider, receiver_stakeholder_key: x.receiver,
      exchange_kind: x.kind, value_statement: x.value_statement,
      consideration: { consideration_state: x.consideration_state, consideration_kind: x.consideration_kind, consideration_statement: x.consideration_statement },
      channel: "", trigger: "", cadence: "unknown", design_status: status("value_exchange", x.key),
      recheck_state: "current", validity_state: "unbounded", evidence_state: "missing", related_refs: [],
    };
  }
  function stakeholderDetail(s: FakeStakeholder) {
    return {
      id: 1, system_id: 1, stakeholder_key: s.key, current_revision_id: s.revision, current_revision_number: s.revision,
      display_name: s.display_name, stakeholder_kind: s.kind, design_status: status("stakeholder", s.key),
      recheck_state: "current", created_by: "dev", created_at: 1, updated_at: 1,
      current_revision: {
        id: s.revision, stakeholder_id: 1, revision_number: s.revision, display_name: s.display_name,
        stakeholder_kind: s.kind, description: s.description, context_note: s.context_note,
        content_digest: digest(s.key, s.revision), authored_by_kind: "developer", decision_method: "manual",
        change_note: "", created_by: "dev", created_at: 1, revision_state: "current", superseded_by_id: null,
      },
      degraded_sections: [], degraded_detail: {},
    };
  }
  function exchangeDetail(x: FakeExchange) {
    return {
      ...edge(x), id: 1, system_id: 1, current_revision_id: x.revision, current_revision_number: x.revision,
      created_at: 1, updated_at: 1,
      current_revision: {
        id: x.revision, exchange_id: 1, revision_number: x.revision, provider_stakeholder_key: x.provider,
        receiver_stakeholder_key: x.receiver, exchange_kind: x.kind, value_statement: x.value_statement,
        consideration_state: x.consideration_state, consideration_kind: x.consideration_kind,
        consideration_statement: x.consideration_statement, channel: "", trigger: "", cadence: "unknown",
        valid_from: null, valid_to: null, content_digest: digest(x.key, x.revision), authored_by_kind: "developer", created_at: 1,
      },
      degraded_sections: [], degraded_detail: {},
    };
  }

  mockApi.get.mockImplementation((path: string) => {
    if (path === "/stakeholder-value-network") {
      return Promise.resolve({
        system_id: 1, generated_at: 1, nodes: stakeholders.map(node), edges: exchanges.map(edge),
        notices: [], degraded_sections: [], degraded_detail: {},
      });
    }
    const sh = path.match(/^\/stakeholder-network\/stakeholders\/(.+)$/);
    if (sh) {
      const s = stakeholders.find((x) => x.key === decodeURIComponent(sh[1]));
      return s ? Promise.resolve(stakeholderDetail(s)) : Promise.reject(new ApiError(404, "nf", "stakeholder_not_found"));
    }
    const ex = path.match(/^\/stakeholder-network\/exchanges\/(.+)$/);
    if (ex) {
      const x = exchanges.find((e) => e.key === decodeURIComponent(ex[1]));
      return x ? Promise.resolve(exchangeDetail(x)) : Promise.reject(new ApiError(404, "nf", "stakeholder_not_found"));
    }
    if (path === "/stakeholder-network/needs") {
      return Promise.resolve({ system_id: 1, generated_at: 1, needs: [], degraded_sections: [], degraded_detail: {} });
    }
    if (path === "/stakeholder-network/observations") {
      return Promise.resolve({ system_id: 1, generated_at: 1, observations: [], degraded_sections: [], degraded_detail: {} });
    }
    return new Promise(() => {});
  });

  mockApi.post.mockImplementation((path: string, body: Record<string, unknown>) => {
    posts.push({ path, body });
    if (path === "/stakeholder-network/stakeholders") {
      if (stakeholders.some((s) => s.key === body.stakeholder_key)) {
        return Promise.reject(new ApiError(409, "同じ key が既にこの System に存在します。", "stakeholder_key_conflict"));
      }
      const s: FakeStakeholder = {
        key: String(body.stakeholder_key), display_name: String(body.display_name),
        kind: body.stakeholder_kind as ValueNetworkStakeholderKind, description: String(body.description),
        context_note: String(body.context_note), revision: 1,
      };
      stakeholders.push(s);
      return Promise.resolve(stakeholderDetail(s));
    }
    const rev = path.match(/^\/stakeholder-network\/stakeholders\/(.+)\/revisions$/);
    if (rev) {
      const s = stakeholders.find((x) => x.key === decodeURIComponent(rev[1]))!;
      s.display_name = String(body.display_name);
      s.description = String(body.description);
      s.revision += 1;
      return Promise.resolve(stakeholderDetail(s));
    }
    if (path === "/stakeholder-network/exchanges") {
      const x: FakeExchange = {
        key: String(body.exchange_key), provider: String(body.provider_stakeholder_key),
        receiver: String(body.receiver_stakeholder_key), kind: body.exchange_kind as FakeExchange["kind"],
        value_statement: String(body.value_statement),
        consideration_state: body.consideration_state as FakeExchange["consideration_state"],
        consideration_kind: (body.consideration_kind ?? null) as FakeExchange["consideration_kind"],
        consideration_statement: String(body.consideration_statement), revision: 1,
      };
      exchanges.push(x);
      return Promise.resolve(exchangeDetail(x));
    }
    if (path === "/stakeholder-network/decisions") {
      decisions.set(`${body.subject_kind}|${body.subject_key}`, body.decision as StakeholderDecisionKind);
      return Promise.resolve({ id: 1, subject_kind: body.subject_kind, subject_key: body.subject_key, decision: body.decision, created_at: 1 });
    }
    return Promise.reject(new Error(`unexpected POST ${path}`));
  });

  return { stakeholders, exchanges, decisions, posts };
}

async function renderNetwork(initial = "/stakeholder-value-network") {
  const { default: Page } = await import("@/pages/stakeholder-value-network");
  return render(<Page />, { wrapper: wrapper(initial) });
}

function createStakeholder(key: string, name: string) {
  const form = screen.getByTestId("stakeholder-create-form");
  fireEvent.change(within(form).getByLabelText(/^key/), { target: { value: key } });
  fireEvent.change(within(form).getByLabelText("表示名"), { target: { value: name } });
  fireEvent.click(within(form).getByRole("button", { name: "Stakeholder を追加" }));
}

beforeEach(() => {
  vi.clearAllMocks();
});

// --- acceptance --------------------------------------------------------------

describe("C1: 空の System から画面だけで作って確定する", () => {
  it("Stakeholder 2 件と Value Exchange 1 件を作り、確定すると図と一覧に出る", async () => {
    const server = makeServer();
    await renderNetwork();

    // 空状態は作成フォームの位置を指す。
    const empty = await screen.findByTestId("value-network-no-data");
    expect(empty).toHaveTextContent("Stakeholder を追加");
    expect(screen.getByTestId("exchange-create-needs-stakeholder")).toBeInTheDocument();

    createStakeholder("end-user", "利用者");
    // 作成直後に選択され、詳細ペインに出る。
    await waitFor(() =>
      expect(within(screen.getByTestId("value-network-node-detail")).getByRole("heading", { name: "利用者" })).toBeInTheDocument());

    createStakeholder("operator", "運営者");
    await waitFor(() =>
      expect(within(screen.getByTestId("value-network-node-detail")).getByRole("heading", { name: "運営者" })).toBeInTheDocument());

    // 作成要求に actor / decision_method / authored_by_kind を載せない。
    for (const p of server.posts) {
      expect(p.body).not.toHaveProperty("created_by");
      expect(p.body).not.toHaveProperty("decided_by");
      expect(p.body).not.toHaveProperty("decision_method");
      expect(p.body).not.toHaveProperty("authored_by_kind");
    }

    // provider / receiver は既存の Stakeholder から選ぶ(手入力欄は無い)。
    const form = screen.getByTestId("exchange-create-form");
    fireEvent.change(within(form).getByLabelText(/^key/), { target: { value: "provide-service" } });
    const provider = within(form).getByLabelText(/^提供する側/) as HTMLSelectElement;
    expect(provider.tagName).toBe("SELECT");
    expect(Array.from(provider.options).map((o) => o.value)).toEqual(["", "end-user", "operator"]);
    fireEvent.change(provider, { target: { value: "operator" } });
    fireEvent.change(within(form).getByLabelText(/^受け取る側/), { target: { value: "end-user" } });
    fireEvent.change(within(form).getByLabelText("渡すものの種類"), { target: { value: "service" } });
    fireEvent.change(within(form).getByLabelText(/^何を渡すか/), { target: { value: "予約の代行" } });
    fireEvent.change(within(form).getByLabelText("対価の有無"), { target: { value: "present" } });
    fireEvent.change(within(form).getByLabelText(/^対価の種類/), { target: { value: "money" } });
    fireEvent.change(within(form).getByLabelText(/^対価の記述/), { target: { value: "月額利用料" } });
    fireEvent.click(within(form).getByRole("button", { name: "Value Exchange を追加" }));

    await waitFor(() => expect(screen.getByTestId("value-network-edge-detail")).toBeInTheDocument());
    const exchangePost = server.posts.find((p) => p.path === "/stakeholder-network/exchanges")!;
    expect(exchangePost.body).toMatchObject({
      exchange_key: "provide-service", provider_stakeholder_key: "operator", receiver_stakeholder_key: "end-user",
      exchange_kind: "service", value_statement: "予約の代行", consideration_state: "present",
      consideration_kind: "money", consideration_statement: "月額利用料",
    });
    // 金額を合計する列はどこにも無い。
    expect(Object.keys(exchangePost.body).some((k) => /amount|total|sum/.test(k))).toBe(false);

    // 図に出る。
    expect(screen.getByTestId("value-network-graph-edge-provide-service")).toBeInTheDocument();

    // 確定は確認ダイアログを通し、表示中の digest を添えて送る。
    fireEvent.click(await screen.findByTestId("exchange-decision-confirm"));
    const dialog = await screen.findByTestId("exchange-decision-dialog");
    fireEvent.click(within(dialog).getByTestId("exchange-decision-submit"));
    await waitFor(() => expect(server.decisions.get("value_exchange|provide-service")).toBe("confirm"));
    const decisionPost = server.posts.find((p) => p.path === "/stakeholder-network/decisions")!;
    expect(decisionPost.body).toEqual({
      subject_kind: "value_exchange", subject_key: "provide-service", decision: "confirm",
      rationale: "", captured_digest: "d-provide-service-1",
    });

    // サーバーが返した状態をそのまま表示する。
    await waitFor(() =>
      expect(within(screen.getByTestId("value-network-edge-provide-service")).getByText("確定")).toBeInTheDocument());
  });
});

// --- rejections, decisions, remount -------------------------------------------

describe("C1: 拒否・決定・切り替え", () => {
  it("409 の key 重複はフォームの近くに日本語で出し、コードは補足に留める", async () => {
    const server = makeServer();
    server.stakeholders.push({ key: "end-user", display_name: "利用者", kind: "end_user", description: "", context_note: "", revision: 1 });
    await renderNetwork();
    await screen.findByTestId("value-network-node-end-user");
    createStakeholder("end-user", "別の利用者");
    const error = await screen.findByTestId("stakeholder-create-form-error");
    expect(error).toHaveTextContent("同じ key がこの System に既にあります。");
    expect(error).toHaveTextContent("コード: stakeholder_key_conflict");
    // 入力は残る。
    expect(within(screen.getByTestId("stakeholder-create-form")).getByLabelText(/^key/)).toHaveValue("end-user");
  });

  it("却下済みの Stakeholder には「提案に戻す」だけを出す", async () => {
    const server = makeServer();
    server.stakeholders.push({ key: "end-user", display_name: "利用者", kind: "end_user", description: "", context_note: "", revision: 1 });
    server.decisions.set("stakeholder|end-user", "reject");
    await renderNetwork("/stakeholder-value-network?node=end-user");
    const controls = await screen.findByTestId("stakeholder-decision");
    expect(within(controls).getByTestId("stakeholder-decision-reinstate")).toBeInTheDocument();
    expect(within(controls).queryByTestId("stakeholder-decision-confirm")).not.toBeInTheDocument();
    expect(within(controls).queryByTestId("stakeholder-decision-reject")).not.toBeInTheDocument();
  });

  it("revision フォームは現在の版から seed され、別の Stakeholder へ入力を持ち越さない", async () => {
    const server = makeServer();
    server.stakeholders.push(
      { key: "a", display_name: "A さん", kind: "end_user", description: "A の説明", context_note: "", revision: 1 },
      { key: "b", display_name: "B さん", kind: "partner", description: "B の説明", context_note: "", revision: 1 },
    );
    await renderNetwork("/stakeholder-value-network?node=a");
    const formA = await screen.findByTestId("stakeholder-revision-form");
    expect(within(formA).getByLabelText("表示名")).toHaveValue("A さん");
    expect(within(formA).getByLabelText("説明")).toHaveValue("A の説明");
    fireEvent.change(within(formA).getByLabelText("説明"), { target: { value: "A への未保存の入力" } });

    fireEvent.click(screen.getByTestId("value-network-node-b"));
    await waitFor(() =>
      expect(within(screen.getByTestId("stakeholder-revision-form")).getByLabelText("表示名")).toHaveValue("B さん"));
    expect(within(screen.getByTestId("stakeholder-revision-form")).getByLabelText("説明")).toHaveValue("B の説明");
  });

  it("revision を保存すると新しい版の内容で seed し直す", async () => {
    const server = makeServer();
    server.stakeholders.push({ key: "a", display_name: "A さん", kind: "end_user", description: "旧", context_note: "", revision: 1 });
    await renderNetwork("/stakeholder-value-network?node=a");
    const form = await screen.findByTestId("stakeholder-revision-form");
    fireEvent.change(within(form).getByLabelText("説明"), { target: { value: "新" } });
    fireEvent.click(within(form).getByRole("button", { name: "新しい版として保存" }));
    await waitFor(() => expect(server.stakeholders[0].revision).toBe(2));
    const post = server.posts.find((p) => p.path === "/stakeholder-network/stakeholders/a/revisions")!;
    // 1 項目だけ直しても他の項目は現在の版の値のまま送る。
    expect(post.body).toMatchObject({ display_name: "A さん", stakeholder_kind: "end_user", description: "新" });
    await waitFor(() =>
      expect(within(screen.getByTestId("stakeholder-revision-form")).getByRole("button", { name: "新しい版として保存" })).toBeDisabled());
  });
});

describe("editing.ts", () => {
  it("allowedDecisions mirrors the server's finite transition rule", () => {
    expect(allowedDecisions("proposed", "current")).toEqual({ primary: "confirm", secondary: ["reject"] });
    expect(allowedDecisions("confirmed", "current").primary).toBeNull();
    expect(allowedDecisions("confirmed", "stale").primary).toBe("confirm");
    expect(allowedDecisions("rejected", "current")).toEqual({ primary: "reinstate", secondary: [] });
    expect(allowedDecisions("retired", "stale")).toEqual({ primary: "reinstate", secondary: [] });
  });

  it("describeWriteFailure prefers the finite code's Japanese copy and keeps the code as a supplement", () => {
    expect(describeWriteFailure(new ApiError(422, "x", "exchange_self_loop"))).toEqual({
      message: "提供する側と受け取る側が同じ場合、種類は「情報」だけを選べます。", code: "exchange_self_loop",
    });
    expect(describeWriteFailure(new ApiError(422, "x")).message).toContain("入力内容を受け付けられませんでした");
  });
});

// --- Journey Service Blueprint -------------------------------------------------

function laneCell(lane_kind: BlueprintLaneCellOut["lane_kind"], overrides: Partial<BlueprintLaneCellOut> = {}): BlueprintLaneCellOut {
  return {
    lane_kind, state: "unknown", summary: "", stakeholder_links: [], delivery_links: [], exchange_links: [],
    requirement_refs: [], evidence_refs: [], ...overrides,
  };
}

const LANES = [
  "stakeholder_action", "touchpoint", "frontstage", "backstage", "support", "external",
  "requirement", "evidence", "failure_recovery",
] as const;

describe("C1: Journey Step の関連付け", () => {
  it("Step の Stakeholder / Value Exchange を既存の一覧から選んで関連付け、Blueprint の列に出る", async () => {
    const stakeholderLinks: BlueprintLaneCellOut["stakeholder_links"] = [];
    const exchangeLinks: BlueprintLaneCellOut["exchange_links"] = [];
    const posts: { path: string; body: Record<string, unknown> }[] = [];
    function blueprint(): BlueprintOut {
      const lanes: Record<string, BlueprintLaneCellOut> = {};
      for (const k of LANES) lanes[k] = laneCell(k);
      lanes.stakeholder_action = laneCell("stakeholder_action", {
        state: stakeholderLinks.length ? "present" : "unknown", stakeholder_links: [...stakeholderLinks],
      });
      lanes.touchpoint = laneCell("touchpoint", {
        state: exchangeLinks.length ? "present" : "unknown", exchange_links: [...exchangeLinks],
      });
      return {
        journey_key: "j1", perspective: "to_be", baseline_state: "absent", current_revision_number: 1,
        steps: [{ step_key: "s1", step_order: 0, user_intent: "", system_response: "", lanes }],
        degraded_sections: [], degraded_detail: {},
      };
    }
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/ux-design/journeys") {
        return Promise.resolve({
          system_id: 1, generated_at: 1, degraded_sections: [], degraded_detail: {},
          journeys: [{
            id: 1, system_id: 1, journey_key: "j1", perspective: "to_be", baseline_mode: "undecided",
            baseline_journey_id: null, baseline_journey_key: null, baseline_state: "absent",
            current_revision_id: 1, current_revision_number: 1, title: "J1", design_status: "proposed",
            recheck_state: "current", created_by: "dev", created_at: 1, updated_at: 1,
          }],
        });
      }
      if (path === "/journey-blueprint?journey_key=j1") return Promise.resolve(blueprint());
      if (path === "/stakeholder-value-network") {
        return Promise.resolve({
          system_id: 1, generated_at: 1, notices: [], degraded_sections: [], degraded_detail: {},
          nodes: [{
            stakeholder_key: "end-user", display_name: "利用者", stakeholder_kind: "end_user", roles: [],
            design_status: "confirmed", recheck_state: "current", authored_by_kind: "developer", evidence_state: "missing",
          }],
          edges: [{
            exchange_key: "provide-service", provider_stakeholder_key: "operator", receiver_stakeholder_key: "end-user",
            exchange_kind: "service", value_statement: "予約の代行",
            consideration: { consideration_state: "unknown", consideration_kind: null, consideration_statement: "" },
            channel: "", trigger: "", cadence: "unknown", design_status: "confirmed", recheck_state: "current",
            validity_state: "unbounded", evidence_state: "missing", related_refs: [],
          }],
        });
      }
      if (path === "/ux-design/requirements") {
        return Promise.resolve({ system_id: 1, generated_at: 1, requirements: [], degraded_sections: [], degraded_detail: {} });
      }
      return new Promise(() => {});
    });
    mockApi.post.mockImplementation((path: string, body: Record<string, unknown>) => {
      posts.push({ path, body });
      const base = {
        id: posts.length, journey_id: 1, journey_key: "j1", step_key: "s1", step_label: null,
        target_resolution: "resolved" as const, recheck_state: "current" as const, note: "",
        decision_method: "manual", created_by: "dev", created_at: 1, superseded_by_id: null,
      };
      if (path === "/journey-blueprint/stakeholder-links") {
        const link = { ...base, stakeholder_key: String(body.stakeholder_key), stakeholder_name: "利用者", role: body.role as "actor" };
        stakeholderLinks.push(link);
        return Promise.resolve(link);
      }
      if (path === "/journey-blueprint/exchange-links") {
        const link = { ...base, exchange_key: String(body.exchange_key), exchange_kind: "service", channel: null };
        exchangeLinks.push(link);
        return Promise.resolve(link);
      }
      return Promise.reject(new ApiError(404, "nf", "journey_step_not_found"));
    });

    const { default: Page } = await import("@/pages/journey-blueprint");
    render(<Page />, { wrapper: wrapper("/journey-blueprint?journey=j1") });

    fireEvent.click(await screen.findByTestId("blueprint-cell-stakeholder_action"));
    const shForm = await screen.findByTestId("blueprint-link-stakeholder-form");
    const select = within(shForm).getByLabelText(/^Stakeholder/) as HTMLSelectElement;
    await waitFor(() => expect(Array.from(select.options).map((o) => o.value)).toEqual(["", "end-user"]));
    fireEvent.change(select, { target: { value: "end-user" } });
    fireEvent.change(within(shForm).getByLabelText("この Step での役割"), { target: { value: "beneficiary" } });
    fireEvent.click(within(shForm).getByRole("button", { name: "関連付ける" }));
    await waitFor(() => expect(screen.getAllByTestId("blueprint-detail-stakeholder-link")).toHaveLength(1));
    expect(posts[0]).toEqual({
      path: "/journey-blueprint/stakeholder-links",
      body: { journey_key: "j1", step_key: "s1", stakeholder_key: "end-user", role: "beneficiary", note: "" },
    });
    expect(screen.getByTestId("blueprint-cell-state-stakeholder_action")).toHaveTextContent(/./);

    // 接点レーンでは Value Exchange のフォームが先頭に出る。
    fireEvent.click(screen.getByTestId("blueprint-cell-touchpoint"));
    const exForm = await screen.findByTestId("blueprint-link-exchange-form");
    const exSelect = within(exForm).getByLabelText(/^Value Exchange/) as HTMLSelectElement;
    await waitFor(() => expect(exSelect.options.length).toBe(2));
    fireEvent.change(exSelect, { target: { value: "provide-service" } });
    fireEvent.click(within(exForm).getByRole("button", { name: "関連付ける" }));
    await waitFor(() => expect(screen.getAllByTestId("blueprint-detail-exchange-link")).toHaveLength(1));
    expect(posts[1].body).toEqual({ journey_key: "j1", step_key: "s1", exchange_key: "provide-service", note: "" });
  });

  it("関連付けの拒否はフォームの近くに日本語で出す", async () => {
    mockApi.get.mockImplementation((path: string) => {
      if (path === "/ux-design/journeys") {
        return Promise.resolve({
          system_id: 1, generated_at: 1, degraded_sections: [], degraded_detail: {},
          journeys: [{
            id: 1, system_id: 1, journey_key: "j1", perspective: "to_be", baseline_mode: "undecided",
            baseline_journey_id: null, baseline_journey_key: null, baseline_state: "absent",
            current_revision_id: 1, current_revision_number: 1, title: "J1", design_status: "proposed",
            recheck_state: "current", created_by: "dev", created_at: 1, updated_at: 1,
          }],
        });
      }
      if (path === "/journey-blueprint?journey_key=j1") {
        const lanes: Record<string, BlueprintLaneCellOut> = {};
        for (const k of LANES) lanes[k] = laneCell(k);
        return Promise.resolve({
          journey_key: "j1", perspective: "to_be", baseline_state: "absent", current_revision_number: 1,
          steps: [{ step_key: "s1", step_order: 0, user_intent: "", system_response: "", lanes }],
          degraded_sections: [], degraded_detail: {},
        });
      }
      if (path === "/stakeholder-value-network") {
        return Promise.resolve({
          system_id: 1, generated_at: 1, notices: [], degraded_sections: [], degraded_detail: {}, edges: [],
          nodes: [{
            stakeholder_key: "end-user", display_name: "利用者", stakeholder_kind: "end_user", roles: [],
            design_status: "confirmed", recheck_state: "current", authored_by_kind: "developer", evidence_state: "missing",
          }],
        });
      }
      return new Promise(() => {});
    });
    mockApi.post.mockRejectedValue(new ApiError(404, "指定された Journey Step が見つかりません。", "journey_step_not_found"));

    const { default: Page } = await import("@/pages/journey-blueprint");
    render(<Page />, { wrapper: wrapper("/journey-blueprint?journey=j1") });
    fireEvent.click(await screen.findByTestId("blueprint-cell-stakeholder_action"));
    const form = await screen.findByTestId("blueprint-link-stakeholder-form");
    const select = within(form).getByLabelText(/^Stakeholder/);
    await waitFor(() => expect((select as HTMLSelectElement).options.length).toBe(2));
    fireEvent.change(select, { target: { value: "end-user" } });
    fireEvent.click(within(form).getByRole("button", { name: "関連付ける" }));
    const error = await screen.findByTestId("blueprint-link-stakeholder-error");
    expect(error).toHaveTextContent("指定した Journey Step が見つかりません。");
    expect(error).toHaveTextContent("コード: journey_step_not_found");
  });
});

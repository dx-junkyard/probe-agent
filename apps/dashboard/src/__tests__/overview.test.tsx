/// <reference types="vitest/globals" />
// Issues #380-#384: the Overview decision cockpit.
//
// Everything semantic is decided by `GET /overview`, so these tests assert the
// one thing the Dashboard is actually responsible for: rendering the server's
// conclusions without losing them, without conflating them, and without
// quietly substituting a client-side judgement.
//
// The display components take props only, so they need no API mock. The page
// itself is tested through the mocked `api.get`, because its degraded /
// loading / zero-System branches are the states #384 asks for.

import { render, screen, fireEvent, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { SystemBriefCard } from "@/components/overview/system-brief";
import { FindingsCard } from "@/components/overview/findings";
import { LoopRailCard, NextActionCard } from "@/components/overview/next-action";
import { RuntimeHealthCard } from "@/components/overview/runtime-health";
import { targetHref } from "@/components/overview/display";
import type {
  OverviewFindingOut,
  OverviewOut,
  UnderstandingBriefClaimOut,
  UnderstandingBriefOut,
} from "@/api/types";

function wrap(ui: React.ReactElement) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

function claim(
  overrides: Partial<UnderstandingBriefClaimOut> & { name: string },
): UnderstandingBriefClaimOut {
  return {
    kind: "core_capability",
    summary: "",
    confirmation: "ai_hypothesis",
    provenance: "ai_hypothesis",
    confirmation_label: "AI 仮説・未確認",
    provenance_label: "AI の解釈・仮説",
    reason: "",
    contribution: "",
    is_mock: false,
    evidence: [],
    related_docs: [],
    related_apis: [],
    ...overrides,
  };
}

function brief(overrides: Partial<UnderstandingBriefOut> = {}): UnderstandingBriefOut {
  return {
    system_id: 1,
    session_id: 7,
    built: true,
    vision: null,
    vision_missing_information: [],
    system_purpose: [],
    core_capabilities: [],
    core_capability_initial_count: 0,
    key_unconfirmed: [],
    detail_counts: {},
    readiness_state: "needs_confirmation",
    readiness_label: "確認が必要です",
    readiness_description: "判断の対象はありますが、あなたの確認が必要な内容が残っています。",
    readiness_reasons: [],
    changes_since_confirmation: [],
    confirmed_at: null,
    confirmed_revision_id: null,
    revision_id: 3,
    snapshot_id: 42,
    ...overrides,
  };
}

function finding(overrides: Partial<OverviewFindingOut> & { id: string }): OverviewFindingOut {
  return {
    kind: "understanding_changed",
    kind_label: "理解の変化",
    severity: "material_change",
    severity_label: "判断の前提が変わりました",
    status: "new",
    status_label: "前回の確認より後に発生",
    summary: "主要機能が 2 件変わりました。",
    decision_impact: "前回の確定をそのまま再利用できません。",
    provenance: "implementation_fact",
    provenance_label: "コード・ドキュメントの実装事実",
    snapshot_id: 42,
    revision_id: 3,
    runtime_window_seconds: null,
    first_seen: 200,
    last_updated: 200,
    target: null,
    evidence: [],
    occurrence_count: 1,
    ...overrides,
  };
}

function overview(overrides: Partial<OverviewOut> = {}): OverviewOut {
  return {
    system_id: 1,
    generated_at: 1000,
    interview_session_id: 7,
    brief: brief(),
    snapshot_id: 42,
    snapshot_commit_sha: "abcdef1234",
    latest_ready_snapshot_id: 42,
    snapshot_freshness: "current",
    understanding_revision_id: 3,
    understanding_confirmed_at: 100,
    findings: [],
    findings_initial_count: 0,
    findings_state: "no_findings",
    findings_baseline_state: "has_baseline",
    findings_baseline_label: "前回あなたがシステム理解を確認した時点",
    findings_baseline_at: 100,
    next_action: null,
    next_action_state: "waiting",
    next_action_message: "システムが理解を作成・更新しています。",
    loop_stages: [],
    user_phase: "preparation",
    runtime: null,
    degraded_sections: [],
    degraded_detail: {},
    ...overrides,
  };
}

// ── #381: System Brief ────────────────────────────────────────────────

describe("System Brief (Issue #381)", () => {
  test("separates Vision from System Purpose and shows both axes per claim", () => {
    wrap(
      <SystemBriefCard
        interviewHref="/interview?session=7"
        overview={overview({
          brief: brief({
            vision: claim({
              kind: "vision",
              name: "開発者が自分のシステムを説明できる状態にする",
              confirmation: "confirmed",
              confirmation_label: "確認済み",
              provenance: "developer_intent",
              provenance_label: "開発者が明示した意図",
            }),
            system_purpose: [
              claim({
                kind: "system_purpose",
                name: "実行時の挙動を根拠として提示する",
                provenance: "implementation_fact",
                provenance_label: "コード・ドキュメントの実装事実",
              }),
            ],
          }),
        })}
      />,
    );

    const vision = screen.getByTestId("overview-vision");
    const purpose = screen.getByTestId("overview-system-purpose");
    expect(within(vision).getByText(/誰の状態をどう変えたいか/)).toBeInTheDocument();
    expect(within(purpose).getByText(/システムが担う役割/)).toBeInTheDocument();

    // 確認状態 と 出所 は独立した二軸。AI が書いた文を人が承認しても
    // 「開発者が書いた」にはならない。
    const visionClaim = screen.getByTestId("overview-vision-claim");
    expect(within(visionClaim).getByText("確認済み")).toBeInTheDocument();
    expect(within(visionClaim).getByText("開発者が明示した意図")).toBeInTheDocument();
    const purposeClaim = screen.getByTestId("overview-purpose-claim");
    expect(within(purposeClaim).getByText("AI 仮説・未確認")).toBeInTheDocument();
    expect(within(purposeClaim).getByText("コード・ドキュメントの実装事実")).toBeInTheDocument();
  });

  test("a missing Vision names what is missing instead of inventing one", () => {
    wrap(
      <SystemBriefCard
        interviewHref="/interview"
        overview={overview({
          brief: brief({
            vision: null,
            vision_missing_information: ["このシステムで実現したい状態・価値"],
          }),
        })}
      />,
    );
    const missing = screen.getByTestId("overview-vision-missing");
    expect(within(missing).getByText(/まだ分かっていません/)).toBeInTheDocument();
    expect(
      within(missing).getByText("このシステムで実現したい状態・価値"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("overview-vision-claim")).not.toBeInTheDocument();
  });

  test("readiness renders the server's verdict and reasons in the same area", () => {
    wrap(
      <SystemBriefCard
        interviewHref="/interview"
        overview={overview({
          brief: brief({
            readiness_state: "blocked",
            readiness_label: "この理解では進めません",
            readiness_description: "矛盾により信頼できません。",
            readiness_reasons: [
              {
                code: "claim_conflict",
                severity: "blocking",
                message: "「要約」の解釈に矛盾があります。",
                target_kind: "claim",
                target_name: "要約",
              },
            ],
          }),
        })}
      />,
    );
    const badge = screen.getByTestId("overview-readiness-badge");
    expect(badge).toHaveAttribute("data-readiness", "blocked");
    expect(badge).toHaveTextContent("この理解では進めません");
    expect(screen.getByTestId("overview-readiness-description")).toHaveTextContent(
      "矛盾により信頼できません。",
    );
    expect(
      within(screen.getByTestId("overview-readiness-reasons")).getByText(
        "「要約」の解釈に矛盾があります。",
      ),
    ).toBeInTheDocument();
  });

  test("building and partial understanding never render as complete", () => {
    wrap(
      <SystemBriefCard
        interviewHref="/interview"
        overview={overview({
          brief: brief({
            readiness_state: "building",
            readiness_label: "システムが理解を作成しています",
            system_purpose: [],
            core_capabilities: [],
          }),
        })}
      />,
    );
    expect(screen.getByTestId("overview-readiness-badge")).toHaveAttribute(
      "data-readiness",
      "building",
    );
    expect(screen.getByTestId("overview-purpose-missing")).toBeInTheDocument();
    expect(screen.getByTestId("overview-capabilities-missing")).toBeInTheDocument();
  });

  test("core capabilities are capped by the server's initial count, never padded", () => {
    const capabilities = ["A", "B", "C", "D", "E", "F"].map((name) => claim({ name }));
    wrap(
      <SystemBriefCard
        interviewHref="/interview?session=7"
        overview={overview({
          brief: brief({
            core_capabilities: capabilities,
            core_capability_initial_count: 5,
          }),
        })}
      />,
    );
    expect(screen.getAllByTestId("overview-capability-claim")).toHaveLength(5);
    expect(screen.getByTestId("overview-capabilities-more")).toHaveTextContent("残り 1 件");
  });

  test("a degraded Brief says so rather than claiming there is no understanding", () => {
    wrap(
      <SystemBriefCard
        interviewHref="/interview"
        overview={overview({ brief: null, degraded_sections: ["brief"] })}
      />,
    );
    expect(screen.getByTestId("overview-brief-unavailable")).toHaveTextContent(
      "取得できませんでした",
    );
  });
});

// ── #382: 今わかったこと ──────────────────────────────────────────────

describe("Findings (Issue #382)", () => {
  test("shows only the server's initial count and discloses the rest", () => {
    const findings = ["a", "b", "c", "d"].map((id) => finding({ id }));
    wrap(<FindingsCard overview={overview({ findings, findings_initial_count: 3, findings_state: "has_findings" })} />);

    expect(screen.getAllByTestId("overview-finding")).toHaveLength(3);
    fireEvent.click(screen.getByTestId("overview-findings-toggle"));
    expect(screen.getAllByTestId("overview-finding")).toHaveLength(4);
  });

  test("renders the server order verbatim — the client never re-ranks", () => {
    const findings = [
      finding({ id: "low", severity: "informative", severity_label: "参考情報", summary: "S1" }),
      finding({ id: "high", severity: "blocker", severity_label: "判断を止めています", summary: "S2" }),
    ];
    wrap(<FindingsCard overview={overview({ findings, findings_initial_count: 2, findings_state: "has_findings" })} />);
    const rows = screen.getAllByTestId("overview-finding");
    expect(rows[0]).toHaveAttribute("data-finding-severity", "informative");
    expect(rows[1]).toHaveAttribute("data-finding-severity", "blocker");
  });

  test("each finding states its impact, provenance and the revision it was read against", () => {
    wrap(
      <FindingsCard
        overview={overview({
          findings: [finding({ id: "x", target: { route: "/interview", label: "システム理解を開く", params: { session: "7" }, anchor: null } })],
          findings_initial_count: 1,
          findings_state: "has_findings",
        })}
      />,
    );
    const row = screen.getByTestId("overview-finding");
    expect(within(row).getByText(/判断への影響/)).toBeInTheDocument();
    expect(within(row).getByText(/コード・ドキュメントの実装事実/)).toBeInTheDocument();
    expect(within(row).getByText(/理解リビジョン 3/)).toBeInTheDocument();
    expect(within(row).getByTestId("overview-finding-target")).toHaveAttribute(
      "href",
      "/interview?session=7",
    );
  });

  test("the three empty states are three different answers", () => {
    const { unmount } = wrap(
      <FindingsCard overview={overview({ findings: [], findings_state: "no_findings" })} />,
    );
    expect(screen.getByTestId("overview-findings-none")).toHaveTextContent(
      "重要な新しい発見はありません",
    );
    unmount();

    const second = wrap(
      <FindingsCard
        overview={overview({
          findings: [],
          findings_state: "not_compared",
          findings_baseline_at: null,
          findings_baseline_state: "no_baseline",
    findings_baseline_label: "まだ理解を確認していないため、比較の基準がありません",
        })}
      />,
    );
    expect(screen.getByTestId("overview-findings-not-compared")).toHaveTextContent(
      "まだ比較していません",
    );
    second.unmount();

    wrap(<FindingsCard overview={overview({ findings: [], findings_state: "unavailable" })} />);
    const unavailable = screen.getByTestId("overview-findings-unavailable");
    expect(unavailable).toHaveTextContent("取得できませんでした");
    // 「発見がない」との取り違えを明示的に否定する。
    expect(unavailable).toHaveTextContent("という意味ではありません");
  });

  test("status is shown as text, not by colour alone", () => {
    wrap(
      <FindingsCard
        overview={overview({
          findings: [finding({ id: "s", status: "not_compared", status_label: "比較の基準がありません" })],
          findings_initial_count: 1,
          findings_state: "has_findings",
        })}
      />,
    );
    expect(screen.getByText("比較の基準がありません")).toBeInTheDocument();
  });
});

// ── #383: 次にすること / 改善ループ ──────────────────────────────────

describe("Next action (Issue #383)", () => {
  const action = {
    key: "confirm_understanding" as const,
    label: "理解を確認する",
    reason: "AI の読み取りは出ていますが、あなたの確認がまだです。",
    completion_condition: "System Purpose と主要機能を確認済みにすること。",
    value: "以降の判断が確認済みの前提の上で行えます。",
    target: {
      route: "/interview",
      label: "理解を確認する",
      params: { session: "7" },
      anchor: "understanding-brief",
    },
    rule_row: 7,
    source_state_ids: ["understanding.readiness.needs_confirmation"],
    source_finding_ids: ["understanding_changed:abc"],
    blockers: [],
  };

  test("renders exactly one CTA with its reason, completion condition and value", () => {
    wrap(<NextActionCard overview={overview({ next_action: action, next_action_state: "available" })} />);
    const cta = screen.getByTestId("overview-next-action-cta");
    expect(cta).toHaveAttribute("data-action-key", "confirm_understanding");
    expect(cta).toHaveAttribute("href", "/interview?session=7#understanding-brief");
    expect(screen.getAllByTestId("overview-next-action-cta")).toHaveLength(1);
    expect(screen.getByTestId("overview-next-action-reason")).toHaveTextContent(action.reason);
    expect(screen.getByTestId("overview-next-action-completion")).toHaveTextContent(
      action.completion_condition,
    );
    expect(screen.getByTestId("overview-next-action-value")).toHaveTextContent(action.value);
    expect(screen.getByTestId("overview-next-action-sources")).toBeInTheDocument();
  });

  test("waiting and unavailable render as text, never as a disabled control", () => {
    const { unmount } = wrap(
      <NextActionCard
        overview={overview({ next_action: null, next_action_state: "waiting", next_action_message: "作成中です。" })}
      />,
    );
    expect(screen.getByTestId("overview-next-action-waiting")).toHaveTextContent("作成中です。");
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByTestId("overview-next-action-cta")).not.toBeInTheDocument();
    unmount();

    wrap(
      <NextActionCard
        overview={overview({ next_action: null, next_action_state: "unavailable", next_action_message: "" })}
      />,
    );
    expect(screen.getByTestId("overview-next-action-unavailable")).toHaveTextContent(
      "推測での案内は行いません",
    );
  });

  test("the loop rail marks reached / current / future and names the next milestone", () => {
    wrap(
      <LoopRailCard
        overview={overview({
          user_phase: "instrumentation",
          loop_stages: [
            { stage: "setup", label: "Setup", status: "reached", meaning: "m", next_milestone: "", complete: true },
            { stage: "preparation", label: "Understand", status: "reached", meaning: "m", next_milestone: "", complete: true },
            { stage: "instrumentation", label: "Instrument", status: "current", meaning: "計測を組み込みます。", next_milestone: "承認済みの計測経路が 1 本できること。", complete: false },
            { stage: "observation", label: "Observe", status: "future", meaning: "m", next_milestone: "", complete: false },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("overview-loop-stage-setup")).toHaveAttribute(
      "data-stage-status",
      "reached",
    );
    const current = screen.getByTestId("overview-loop-stage-instrumentation");
    expect(current).toHaveAttribute("data-stage-status", "current");
    expect(current).toHaveAttribute("aria-current", "step");
    expect(screen.getByTestId("overview-loop-next-milestone")).toHaveTextContent(
      "承認済みの計測経路が 1 本できること。",
    );
  });

  test("a recheck is not shown as ordinary forward progress", () => {
    wrap(
      <LoopRailCard
        overview={overview({
          brief: brief({ readiness_state: "recheck_required" }),
          loop_stages: [
            { stage: "setup", label: "Setup", status: "current", meaning: "m", next_milestone: "n", complete: false },
          ],
        })}
      />,
    );
    expect(screen.getByTestId("overview-loop-recheck")).toHaveTextContent("再確認が必要");
  });
});

// ── #384: Runtime health ─────────────────────────────────────────────

describe("Runtime health (Issue #384)", () => {
  const runtime = {
    state: "receiving" as const,
    freshness: "stale" as const,
    freshness_label: "受信が止まっています",
    transport_freshness: "receiving_now" as const,
    last_real_trace_at: 1000,
    seconds_since_last_trace: 90000,
    last_trace_at: 2000,
    seconds_since_last_any_trace: 10,
    evaluated_at: 91000,
    real_trace_count_5m: 0,
    real_trace_count_1h: 0,
    real_trace_count_24h: 0,
    delayed_after_seconds: 900,
    stale_after_seconds: 86400,
    component_count: 4,
    total_trace_count: 1234,
    mode_counts: { trace: 3, shadow: 1 },
    window_seconds: 86400,
    error_count: 2,
    runtime_mismatch_count: 1,
    replayable_count: 5,
    partial_count: 1,
    unreplayable_count: 0,
    not_captured_count: 3,
    observed_component_count: 2,
    known_component_count: 7,
    core_capability_count: 4,
    capability_coverage_state: "not_computed" as const,
    observed_capability_count: null,
    unmapped_component_count: null,
  };

  test("the headline is the live workload freshness, not the cumulative milestone", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime })} />);
    const badge = screen.getByTestId("overview-runtime-freshness");
    expect(badge).toHaveAttribute("data-freshness", "stale");
    expect(badge).toHaveTextContent("長時間受信なし");
    // 累積 (state=receiving / 総数) は履歴として畳まれ、見出しにはならない。
    expect(badge).not.toHaveTextContent("受信中");
  });

  test("last reception shows relative time and the absolute timestamp", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime })} />);
    const line = screen.getByTestId("overview-runtime-last-seen");
    expect(line).toHaveTextContent("1日前");
    expect(line.textContent).toMatch(/\(/);
  });

  test("the transport axis appears only when it disagrees with the workload", () => {
    const { unmount } = wrap(<RuntimeHealthCard overview={overview({ runtime })} />);
    expect(screen.getByTestId("overview-runtime-transport")).toBeInTheDocument();
    unmount();

    wrap(
      <RuntimeHealthCard
        overview={overview({
          runtime: { ...runtime, freshness: "receiving_now", transport_freshness: "receiving_now" },
        })}
      />,
    );
    expect(screen.queryByTestId("overview-runtime-transport")).not.toBeInTheDocument();
  });

  test("cumulative totals are disclosed as history, never as the first reading", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime })} />);
    const cumulative = screen.getByTestId("overview-runtime-cumulative");
    expect(cumulative.tagName.toLowerCase()).toBe("details");
    expect(cumulative).not.toHaveAttribute("open");
    expect(within(cumulative).getByText(/現在の稼働状態ではありません/)).toBeInTheDocument();
  });

  test("Component detail is one click away and never duplicated here", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime })} />);
    expect(screen.getByTestId("overview-runtime-components-link")).toHaveAttribute(
      "href",
      "/components",
    );
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  test("a degraded runtime section does not read as 受信が止まっている", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime: null, degraded_sections: ["runtime"] })} />);
    expect(screen.getByTestId("overview-runtime-unavailable")).toHaveTextContent(
      "受信が止まっているという意味ではありません",
    );
  });
});

// ── review follow-ups: provenance / coverage / headings ───────────────

describe("Provenance is rendered as the server reports it (Issue #382)", () => {
  test("a developer-authored claim is not shown as an AI hypothesis", () => {
    wrap(
      <FindingsCard
        overview={overview({
          findings: [
            finding({
              id: "p",
              provenance: "developer_intent",
              provenance_label: "開発者が明示した意図",
            }),
          ],
          findings_initial_count: 1,
          findings_state: "has_findings",
        })}
      />,
    );
    const row = screen.getByTestId("overview-finding");
    expect(within(row).getByText(/開発者が明示した意図/)).toBeInTheDocument();
    expect(within(row).queryByText(/AI の解釈/)).not.toBeInTheDocument();
  });

  test("a mixed-source aggregation says so rather than naming one source", () => {
    wrap(
      <FindingsCard
        overview={overview({
          findings: [finding({ id: "m", provenance: "mixed", provenance_label: "複数の出所" })],
          findings_initial_count: 1,
          findings_state: "has_findings",
        })}
      />,
    );
    expect(within(screen.getByTestId("overview-finding")).getByText(/複数の出所/))
      .toBeInTheDocument();
  });
});

describe("Runtime coverage entity (Issue #384)", () => {
  const base = {
    state: "receiving" as const,
    freshness: "receiving_now" as const,
    freshness_label: "受信中",
    transport_freshness: "receiving_now" as const,
    last_real_trace_at: 1000,
    seconds_since_last_trace: 10,
    last_trace_at: 1000,
    seconds_since_last_any_trace: 10,
    evaluated_at: 1010,
    real_trace_count_5m: 1,
    real_trace_count_1h: 1,
    real_trace_count_24h: 1,
    delayed_after_seconds: 900,
    stale_after_seconds: 86400,
    component_count: 7,
    total_trace_count: 10,
    mode_counts: { trace: 7 },
    window_seconds: 86400,
    error_count: 0,
    runtime_mismatch_count: 0,
    replayable_count: 0,
    partial_count: 0,
    unreplayable_count: 0,
    not_captured_count: 0,
    observed_component_count: 5,
    known_component_count: 7,
    core_capability_count: 3,
    capability_coverage_state: "not_computed" as const,
    observed_capability_count: null,
    unmapped_component_count: null,
  };

  test("component observation is shown against a component denominator", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime: base })} />);
    const quality = screen.getByTestId("overview-runtime-quality");
    // Both sides of the ratio are components. Pairing 5 components with 3
    // Capabilities produced a "coverage" that could exceed 100%.
    expect(within(quality).getByText("5 / 7")).toBeInTheDocument();
  });

  test("Capability coverage says it is not computed rather than approximating", () => {
    wrap(<RuntimeHealthCard overview={overview({ runtime: base })} />);
    expect(screen.getByTestId("overview-runtime-coverage-unavailable")).toHaveTextContent(
      "算出していません",
    );
    expect(screen.queryByTestId("overview-runtime-coverage")).not.toBeInTheDocument();
  });

  test("a real Capability coverage renders numerator, denominator and unmapped", () => {
    wrap(
      <RuntimeHealthCard
        overview={overview({
          runtime: {
            ...base,
            capability_coverage_state: "computed",
            observed_capability_count: 2,
            unmapped_component_count: 1,
          },
        })}
      />,
    );
    const line = screen.getByTestId("overview-runtime-coverage");
    expect(line).toHaveTextContent("2 / 3");
    expect(line).toHaveTextContent("対応不明の component 1 件");
  });
});

describe("Heading hierarchy (Issue #384)", () => {
  test("each Overview section is a real h2", () => {
    const { unmount } = wrap(
      <SystemBriefCard interviewHref="/interview" overview={overview()} />,
    );
    expect(screen.getByRole("heading", { level: 2, name: "System Brief" })).toBeInTheDocument();
    // The Brief's own sections sit one level below it, so the outline is
    // h1 → h2 → h3 rather than h1 → h3.
    expect(screen.getByRole("heading", { level: 3, name: /^Vision —/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { level: 3, name: /System Purpose/ })).toBeInTheDocument();
    unmount();

    const second = wrap(<FindingsCard overview={overview()} />);
    expect(screen.getByRole("heading", { level: 2, name: "今わかったこと" })).toBeInTheDocument();
    second.unmount();

    const third = wrap(<NextActionCard overview={overview()} />);
    expect(screen.getByRole("heading", { level: 2, name: "次にすること" })).toBeInTheDocument();
    third.unmount();

    wrap(<RuntimeHealthCard overview={overview({ runtime: null, degraded_sections: ["runtime"] })} />);
    expect(screen.getByRole("heading", { level: 2, name: "Runtime health" })).toBeInTheDocument();
  });
});

// ── target links ──────────────────────────────────────────────────────

describe("targetHref", () => {
  test("serialises the destination's own parameter names and anchor", () => {
    expect(
      targetHref({ route: "/interview", label: "", params: { session: "7" }, anchor: "work-surface-W3" }),
    ).toBe("/interview?session=7#work-surface-W3");
    expect(targetHref({ route: "/components", label: "", params: {}, anchor: null })).toBe(
      "/components",
    );
    expect(targetHref(null)).toBe("#");
  });
});

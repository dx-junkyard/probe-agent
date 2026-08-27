/// <reference types="vitest/globals" />
// Issue #349: 状態駆動ワークフローの表示規約テスト。
//
// docs/system-interview-workflow-ux.md の §3.1(情報役割)・§4.3(主操作)・
// §5.1〜§5.3(例外の区分と表示規則)が、コンポーネント側で守られていること
// を確認する。状態そのものの決定はサーバー(app/interview_workflow.py と
// その pytest)側の責務で、ここではその結果の描き方だけを見る。

import { render, screen, fireEvent } from "@testing-library/react";
import { vi } from "vitest";
import type {
  InterviewWorkflowState,
  InterviewWorkflowStateOut,
} from "@/api/types";
import {
  BackRequestNotice,
  PRIMARY_ACTION_LABELS,
  WORKFLOW_STATE_LABELS,
  WorkflowExceptions,
  WorkflowLocationCard,
} from "@/components/system-understanding/workflow-panel";

function workflow(overrides: Partial<InterviewWorkflowStateOut> = {}): InterviewWorkflowStateOut {
  return {
    system_id: 1,
    session_id: 7,
    state: "W5",
    candidate_state: "W5",
    rule_row: 11,
    reached_state: "W5",
    backward_hold: false,
    pending_back_request: null,
    terminal_kind: null,
    primary_action: "approve_proposal",
    facts: {
      has_snapshot: true,
      has_session: true,
      session_closed: false,
      running_process_kinds: [],
      blocking_failure_states: [],
      understanding_unconfirmed: false,
      open_required_questions: 0,
      outstanding_alignment_items: 0,
      proposals_needing_review: 1,
      proposals_generatable: false,
      approved_proposal_count: 0,
      diff_matches_approval_set: false,
      diff_review_complete: false,
      pending_handoff_count: 0,
    },
    running_processes: [],
    unresolved_failures: [],
    exceptions: [],
    diff_materialized_at: null,
    latest_ready_snapshot_id: 42,
    ...overrides,
  };
}

describe("WorkflowLocationCard (R1)", () => {
  const ALL_STATES: InterviewWorkflowState[] = [
    "W0-A", "W0-B", "W1", "W2", "W3", "W4", "W5", "W6", "W7",
  ];

  test("every state has a developer-facing label and no internal stage name leaks", () => {
    for (const state of ALL_STATES) {
      const label = WORKFLOW_STATE_LABELS[state];
      expect(label).toBeTruthy();
      // 内部ステージ名 (understanding_initialized 等) は露出しない (§2.1)。
      expect(label).not.toMatch(/_/);
    }
  });

  test("names exactly one primary action, matching the state's completion condition", () => {
    render(<WorkflowLocationCard workflow={workflow({ state: "W6", primary_action: "record_diff_review" })} />);
    const next = screen.getByTestId("workflow-next-action");
    expect(next).toHaveTextContent(PRIMARY_ACTION_LABELS.record_diff_review);
    // W6 の完了条件は「確認したという記録」であって、ダウンロードではない。
    expect(next).not.toHaveTextContent("ダウンロード");
  });

  test("W1 shows what is running and offers no primary action (§4.3.1)", () => {
    render(
      <WorkflowLocationCard
        workflow={workflow({
          state: "W1",
          primary_action: "none",
          running_processes: [{
            id: 1, session_id: 7, system_id: 1,
            process_kind: "diff_generation", status: "running",
            failure_class: null, target_state: null, error: null,
            started_at: 1, finished_at: null,
          }],
        })}
      />,
    );
    expect(screen.getByTestId("workflow-running")).toHaveTextContent("差分を生成しています");
    expect(screen.queryByTestId("workflow-next-action")).toBeNull();
    // 進捗率・内部ステージ名・ジョブ ID は出さない (§4.4)。
    expect(screen.queryByText(/%/)).toBeNull();
  });

  test("the terminal kind is shown as text, not colour alone (原則 P8)", () => {
    render(
      <WorkflowLocationCard
        workflow={workflow({ state: "W7", terminal_kind: "handoff", primary_action: "view_handoff_status" })}
      />,
    );
    expect(screen.getByTestId("workflow-terminal-kind")).toHaveTextContent("引き継ぎ");
  });
});

describe("WorkflowExceptions (R5)", () => {
  test("renders nothing when no exception is active (§5.3-1)", () => {
    const { container } = render(<WorkflowExceptions workflow={workflow()} />);
    expect(container).toBeEmptyDOMElement();
  });

  test("informational exceptions never appear as R5 warnings (§3.1)", () => {
    render(
      <WorkflowExceptions
        workflow={workflow({
          exceptions: [
            {
              code: "E8", severity: "informational", target_state: "W3",
              message: "回答の修正により、確認が必要な質問が発生しました。",
              detail: null, recovery_process_kind: null, recovery_condition: null,
            },
            {
              code: "E9", severity: "informational", target_state: "W5",
              message: "1 件の提案が再レビュー対象になりました。",
              detail: null, recovery_process_kind: null, recovery_condition: null,
            },
          ],
        })}
      />,
    );
    expect(screen.queryByTestId("workflow-exceptions")).toBeNull();
  });

  test("a blocking exception shows all four points and both allowed recoveries (§4.4/§5.3-7)", () => {
    const onRetry = vi.fn();
    const onLeaveSafely = vi.fn();
    render(
      <WorkflowExceptions
        workflow={workflow({
          exceptions: [{
            code: "E4-a", severity: "blocking", target_state: "W4",
            message: "意図とのズレの突き合わせに失敗し、確認できる項目がまだ 1 件もありません。",
            detail: "reasoning model call failed",
            recovery_process_kind: "alignment_build",
            recovery_condition: "再試行が成功すると通常フローへ戻ります。",
          }],
        })}
        onRetry={onRetry}
        onLeaveSafely={onLeaveSafely}
      />,
    );
    const card = screen.getByTestId("workflow-exception-E4-a");
    expect(card).toHaveAttribute("data-severity", "blocking");
    expect(card).toHaveTextContent("作業を進められません");        // 影響
    expect(card).toHaveTextContent("突き合わせに失敗");             // 何が失敗したか
    expect(card).toHaveTextContent("reasoning model call failed"); // 詳細
    expect(card).toHaveTextContent("再試行が成功すると");           // 戻る条件

    fireEvent.click(screen.getByTestId("workflow-retry-alignment_build"));
    expect(onRetry).toHaveBeenCalledWith("alignment_build");
    fireEvent.click(screen.getByTestId("workflow-leave-safely"));
    expect(onLeaveSafely).toHaveBeenCalled();
    // 迂回路 (人間のゲートを飛ばす操作) は存在しない。
    expect(screen.queryByText(/突き合わせを行わずに進む/)).toBeNull();
  });

  test("a degraded exception is labelled differently and offers no safe-exit lead (§5.3-5)", () => {
    render(
      <WorkflowExceptions
        workflow={workflow({
          exceptions: [{
            code: "E4-b", severity: "degraded", target_state: null,
            message: "突き合わせの再実行に失敗しました。表示中の項目は前回の結果です。",
            detail: null,
            recovery_process_kind: "alignment_build",
            recovery_condition: "再試行が成功すると解消します。",
          }],
        })}
        onLeaveSafely={() => {}}
      />,
    );
    const card = screen.getByTestId("workflow-exception-E4-b");
    expect(card).toHaveAttribute("data-severity", "degraded");
    expect(card).toHaveTextContent("一部の情報が欠けています");
    expect(screen.queryByTestId("workflow-leave-safely")).toBeNull();
  });
});

describe("BackRequestNotice (E8, 情報区分)", () => {
  test("is hidden unless a backward request is actually pending", () => {
    const { container } = render(
      <BackRequestNotice workflow={workflow()} onAcknowledge={() => {}} />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  test("offers exactly one lead and names where it would go back to", () => {
    const onAcknowledge = vi.fn();
    render(
      <BackRequestNotice
        workflow={workflow({
          state: "W5",
          candidate_state: "W3",
          backward_hold: true,
          pending_back_request: {
            id: 12, session_id: 7, system_id: 1,
            cause_kind: "question_reopened",
            candidate_state: "W3", reached_state: "W5",
            status: "pending", created_at: 1,
          },
          exceptions: [{
            code: "E8", severity: "informational", target_state: "W3",
            message: "回答の修正により、確認が必要な質問が発生しました。",
            detail: null, recovery_process_kind: null,
            recovery_condition: "「先に確認する」を選ぶと該当の状態へ戻ります。",
          }],
        })}
        onAcknowledge={onAcknowledge}
      />,
    );
    const notice = screen.getByTestId("workflow-back-request");
    expect(notice).toHaveTextContent("回答の修正により");
    expect(notice).toHaveTextContent(WORKFLOW_STATE_LABELS.W3);
    fireEvent.click(screen.getByTestId("workflow-acknowledge-back"));
    expect(onAcknowledge).toHaveBeenCalledWith(12);
  });
});

describe("重複表示の抑止 (原則 P7)", () => {
  test("skipCodes に挙げた例外は汎用カードから外れる", () => {
    const wf = workflow({
      exceptions: [
        {
          code: "E2", severity: "blocking", target_state: "W5",
          message: "Repository の HEAD がセッションの snapshot より進んでいます。",
          detail: null, recovery_process_kind: null,
          recovery_condition: "新しい snapshot に更新すると解消します。",
        },
        {
          code: "E12", severity: "blocking", target_state: "W5",
          message: "差分を生成できませんでした。",
          detail: null, recovery_process_kind: "diff_generation",
          recovery_condition: "原因を解消して差分を生成し直すと通常フローへ戻ります。",
        },
      ],
    });
    render(<WorkflowExceptions workflow={wf} skipCodes={["E2"]} />);
    // E2 は専用のバナーが担うので、汎用カードには出さない。
    expect(screen.queryByTestId("workflow-exception-E2")).toBeNull();
    // それ以外は通常どおり出る。
    expect(screen.getByTestId("workflow-exception-E12")).toBeInTheDocument();
  });
});

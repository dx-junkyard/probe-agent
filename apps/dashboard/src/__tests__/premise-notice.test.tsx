/// <reference types="vitest/globals" />
// Issue #464 — Interview の前提表示と、前提が動いたときの明示選択。
//
// 判定はすべてサーバーが返す。ここで検証するのは Dashboard の唯一の責任、
// 「サーバーの結論を失わず・混ぜず・勝手な判断で置き換えずに描くこと」:
//   * 正常な前提には何も出さない(正常状態のバッジは、本当に読むべきときに
//     読まれなくなる)
//   * branch 済みは stale のままだと分かる形で出し続ける
//   * available_actions に無い選択肢は決して描かない
//   * 昇格は前提が current のときだけ描く(押せて必ず 409 になる操作を
//     見せない)

import { render, screen } from "@testing-library/react";
import {
  CanonicalPromotePanel,
  PremiseNotice,
} from "@/components/system-understanding/premise-notice";
import type { InterviewPremiseOut } from "@/api/types";

function premise(overrides: Partial<InterviewPremiseOut> = {}): InterviewPremiseOut {
  return {
    session_id: 1,
    system_id: 1,
    state: "current",
    reason_code: "premise_matches_head",
    message: "このセッションは現在の正準 Understanding を前提にしています。",
    label: "現在の正準 Understanding と一致",
    continuable: true,
    disposition: "active",
    disposition_label: "通常のセッション",
    base_revision_id: 10,
    base_premise_digest: "d",
    head_revision_id: 10,
    head_premise_digest: "d",
    head_version: 1,
    origin_kind: null,
    origin_session_id: null,
    successor_session_id: null,
    result_revision_id: null,
    promotable_revision_id: null,
    available_actions: ["continue"],
    ...overrides,
  };
}

const noop = () => {};

function renderNotice(value: InterviewPremiseOut) {
  return render(
    <PremiseNotice
      premise={value}
      onRebase={noop}
      onBranch={noop}
      onAdoptCurrent={noop}
      onStartNewSession={noop}
      onOpenSession={noop}
    />,
  );
}

test("a current premise renders nothing at all", () => {
  const { container } = renderNotice(premise());
  expect(container).toBeEmptyDOMElement();
});

test("a stale premise names the three explicit choices and nothing else", () => {
  renderNotice(
    premise({
      state: "stale",
      reason_code: "head_moved",
      label: "前提が古くなっています",
      message: "このセッションの開始後に正準 Understanding が更新されました。",
      continuable: false,
      head_revision_id: 12,
      head_version: 2,
      available_actions: ["start_new_session", "rebase", "branch"],
    }),
  );
  expect(screen.getByTestId("interview-premise-notice")).toBeInTheDocument();
  expect(screen.getByText("前提が古くなっています")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新しい Interview を開始" })).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "現在の前提へ rebase" })).toBeInTheDocument();
  expect(
    screen.getByRole("button", { name: "古い前提のまま維持 (branch)" }),
  ).toBeInTheDocument();
  // 提示されていない選択肢は描かない。
  expect(screen.queryByRole("button", { name: "現在の前提を採用" })).toBeNull();
});

test("a legacy session offers adopting the current premise", () => {
  renderNotice(
    premise({
      state: "invalid",
      reason_code: "premise_not_captured",
      label: "前提が記録されていません",
      message: "このセッションは前提を記録する前に作成されました。",
      continuable: false,
      base_revision_id: null,
      available_actions: ["adopt_current_premise", "start_new_session", "branch"],
    }),
  );
  expect(screen.getByRole("button", { name: "現在の前提を採用" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "現在の前提へ rebase" })).toBeNull();
});

test("a branched session keeps saying its premise is still stale", () => {
  renderNotice(
    premise({
      state: "stale",
      reason_code: "head_moved",
      label: "前提が古くなっています",
      message: "このセッションの開始後に正準 Understanding が更新されました。",
      continuable: true,
      disposition: "branched",
      disposition_label: "古い前提のまま維持(branch)",
      available_actions: ["start_new_session", "rebase", "branch"],
    }),
  );
  expect(screen.getByText("前提が古くなっています")).toBeInTheDocument();
  expect(screen.getByText("古い前提のまま維持(branch)")).toBeInTheDocument();
});

test("promotion is offered only when the premise is current", () => {
  const promotable = premise({ promotable_revision_id: 11 });
  const { rerender, container } = render(
    <CanonicalPromotePanel
      premise={promotable}
      headRevisionId={10}
      headVersion={1}
      onPromote={noop}
    />,
  );
  expect(screen.getByTestId("canonical-promote-panel")).toBeInTheDocument();

  rerender(
    <CanonicalPromotePanel
      premise={{ ...promotable, state: "stale", continuable: true }}
      headRevisionId={10}
      headVersion={1}
      onPromote={noop}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

test("promotion sends the head the developer was actually looking at", () => {
  const calls: unknown[] = [];
  render(
    <CanonicalPromotePanel
      premise={premise({ promotable_revision_id: 11 })}
      headRevisionId={10}
      headVersion={3}
      onPromote={input => calls.push(input)}
    />,
  );
  screen.getByRole("button", { name: "正準として確定する" }).click();
  expect(calls).toEqual([
    {
      revision_id: 11,
      expected_head_revision_id: 10,
      expected_head_version: 3,
    },
  ]);
});

test("an already-canonical revision offers no promotion", () => {
  const { container } = render(
    <CanonicalPromotePanel
      premise={premise({ promotable_revision_id: 10 })}
      headRevisionId={10}
      headVersion={1}
      onPromote={noop}
    />,
  );
  expect(container).toBeEmptyDOMElement();
});

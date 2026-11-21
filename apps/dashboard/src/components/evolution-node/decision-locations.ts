// Evolution Node について行える判断と、それをどこで行えるか (UX 監査 2026-11-17 C7)。
//
// #401 (Phase 6: lifecycle UX 統合) で操作画面を作るまでのつなぎとして、画面から
// 操作できない判断を「画面未対応 (API のみ)」と明示するための固定表。これは
// サーバーの状態判定ではなく「どの判断がどの画面・どの endpoint にあるか」という
// コードの事実なので、クライアントの定数として持つ。Node の状態に応じて行を
// 出し分けたり、実行可否を先回りで判定したりはしない (#396: 画面は遷移表を持たない)。
//
// endpoint は実際の router (prefix + path) から写したもの:
//   - app/routes/evolution_nodes.py   prefix "/evolution-nodes"
//   - app/routes/stabilization.py     prefix "/stabilization"
//   - app/routes/node_operations.py   prefix "/node-operations"
//   - app/routes/execution_modes.py   prefix "/execution-modes"
//   - app/routes/flow_experiments.py  prefix "/flow-experiments"
// 画面で行えるかどうかは、Dashboard の hooks がその endpoint を呼び、かつその hook を
// 使う画面があるかで確定した (`pages/evolution-nodes.tsx` / `pages/flow-agents.tsx`)。

export type EvolutionDecisionId =
  | "maturity_transition"
  | "register_version"
  | "register_implementation"
  | "add_link"
  | "pin_stable_implementation"
  | "stabilization_package"
  | "stabilization_parent_review"
  | "stabilization_approval"
  | "monitoring_contract"
  | "reopen_approval"
  | "execution_mode_assignment"
  | "flow_experiment_proposal"
  | "flow_experiment_decision";

export type EvolutionDecisionLocation =
  | { kind: "this_screen"; note: string }
  | { kind: "flow_agents"; to: string; note: string }
  | { kind: "api_only"; endpoints: string[]; note?: string };

export interface EvolutionDecisionRow {
  id: EvolutionDecisionId;
  label: string;
  location: EvolutionDecisionLocation;
}

export const EVOLUTION_DECISION_LOCATIONS: readonly EvolutionDecisionRow[] = [
  {
    id: "maturity_transition",
    label: "maturity の遷移 (固定化を除く)",
    location: {
      kind: "this_screen",
      note: "下の「maturity を変更する」で要求します。許可されるかはサーバーが判定します。",
    },
  },
  {
    id: "register_version",
    label: "契約 version の登録",
    location: { kind: "api_only", endpoints: ["POST /evolution-nodes/{node_id}/versions"] },
  },
  {
    id: "register_implementation",
    label: "実装の登録",
    location: { kind: "api_only", endpoints: ["POST /evolution-nodes/{node_id}/implementations"] },
  },
  {
    id: "add_link",
    label: "リンクの追加",
    location: { kind: "api_only", endpoints: ["POST /evolution-nodes/{node_id}/links"] },
  },
  {
    id: "pin_stable_implementation",
    label: "安定実装の pin",
    location: {
      kind: "api_only",
      endpoints: ["POST /evolution-nodes/{node_id}/stable-implementation"],
    },
  },
  {
    id: "stabilization_package",
    label: "固定化: stabilization package の作成と根拠の追加",
    location: {
      kind: "api_only",
      endpoints: [
        "POST /stabilization/packages",
        "POST /stabilization/packages/{package_id}/evidence",
      ],
    },
  },
  {
    id: "stabilization_parent_review",
    label: "固定化: parent review",
    location: {
      kind: "api_only",
      endpoints: ["POST /stabilization/packages/{package_id}/parent-review"],
    },
  },
  {
    id: "stabilization_approval",
    label: "固定化: 承認 (または却下)",
    location: {
      kind: "api_only",
      endpoints: [
        "POST /stabilization/packages/{package_id}/approve",
        "POST /stabilization/packages/{package_id}/reject",
      ],
      note: "established への遷移はこの承認を通してだけ記録されます。",
    },
  },
  {
    id: "monitoring_contract",
    label: "監視契約の設定",
    location: {
      kind: "api_only",
      endpoints: ["POST /node-operations/nodes/{node_id}/monitoring-contracts"],
    },
  },
  {
    id: "reopen_approval",
    label: "reopen 計画の作成と承認",
    location: {
      kind: "api_only",
      endpoints: [
        "POST /node-operations/nodes/{node_id}/reopen-plans",
        "POST /node-operations/reopen-plans/{plan_id}/approve",
      ],
    },
  },
  {
    id: "execution_mode_assignment",
    label: "実行モードの割り当て (node スコープ) と revoke",
    location: {
      kind: "flow_agents",
      to: "/flow-agents",
      note: "Flow・エージェント群の「実行モード」で、スコープを node にして node_key を指定します。",
    },
  },
  {
    id: "flow_experiment_proposal",
    label: "Flow 実験提案の作成",
    location: {
      kind: "api_only",
      endpoints: ["POST /flow-experiments/draft", "POST /flow-experiments"],
    },
  },
  {
    id: "flow_experiment_decision",
    label: "Flow 実験提案の承認・却下・撤回",
    location: {
      kind: "flow_agents",
      to: "/flow-agents",
      note: "作成済みの提案は Flow・エージェント群の「Flow 実験提案」で判断します。",
    },
  },
];

export function decisionRowAnchor(id: EvolutionDecisionId): string {
  return `evolution-decision-${id}`;
}

export function decisionRowLabel(id: EvolutionDecisionId): string {
  return EVOLUTION_DECISION_LOCATIONS.find((row) => row.id === id)?.label ?? id;
}

/** `{node_id}` を実際の id に置き換える。他の placeholder はそのまま残す。 */
export function fillNodeId(endpoint: string, nodeId: number): string {
  return endpoint.replace("{node_id}", String(nodeId));
}

/// <reference types="vitest/globals" />
// Issue #367: Trace 詳細で秘密値が露出しないことと、redaction の3状態が
// 混同されないことを検証する。
//
// 1. 入出力の中身は既定で折りたたまれ、型・件数・サイズ・redaction有無を先に示す。
// 2. redaction 未確認（null）/ 秘匿値なし / redact済み を別表示にする。
// 3. redact が Replay 入力に及ぶ場合とそうでない場合を書き分ける。

import { render, screen, fireEvent } from "@testing-library/react";
import type { TracePayloadSummary, TraceRedaction } from "@/api/types";
import { RedactionBadge, TracePayloadPanel, formatBytes } from "@/components/trace-payload-panel";

const summary: TracePayloadSummary = {
  input: { kind: "object", item_count: 2, bytes: 128 },
  output: { kind: "string", item_count: null, bytes: 32 },
  error: { kind: "none", item_count: null, bytes: 0 },
};

const redacted: TraceRedaction = {
  redacted: true,
  rules: ["sensitive_key"],
  fields: [{ field: "input", path: "kwargs.api_key", rule: "sensitive_key" }],
};

describe("TracePayloadPanel", () => {
  it("入出力の中身を既定で表示しない", () => {
    render(
      <TracePayloadPanel
        input={{ kwargs: { api_key: "██redacted██" } }}
        output="'ok'"
        error={null}
        summary={summary}
        redaction={redacted}
      />,
    );
    expect(screen.queryByTestId("trace-payload-body")).toBeNull();
  });

  it("展開前に型・件数・サイズを示す", () => {
    render(
      <TracePayloadPanel input={{}} output="'ok'" error={null} summary={summary} redaction={null} />,
    );
    expect(screen.getByText(/オブジェクト · 2件 · 128 B/)).toBeTruthy();
    expect(screen.getByText(/文字列 · 32 B/)).toBeTruthy();
  });

  it("明示的に展開すると中身を表示する", () => {
    render(
      <TracePayloadPanel input={{ a: 1 }} output="'ok'" error={null} summary={summary} redaction={null} />,
    );
    fireEvent.click(screen.getByText("入出力の中身を表示"));
    expect(screen.getByTestId("trace-payload-body")).toBeTruthy();
  });

  it("redact済みのときフィールドと規則を示す", () => {
    render(
      <TracePayloadPanel input={{}} output={null} error={null} summary={summary} redaction={redacted} />,
    );
    const note = screen.getByTestId("trace-redaction-note");
    expect(note.textContent).toContain("kwargs.api_key");
    expect(note.textContent).toContain("秘匿キー名");
  });

  it("Replay入力に影響しない redact は表示上のマスクと明記する", () => {
    render(
      <TracePayloadPanel
        input={{}} output={null} error={null}
        summary={summary} redaction={redacted} replayAffected={false}
      />,
    );
    expect(screen.getByTestId("trace-redaction-note").textContent)
      .toContain("Replayに使う入力capture自体は影響を受けていません");
  });

  it("Replay入力に及ぶ redact は復元できない旨を示す", () => {
    render(
      <TracePayloadPanel
        input={{}} output={null} error={null}
        summary={summary}
        redaction={{
          redacted: true,
          rules: ["sensitive_key"],
          fields: [{ field: "input_capture", path: "kwargs.token", rule: "sensitive_key" }],
        }}
        replayAffected
      />,
    );
    expect(screen.getByTestId("trace-redaction-note").textContent)
      .toContain("Replayで復元できません");
  });
});

describe("RedactionBadge", () => {
  it("未スキャン(null)を「秘匿値なし」と混同しない", () => {
    const { rerender } = render(<RedactionBadge redaction={null} />);
    expect(screen.getByText("redaction未確認")).toBeTruthy();

    rerender(<RedactionBadge redaction={{ redacted: false, rules: [], fields: [] }} />);
    expect(screen.getByText("秘匿値なし")).toBeTruthy();
    expect(screen.queryByText("redaction未確認")).toBeNull();
  });

  it("redact済みは件数を示す", () => {
    render(<RedactionBadge redaction={redacted} />);
    expect(screen.getByText("redact済み 1件")).toBeTruthy();
  });
});

describe("formatBytes", () => {
  it("単位を切り替える", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});

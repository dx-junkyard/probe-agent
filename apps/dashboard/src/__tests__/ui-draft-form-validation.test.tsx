/// <reference types="vitest/globals" />
// Issue #451 (`docs/01-specifications/capabilities/ai-discussion-adapter.md` §2.8): `useFormValidation`
// (`src/lib/ui-draft.tsx`) is the ONE place a rejected save's `ApiError`
// (`fieldPath`/`section`, both structural -- never guessed from `.detail`'s
// message text) becomes per-field diagnostics a form can display AND feed
// into its own `useUiDraftSource` `fields[].validationError`. These tests
// exercise the hook directly, isolated from any specific form component.

import { act, renderHook } from "@testing-library/react";
import { useFormValidation } from "@/lib/ui-draft";
import type { ApiError } from "@/api/client";

function apiError(overrides: Partial<ApiError> = {}): ApiError {
  return {
    name: "ApiError",
    message: "既定の失敗メッセージ",
    status: 422,
    detail: "既定の失敗メッセージ",
    code: "some_code",
    fieldPath: "",
    section: "",
    ...overrides,
  } as ApiError;
}

describe("useFormValidation", () => {
  test("編集前に送った保存の遅延診断は新しい draft を汚さない", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => { token = result.current.begin(); });
    act(() => { result.current.clearField("statement"); });
    act(() => { result.current.resolveError(token, apiError({ fieldPath: "statement" }), ["statement"]); });
    expect(result.current.status).toBe("idle");
    expect(result.current.fieldErrors).toEqual({});
  });
  test("初期状態は idle で診断を持たない", () => {
    const { result } = renderHook(() => useFormValidation());
    expect(result.current.status).toBe("idle");
    expect(result.current.fieldErrors).toEqual({});
    expect(result.current.formError).toBeNull();
  });

  test("begin() は validating へ遷移する", () => {
    const { result } = renderHook(() => useFormValidation());
    act(() => {
      result.current.begin();
    });
    expect(result.current.status).toBe("validating");
  });

  test("known field の field_path は fieldErrors へ、他の field は無傷のまま残る", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(
        token,
        apiError({ code: "journey_step_key_duplicated", detail: "重複しています", fieldPath: "title", section: "" }),
        ["title", "beneficiary"],
      );
    });
    expect(result.current.status).toBe("invalid");
    expect(result.current.fieldErrors.title?.message).toBe("重複しています");
    expect(result.current.fieldErrors.beneficiary).toBeUndefined();
    expect(result.current.formError).toBeNull();
  });

  test("空の field_path はフォーム全体のエラーになる", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(
        token,
        apiError({ code: "out_of_scope_requirement_not_verifiable", detail: "対象外です", fieldPath: "", section: "acceptance_criteria" }),
        ["statement", "rationale"],
      );
    });
    expect(result.current.formError).toEqual({
      message: "対象外です", code: "out_of_scope_requirement_not_verifiable", section: "acceptance_criteria",
    });
    expect(result.current.fieldErrors).toEqual({});
  });

  test("このフォームの knownFields に無い field_path はフォーム全体のエラーになる (§2.8.2)", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      // step_key is a real code the server returns for a Journey revision
      // save, but the top-level Journey form does not render an input named
      // "step_key" -- this must NOT silently vanish or attach to the wrong
      // input; it becomes the form-level diagnostic.
      result.current.resolveError(
        token,
        apiError({ code: "journey_step_key_duplicated", detail: "step_key が重複しています", fieldPath: "step_key", section: "steps" }),
        ["title", "beneficiary", "usage_context", "entry_trigger", "value_arrival", "summary"],
      );
    });
    expect(result.current.fieldErrors).toEqual({});
    expect(result.current.formError?.section).toBe("steps");
    expect(result.current.formError?.message).toBe("step_key が重複しています");
  });

  test("clearField はその field だけを解除し、他の診断と formError は保持する", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(token, apiError({ fieldPath: "title" }), ["title", "beneficiary"]);
    });
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(token, apiError({ fieldPath: "beneficiary", detail: "対象者エラー" }), ["title", "beneficiary"]);
    });
    // Both fields now carry independent diagnostics -- resolving a NEW error
    // for one field must not erase an untouched field's earlier one.
    expect(result.current.fieldErrors.title).toBeDefined();
    expect(result.current.fieldErrors.beneficiary?.message).toBe("対象者エラー");

    act(() => {
      result.current.clearField("title");
    });
    expect(result.current.fieldErrors.title).toBeUndefined();
    expect(result.current.fieldErrors.beneficiary?.message).toBe("対象者エラー");
    expect(result.current.status).toBe("invalid"); // beneficiary's diagnostic still stands
  });

  test("clearField で最後の診断が消えると idle に戻る", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(token, apiError({ fieldPath: "title" }), ["title"]);
    });
    act(() => {
      result.current.clearField("title");
    });
    expect(result.current.status).toBe("idle");
  });

  test("resolveSuccess はすべての診断を消して idle に戻す", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(token, apiError({ fieldPath: "title" }), ["title"]);
    });
    act(() => {
      result.current.resolveSuccess(token);
    });
    expect(result.current.status).toBe("idle");
    expect(result.current.fieldErrors).toEqual({});
    expect(result.current.formError).toBeNull();
  });

  test("古い token の応答は無視される -- 遅延応答が新しい試行を汚さない (§2.8.5)", () => {
    const { result } = renderHook(() => useFormValidation());
    let firstToken = 0;
    let secondToken = 0;
    act(() => {
      firstToken = result.current.begin();
    });
    act(() => {
      secondToken = result.current.begin(); // superseded before the first ever resolved
    });
    expect(secondToken).not.toBe(firstToken);

    act(() => {
      // The FIRST (now-stale) attempt's response arrives late.
      result.current.resolveError(firstToken, apiError({ fieldPath: "title", detail: "古い失敗" }), ["title"]);
    });
    // Discarded entirely: no diagnostic, still "validating" (the second
    // attempt has not resolved yet).
    expect(result.current.fieldErrors).toEqual({});
    expect(result.current.formError).toBeNull();
    expect(result.current.status).toBe("validating");

    act(() => {
      result.current.resolveSuccess(secondToken);
    });
    expect(result.current.status).toBe("idle");
  });

  test("古い token の resolveSuccess も無視される", () => {
    const { result } = renderHook(() => useFormValidation());
    let firstToken = 0;
    let secondToken = 0;
    act(() => {
      firstToken = result.current.begin();
    });
    act(() => {
      secondToken = result.current.begin();
    });
    act(() => {
      result.current.resolveError(secondToken, apiError({ fieldPath: "title", detail: "新しい失敗" }), ["title"]);
    });
    act(() => {
      // The stale FIRST attempt's late success must not erase the SECOND
      // attempt's real, still-outstanding failure.
      result.current.resolveSuccess(firstToken);
    });
    expect(result.current.fieldErrors.title?.message).toBe("新しい失敗");
    expect(result.current.status).toBe("invalid");
  });

  test("reset() はすべてを消し、以降の古い応答も無効化する", () => {
    const { result } = renderHook(() => useFormValidation());
    let token = 0;
    act(() => {
      token = result.current.begin();
    });
    act(() => {
      result.current.resolveError(token, apiError({ fieldPath: "title" }), ["title"]);
    });
    act(() => {
      result.current.reset();
    });
    expect(result.current.status).toBe("idle");
    expect(result.current.fieldErrors).toEqual({});
    act(() => {
      result.current.resolveError(token, apiError({ fieldPath: "title", detail: "無視されるべき" }), ["title"]);
    });
    expect(result.current.fieldErrors).toEqual({});
  });
});

/// <reference types="vitest/globals" />
// Issue #368: expired / revoked を緑で表示しない。有効な API token を優先し、
// login session と分離する。表示は必ずサーバの `status` を読む。

import type { TokenOut } from "@/api/types";
import {
  TOKEN_STATUS_LABEL,
  TOKEN_STATUS_VARIANT,
  formatExpiresIn,
  isApiToken,
  isTokenUsable,
  sortTokensByUsability,
} from "@/lib/token-display";

function token(overrides: Partial<TokenOut> = {}): TokenOut {
  return {
    id: 1,
    name: "sdk",
    kind: "api",
    user_id: 1,
    system_id: 1,
    revoked: false,
    created_at: 1_800_000_000,
    expires_at: 1_800_086_400,
    status: "active",
    expires_in_seconds: 86_400,
    ...overrides,
  } as TokenOut;
}

describe("token status presentation", () => {
  it("expired と revoked を success 表示にしない", () => {
    expect(TOKEN_STATUS_VARIANT.expired).not.toBe("success");
    expect(TOKEN_STATUS_VARIANT.revoked).not.toBe("success");
  });

  it("使用可能な状態だけが success/warning", () => {
    expect(TOKEN_STATUS_VARIANT.active).toBe("success");
    expect(TOKEN_STATUS_VARIANT.expiring_soon).toBe("warning");
  });

  it("4状態が別々の日本語ラベルを持つ", () => {
    const labels = Object.values(TOKEN_STATUS_LABEL);
    expect(labels).toHaveLength(4);
    expect(new Set(labels).size).toBe(4);
  });
});

describe("isTokenUsable", () => {
  it("いま認証に使えるかで判定する", () => {
    expect(isTokenUsable("active")).toBe(true);
    expect(isTokenUsable("expiring_soon")).toBe(true);
    expect(isTokenUsable("expired")).toBe(false);
    expect(isTokenUsable("revoked")).toBe(false);
    expect(isTokenUsable(undefined)).toBe(false);
  });
});

describe("isApiToken", () => {
  it("login session を SDK 用 token として扱わない", () => {
    expect(isApiToken(token({ kind: "api" }))).toBe(true);
    expect(isApiToken(token({ kind: "session", name: "login session" }))).toBe(false);
  });
});

describe("sortTokensByUsability", () => {
  it("有効な token を先頭にし、同順位は新しい順にする", () => {
    const sorted = sortTokensByUsability([
      token({ id: 1, status: "revoked" }),
      token({ id: 2, status: "expired" }),
      token({ id: 3, status: "active", created_at: 100 }),
      token({ id: 4, status: "expiring_soon" }),
      token({ id: 5, status: "active", created_at: 200 }),
    ]);
    expect(sorted.map(t => t.id)).toEqual([5, 3, 4, 2, 1]);
  });

  it("入力を変更しない", () => {
    const input = [token({ id: 1, status: "revoked" }), token({ id: 2, status: "active" })];
    sortTokensByUsability(input);
    expect(input.map(t => t.id)).toEqual([1, 2]);
  });
});

describe("formatExpiresIn", () => {
  it("無期限を期限切れと混同しない", () => {
    expect(
      formatExpiresIn({ status: "active", expires_at: null, expires_in_seconds: null }),
    ).toBe("無期限");
  });

  it("期限切れを相対時間で表さない", () => {
    expect(
      formatExpiresIn({ status: "expired", expires_at: 1, expires_in_seconds: 0 }),
    ).toBe("期限切れ");
  });

  it("残り時間の単位を切り替える", () => {
    const at = (seconds: number) =>
      formatExpiresIn({ status: "expiring_soon", expires_at: 1, expires_in_seconds: seconds });
    expect(at(30)).toBe("あと1分未満");
    expect(at(1800)).toBe("あと約30分");
    expect(at(7200)).toBe("あと約2時間");
    expect(at(3 * 86400)).toBe("あと約3日");
  });

  it("境界の0秒を期限切れとして扱う", () => {
    expect(
      formatExpiresIn({ status: "expiring_soon", expires_at: 1, expires_in_seconds: 0 }),
    ).toBe("期限切れ");
  });
});

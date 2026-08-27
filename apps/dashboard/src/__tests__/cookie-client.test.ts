import { api } from "@/api/client";

describe("cookie session API client", () => {
  beforeEach(() => {
    localStorage.clear();
    document.cookie = "probe_csrf=csrf-value; Path=/; SameSite=Lax";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      status: 200,
      ok: true,
      json: vi.fn().mockResolvedValue({}),
      statusText: "OK",
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    document.cookie = "probe_csrf=; Max-Age=0; Path=/";
  });

  test("includes cookies and double-submit CSRF on unsafe methods", async () => {
    await api.post("/tokens/me", { name: "sdk" });

    expect(fetch).toHaveBeenCalledOnce();
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(init?.credentials).toBe("include");
    expect(init?.headers).toMatchObject({ "X-CSRF-Token": "csrf-value" });
    expect(init?.headers).not.toHaveProperty("Authorization");
  });

  test("does not add CSRF to safe methods", async () => {
    await api.get("/auth/me");
    const [, init] = vi.mocked(fetch).mock.calls[0];
    expect(init?.credentials).toBe("include");
    expect(init?.headers).not.toHaveProperty("X-CSRF-Token");
    expect(init?.headers).not.toHaveProperty("Authorization");
  });
});

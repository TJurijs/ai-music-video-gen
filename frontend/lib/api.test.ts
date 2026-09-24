import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError, shouldRetryQuery } from "./api";

afterEach(() => vi.unstubAllGlobals());

describe("API failure handling", () => {
  it("does not promise that a failed server request changed nothing", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Internal Server Error", { status: 500 })));
    await expect(api.projects.list()).rejects.toThrow("work may already have started");
  });

  it("does not replay a mutation when its response is lost", async () => {
    const fetch = vi.fn().mockRejectedValue(new TypeError("Failed to fetch"));
    vi.stubGlobal("fetch", fetch);
    await expect(api.projects.create({ name: "Example" })).rejects.toThrow("Check the latest status");
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("preserves validation details and does not retry permanent query errors", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ detail: [{ msg: "A project name is required" }] }), { status: 422 },
    )));
    await expect(api.projects.list()).rejects.toThrow("A project name is required");
    for (const status of [400, 401, 403, 404, 409, 422]) {
      expect(shouldRetryQuery(0, new ApiError("invalid", status))).toBe(false);
    }
  });

  it("bounds recovery attempts for transient read failures", () => {
    for (const error of [new Error("offline"), new ApiError("busy", 429), new ApiError("unavailable", 503)]) {
      expect(shouldRetryQuery(0, error)).toBe(true);
      expect(shouldRetryQuery(1, error)).toBe(true);
      expect(shouldRetryQuery(2, error)).toBe(false);
    }
  });
});

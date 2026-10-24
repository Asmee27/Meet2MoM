// @vitest-environment jsdom
// Phase 5 UI tests: per-stage processing timings.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import StageTimings from "../components/StageTimings";

afterEach(() => cleanup());

const stage = (name, status, duration, extra = {}) => ({
  stage: name,
  parent_stage: null,
  status,
  started_at: "2026-10-22T10:00:00+00:00",
  finished_at: status === "running" ? null : "2026-10-22T10:01:00+00:00",
  duration_seconds: duration,
  detail: {},
  ...extra,
});

describe("StageTimings", () => {
  it("renders nothing without timings", () => {
    const { container } = render(<StageTimings timings={null} />);
    expect(container.innerHTML).toBe("");
    render(<StageTimings timings={{ stages: [] }} />);
    expect(screen.queryByTestId("stage-timings")).toBeNull();
  });

  it("shows completed, failed, cached, running and skipped stages honestly", () => {
    render(
      <StageTimings
        timings={{
          processing_seconds: 95.4,
          stages: [
            stage("speech_pipeline", "completed", 80.2),
            stage("transcription", "cached", 0.4, { parent_stage: "speech_pipeline" }),
            stage("verification", "failed", 3.1, { detail: { error: "RuntimeError: boom" } }),
            stage("topic_segmentation", "running", null),
            stage("pdf_export", "skipped", null),
          ],
        }}
      />,
    );
    expect(screen.getByText(/processing 1 min 35 s/)).toBeTruthy();
    expect(screen.getByTestId("stage-speech_pipeline").textContent).toContain("1 min 20 s");
    expect(screen.getByTestId("stage-transcription").textContent).toContain("cached");
    expect(screen.getByTestId("stage-verification").textContent).toContain("failed after 3.1 s");
    expect(screen.getByTestId("stage-verification").textContent).toContain("RuntimeError: boom");
    expect(screen.getByTestId("stage-topic_segmentation").textContent).toContain("running…");
    const skipped = screen.getByTestId("stage-pdf_export").textContent;
    expect(skipped).toContain("skipped");
    expect(skipped).not.toMatch(/0\.0 s/);
  });

  it("separates real LLM extraction from a local fallback", () => {
    render(
      <StageTimings
        timings={{
          stages: [
            stage("extraction", "completed", 42.0, {
              detail: {
                mode_used: "llm",
                llm_provider: "gemini",
                llm_model: "gemini-3.8-flash",
                llm_provider_seconds: 39.5,
                llm_requests: 1,
              },
            }),
          ],
        }}
      />,
    );
    const llm = screen.getByTestId("stage-extraction").textContent;
    expect(llm).toContain("LLM: gemini / gemini-3.8-flash");
    expect(llm).toContain("provider wait 39.5 s");
    cleanup();

    render(
      <StageTimings
        timings={{
          stages: [
            stage("extraction", "fallback", 3.2, {
              detail: { mode_used: "hybrid", fallback_reason: "LLM extraction failed: LLM request timed out after 60s" },
            }),
          ],
        }}
      />,
    );
    const fallback = screen.getByTestId("stage-extraction").textContent;
    expect(fallback).toContain("fallback · 3.2 s");
    expect(fallback).toContain("timed out");
    expect(fallback).not.toContain("LLM: gemini");
  });

  it("formats durations in seconds and minutes", () => {
    render(<StageTimings timings={{ stages: [stage("transcript_index", "completed", 4.25), stage("transcription", "completed", 250)] }} />);
    expect(screen.getByTestId("stage-transcript_index").textContent).toContain("4.3 s");
    expect(screen.getByTestId("stage-transcription").textContent).toContain("4 min 10 s");
  });
});

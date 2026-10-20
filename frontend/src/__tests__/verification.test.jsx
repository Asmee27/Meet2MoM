// @vitest-environment jsdom
// Phase 1 UI tests: verification results and evidence-suggestion actions.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { ClaimReviewCard, MethodComparison } from "../components/ClaimVerification";
import { confirmedFromClaims } from "../context/MeetingFlowContext";
import { reviewEvidenceSuggestion } from "../services/api";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

const utt = (id, speaker, start, text) => ({
  utt_id: id, speaker, speaker_id: speaker, start, end: start + 4, text,
  asr_conf: 0.9, spk_conf: 0.9, overlap: false,
});

// Shape of one claim from GET /api/meetings/{id}/claims (claims_view.py).
function makeClaim(overrides = {}) {
  return {
    claim_id: "m1_d00001",
    claim_type: "decision",
    text: "Move the product release to Friday",
    state: "accepted",
    evidence_ids: ["u00000"],
    evidence: [utt("u00000", "SPEAKER_03", 0, "The coffee machine is broken again.")],
    exportable: false,
    verification: {
      deterministic: { label: "rejected" },
      semantic: {
        label: "supported_elsewhere",
        cited_entailment: 0.01,
        best_support: 0.95,
        contradiction: 0.02,
        best_evidence: [],
      },
      final: {
        label: "review",
        reasons: [
          { code: "possible_miscitation", severity: "review", message: "The cited utterances do not support the claim.", utt_ids: ["u00004"] },
          { code: "deterministic_no_match", severity: "warning", message: "No close text match.", utt_ids: [] },
        ],
      },
    },
    suggestion: {
      utt_ids: ["u00004"],
      evidence: [utt("u00004", "SPEAKER_00", 125, "Let us move the product release to Friday.")],
      support: 0.95,
      cited_support: 0.01,
    },
    contradiction: null,
    correction: null,
    ...overrides,
  };
}

describe("ClaimReviewCard", () => {
  it("shows the persisted status, reasons and cited evidence with speaker and time", () => {
    render(<ClaimReviewCard claim={makeClaim()} onSuggestionDecision={() => {}} busy={false} />);

    expect(screen.getByTestId("claim-status").textContent).toBe("Needs review");
    const reasons = within(screen.getByTestId("claim-reasons")).getAllByRole("listitem");
    expect(reasons.map((r) => r.textContent)).toEqual([
      "The cited utterances do not support the claim.",
      "No close text match.",
    ]);
    const cited = screen.getByTestId("cited-evidence").textContent;
    expect(cited).toContain("SPEAKER_03 · 0:00 · u00000");
    expect(cited).toContain("The coffee machine is broken again.");
  });

  it("shows the suggested span and sends accept / reject decisions", () => {
    const onDecision = vi.fn();
    render(<ClaimReviewCard claim={makeClaim()} onSuggestionDecision={onDecision} busy={false} />);

    const card = screen.getByTestId("suggested-evidence");
    expect(card.textContent).toContain("SPEAKER_00 · 2:05 · u00004");
    fireEvent.click(within(card).getByRole("button", { name: "Accept suggestion" }));
    fireEvent.click(within(card).getByRole("button", { name: "Reject" }));
    expect(onDecision.mock.calls).toEqual([
      ["m1_d00001", "accept"],
      ["m1_d00001", "reject"],
    ]);
  });

  it("disables the suggestion buttons while a decision is being saved", () => {
    render(<ClaimReviewCard claim={makeClaim()} onSuggestionDecision={() => {}} busy />);
    const buttons = within(screen.getByTestId("suggested-evidence")).getAllByRole("button");
    expect(buttons.every((b) => b.disabled)).toBe(true);
  });

  it("shows contradicting evidence and a recorded correction instead of a suggestion", () => {
    const claim = makeClaim({
      suggestion: null,
      contradiction: {
        utt_ids: ["u00005"],
        evidence: [utt("u00005", "SPEAKER_01", 25, "Actually drop PostgreSQL, use MySQL instead.")],
        score: 0.9,
      },
      correction: { action: "rejected", proposed_utt_ids: ["u00004"], original_utt_ids: ["u00000"] },
    });
    render(<ClaimReviewCard claim={claim} onSuggestionDecision={() => {}} busy={false} />);

    expect(screen.getByTestId("contradicting-evidence").textContent).toContain("use MySQL instead");
    expect(screen.getByTestId("claim-correction").textContent).toContain("rejected");
    expect(screen.queryByTestId("suggested-evidence")).toBeNull();
  });

  it("shows owner and deadline for action items, with honest fallbacks", () => {
    const claim = makeClaim({
      claim_type: "action", text: "Configure the replica servers", owner: null,
      deadline_text: null, deadline_resolved: null, suggestion: null,
    });
    render(<ClaimReviewCard claim={claim} onSuggestionDecision={() => {}} busy={false} />);
    expect(screen.getByText(/Owner: Not assigned · Due: Not specified/)).toBeTruthy();
  });
});

describe("MethodComparison", () => {
  it("shows deterministic and semantic results side by side with the fused status", () => {
    render(<MethodComparison claim={makeClaim()} />);
    const text = screen.getByTestId("method-comparison").textContent;
    expect(text).toContain("No text match");
    expect(text).toContain("Supported elsewhere (possible mis-citation)");
    expect(text).toContain("cited 0.01 · best 0.95");
    expect(screen.getByTestId("claim-status").textContent).toBe("Needs review");
  });

  it("does not invent scores when the semantic verifier was unavailable", () => {
    const claim = makeClaim();
    claim.verification.semantic = { label: "unavailable", cited_entailment: null, best_support: null };
    render(<MethodComparison claim={claim} />);
    const text = screen.getByTestId("method-comparison").textContent;
    expect(text).toContain("Verifier unavailable");
    expect(text).not.toContain("cited");
  });
});

describe("confirmedFromClaims", () => {
  it("keeps only claims the backend marked exportable", () => {
    const data = {
      claims: [
        makeClaim({ claim_id: "a", text: "Confirmed decision", exportable: true }),
        makeClaim({ claim_id: "b", text: "Held decision", exportable: false }),
        makeClaim({
          claim_id: "c", claim_type: "action", text: "Confirmed task", exportable: true,
          owner: "Bob", deadline_text: "by Friday", deadline_resolved: "2026-10-23",
        }),
      ],
    };
    const confirmed = confirmedFromClaims(data);
    expect(confirmed.decisions).toEqual(["Confirmed decision"]);
    expect(confirmed.actionItems).toHaveLength(1);
    expect(confirmed.actionItems[0]).toMatchObject({ task: "Confirmed task", owner: "Bob", dueDate: "2026-10-23" });
    expect(confirmedFromClaims(null)).toEqual({ decisions: [], actionItems: [] });
  });
});

describe("reviewEvidenceSuggestion", () => {
  it("posts the decision to the persisted endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ claims: [] }) });
    vi.stubGlobal("fetch", fetchMock);

    await reviewEvidenceSuggestion("m1", "m1_d00001", "accept");

    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toMatch(/\/api\/meetings\/m1\/claims\/m1_d00001\/evidence-suggestion$/);
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ action: "accept" });
    vi.unstubAllGlobals();
  });

  it("surfaces the backend error instead of pretending it was saved", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false, json: async () => ({ detail: "This suggestion has already been reviewed." }),
    }));
    await expect(reviewEvidenceSuggestion("m1", "x", "reject")).rejects.toThrow("already been reviewed");
    vi.unstubAllGlobals();
  });
});

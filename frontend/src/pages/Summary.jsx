import { useMeetingFlow } from "../context/MeetingFlowContext";
import "../components/ClaimVerification.css";
import CommitmentPanel from "../components/CommitmentPanel";
import MinutesEmailPanel from "../components/MinutesEmailPanel";
import { useAuth } from "../components/AuthGate";
import { Alert, Badge, Button, Icon, SkeletonRows } from "../components/ui";
import { formatDateTime, formatDuration, formatLanguage, formatVersion } from "../format";

function Warnings({ items }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="minutes-warnings">
      {items.map((warning) => (
        <li className="minutes-warning" key={warning}>
          <Icon name="warning" size={13} /> {warning}
        </li>
      ))}
    </ul>
  );
}

function EvidenceIds({ ids }) {
  if (!ids || ids.length === 0) return <span className="minutes-muted">None cited</span>;
  return <span className="ui-mono minutes-evidence">{ids.join(", ")}</span>;
}

// Export bar: approval state first, then the downloads it allows.
function ExportBar({ reviewData, exportStatus, downloadMeetingPdf, isDownloadingPdf, downloadApprovedJson, isDownloadingJson, jsonExportError }) {
  const snap = reviewData?.latest_approved_snapshot;
  const isApproved = reviewData?.draft_status === "approved";
  return (
    <section className="ui-card export-bar" aria-label="Approval and export">
      <div className="export-bar__status">
        {isApproved && snap ? (
          <div className="approved-snapshot-strip" data-testid="snapshot-strip">
            <Badge tone="success" icon="shield">Approved</Badge>
            <span>{formatVersion(snap.version)}</span>
            <span>by {snap.approved_by || "reviewer"}</span>
            {snap.approved_at && <span>on {formatDateTime(snap.approved_at)}</span>}
            {snap.notes && <span>· “{snap.notes}”</span>}
          </div>
        ) : !isApproved ? (
          <div className="unapproved-warning" data-testid="unapproved-warning">
            <Icon name="warning" size={16} />
            <span>
              These minutes have not been approved. Approve them in <strong>Verify &amp; review</strong> before
              sharing the final version.
            </span>
          </div>
        ) : null}
      </div>

      <div className="export-bar__actions">
        <Button
          variant={isApproved ? "primary" : "secondary"}
          icon={exportStatus?.pdf ? "check" : "download"}
          onClick={downloadMeetingPdf}
          loading={isDownloadingPdf}
          disabled={isDownloadingPdf}
        >
          {isDownloadingPdf ? "Preparing PDF…" : exportStatus?.pdf ? "PDF downloaded" : isApproved ? "Download approved PDF" : "Download draft PDF"}
        </Button>

        {/* Phase 3: JSON export (only shown when approved) */}
        {isApproved && (
          <Button
            icon="download"
            onClick={downloadApprovedJson}
            loading={isDownloadingJson}
            disabled={isDownloadingJson}
            data-testid="download-json-button"
          >
            {isDownloadingJson ? "Exporting…" : "Download approved JSON"}
          </Button>
        )}
      </div>

      {jsonExportError && (
        <p className="edit-form-error" role="alert" data-testid="json-export-error">{jsonExportError}</p>
      )}
    </section>
  );
}

export default function Summary() {
  const { user, authRequired } = useAuth() || {};
  const {
    currentStep,
    backendFileId,
    exportStatus,
    momData,
    isGeneratingMom,
    momError,
    isDownloadingPdf,
    downloadMeetingPdf,
    reviewData,
    downloadApprovedJson,
    isDownloadingJson,
    jsonExportError,
  } = useMeetingFlow();

  if (currentStep !== 5) return null;

  const isApproved = reviewData?.draft_status === "approved";
  const heldActions = (momData?.needs_review || []).filter((x) => x.claim_type === "action").length;

  return (
    <main className="page page--workspace minutes-page" aria-labelledby="minutes-title">
      <h2 className="ui-visually-hidden" id="minutes-title">Minutes of Meeting</h2>

      {isGeneratingMom && (
        <div className="ui-card ui-card--pad">
          <div className="loading-card" role="status">
            <span className="ui-spinner" aria-hidden="true" />
            <strong>Generating the minutes from verified claims…</strong>
          </div>
          <SkeletonRows rows={3} label="Generating minutes" />
        </div>
      )}

      {momError && (
        <Alert tone="danger" title="The minutes could not be generated">
          <p>{momError}</p>
        </Alert>
      )}

      {!isGeneratingMom && momData && (
        <>
          <ExportBar
            reviewData={reviewData}
            exportStatus={exportStatus}
            downloadMeetingPdf={downloadMeetingPdf}
            isDownloadingPdf={isDownloadingPdf}
            downloadApprovedJson={downloadApprovedJson}
            isDownloadingJson={isDownloadingJson}
            jsonExportError={jsonExportError}
          />

          {/* Phase 8.4: email the exact approved version (sign-in only). */}
          {isApproved && authRequired && backendFileId && (
            <MinutesEmailPanel
              key={`${backendFileId}-${reviewData?.latest_approved_snapshot?.version || 0}`}
              meetingId={backendFileId}
              shownVersion={reviewData?.latest_approved_snapshot?.version}
            />
          )}

          {momData.extraction?.fallback_note && (
            <Alert tone="warning" title="Extraction fallback" data-testid="fallback-alert">
              <p>{momData.extraction.fallback_note}</p>
            </Alert>
          )}

          <article className="ui-card minutes-doc">
            <header className="minutes-doc__header">
              <div>
                <span className="minutes-doc__eyebrow">Minutes of Meeting</span>
                <h3 className="minutes-doc__title">{momData.meeting_title || "Meeting Minutes"}</h3>
              </div>
              {isApproved ? <Badge tone="success" icon="shield">Approved</Badge> : <Badge tone="warning">Draft</Badge>}
            </header>

            <dl className="minutes-facts">
              <div>
                <dt>Duration</dt>
                <dd>{typeof momData.duration_seconds === "number" ? formatDuration(momData.duration_seconds) : "Not available"}</dd>
              </div>
              <div>
                <dt>Language</dt>
                <dd>
                  {formatLanguage(momData)}
                  {momData.segment_languages && Object.keys(momData.segment_languages).length > 1 && (
                    <span className="minutes-sub" data-testid="language-disagreement">
                      Segments were detected as{" "}
                      {Object.entries(momData.segment_languages).map(([code, n]) => `${code} (${n})`).join(", ")}.
                    </span>
                  )}
                </dd>
              </div>
              <div>
                <dt>Date</dt>
                <dd>{momData.meeting_date || "Not specified"}</dd>
              </div>
              {Array.isArray(momData.participants) && momData.participants.length > 0 && (
                <div>
                  <dt>Speakers</dt>
                  <dd>
                    {momData.participants.join(", ")}
                    {momData.participants.every((p) => /^SPEAKER_\d+$/.test(p)) && (
                      <span className="minutes-sub">Unnamed diarization labels, not linked to accounts.</span>
                    )}
                  </dd>
                </div>
              )}
            </dl>

            <section className="minutes-section">
              <h4>Summary</h4>
              <p>{momData.summary || "No meeting summary was generated."}</p>
            </section>

            <section className="minutes-section">
              <h4>Key discussion points</h4>
              {Array.isArray(momData.discussion_points) && momData.discussion_points.length > 0 ? (
                <ol className="minutes-list">
                  {momData.discussion_points.map((point, index) => (
                    <li key={index}>{typeof point === "string" ? point : point?.text || "Discussion point unavailable"}</li>
                  ))}
                </ol>
              ) : (
                <p className="empty-state-text">No key discussion points were identified.</p>
              )}
            </section>

            <section className="minutes-section">
              <h4>Decisions</h4>
              {Array.isArray(momData.decisions) && momData.decisions.length > 0 ? (
                <ol className="minutes-list">
                  {momData.decisions.map((decision, index) => (
                    <li key={index}>
                      <span>{typeof decision === "string" ? decision : decision?.text || "Decision text unavailable"}</span>
                      {typeof decision !== "string" && decision?.evidence_ids && (
                        <div className="minutes-sub">Evidence: <EvidenceIds ids={decision.evidence_ids} /></div>
                      )}
                      <Warnings items={decision?.warnings} />
                    </li>
                  ))}
                </ol>
              ) : (
                <p className="empty-state-text">No decisions could be confirmed from transcript evidence.</p>
              )}
            </section>

            <section className="minutes-section">
              <h4>Action items</h4>
              {Array.isArray(momData.action_items) && momData.action_items.length > 0 ? (
                <div className="ui-table-wrap">
                  <table className="ui-table minutes-table">
                    <thead>
                      <tr>
                        <th scope="col">Task</th>
                        <th scope="col">Owner</th>
                        <th scope="col">Due</th>
                        <th scope="col">Evidence</th>
                      </tr>
                    </thead>
                    <tbody>
                      {momData.action_items.map((item, index) => (
                        <tr key={index}>
                          <td data-label="Task">
                            {item.task || "Not specified"}
                            <Warnings items={item.warnings} />
                          </td>
                          <td data-label="Owner">{item.owner || "Not assigned"}</td>
                          <td data-label="Due">{item.dueDate || "Not specified"}</td>
                          <td data-label="Evidence"><EvidenceIds ids={item.evidence_ids} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : heldActions > 0 ? (
                <p className="empty-state-text minutes-held-note" data-testid="actions-held-note">
                  No action items are confirmed. {heldActions} candidate action item{heldActions === 1 ? " is" : "s are"} held
                  for review below and {heldActions === 1 ? "is" : "are"} not a task of record.
                </p>
              ) : (
                <p className="empty-state-text">No confirmed action items recorded.</p>
              )}
            </section>

            {/* HELD FOR REVIEW: shown, never part of the confirmed minutes */}
            {Array.isArray(momData.needs_review) && momData.needs_review.length > 0 && (
              <section className="minutes-section minutes-needs-review">
                <h4>Held for review (not confirmed)</h4>
                <p className="empty-state-text">
                  These items could not be confirmed from transcript evidence and are not decisions or tasks of record.
                </p>
                <ul className="minutes-list minutes-list--plain">
                  {momData.needs_review.map((item) => (
                    <li key={item.claim_id}>
                      <Badge tone="warning">{item.claim_type === "decision" ? "Decision" : "Action"}</Badge>{" "}
                      {item.text}
                      <Warnings items={item.reasons} />
                    </li>
                  ))}
                </ul>
              </section>
            )}

            {momData.extraction &&
              (momData.extraction.fallback_reason ||
                !["completed", "no_claims"].includes(momData.extraction.status)) && (
                <p className="empty-state-text minutes-extraction-note">
                  <Icon name="fallback" size={14} /> Extraction status: {momData.extraction.status}
                  {momData.extraction.mode_used ? ` (extractor used: ${momData.extraction.mode_used})` : ""}
                  {momData.extraction.fallback_reason ? ` · ${momData.extraction.fallback_reason}` : ""}
                  {momData.extraction.reason ? ` · ${momData.extraction.reason}` : ""}
                </p>
              )}

            {momData.extraction &&
              !momData.extraction.fallback_reason &&
              ["completed", "no_claims"].includes(momData.extraction.status) && (
                <p className="empty-state-text minutes-extraction-note" data-testid="extraction-source">
                  {momData.extraction.llm_used
                    ? `Extracted by LLM: ${momData.extraction.llm_provider} / ${momData.extraction.llm_model}. Every item cites transcript utterances and was verified independently.`
                    : `Extracted by the local ${momData.extraction.mode_used || "rules"} extractor (no LLM).`}
                </p>
              )}

            {momData.llm_discussion_summary?.points?.length > 0 && (
              <section className="minutes-section">
                <h4>Discussion Summary (LLM, cited, not verified)</h4>
                <p className="empty-state-text">
                  {momData.llm_discussion_summary.note} It reports what was said; only the confirmed sections are
                  decisions or commitments of record.
                </p>
                <ol className="minutes-list">
                  {momData.llm_discussion_summary.points.map((point, index) => (
                    <li key={`${index}-${point.evidence?.[0]}`}>
                      {point.text}
                      <div className="minutes-sub">
                        <span className="ui-mono minutes-evidence">{(point.evidence || []).join(", ")}</span>
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            )}

            <section className="minutes-section">
              <h4>Follow-ups / open items</h4>
              {Array.isArray(momData.follow_ups) && momData.follow_ups.length > 0 ? (
                <ol className="minutes-list">
                  {momData.follow_ups.map((item, index) => <li key={index}>{item}</li>)}
                </ol>
              ) : (
                <p className="empty-state-text">No explicit follow-ups or open items recorded.</p>
              )}
            </section>

            <footer className="minutes-doc__footer">
              Generated from transcript-grounded Meet2MoM data. Every confirmed item cites the utterances it relies on.
            </footer>
          </article>

          {/* Phase 9: commitment ledger */}
          <CommitmentPanel meetingId={backendFileId} reviewerName={user?.display_name || "Local reviewer"} />
        </>
      )}

      {!isGeneratingMom && !momData && !momError && (
        <div className="ui-card">
          <div className="ui-empty">
            <h3 className="ui-empty__title">No minutes yet</h3>
            <p className="ui-empty__text">Return to Verify &amp; review and approve, or continue to generate the minutes.</p>
          </div>
        </div>
      )}
    </main>
  );
}

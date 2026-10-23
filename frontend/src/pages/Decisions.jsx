import { useMeetingFlow } from "../context/MeetingFlowContext";
import { Alert, Badge, Button, EmptyState, Icon, SkeletonRows } from "../components/ui";

export default function Decisions() {
  const {
    currentStep,
    extractedData,
    isExtracting,
    extractionError,
    visibleDecisions,
    visibleActions,
    extractAnimationComplete,
    goToVerify,
  } = useMeetingFlow();

  if (currentStep !== 3) return null;

  const total = extractedData.decisions.length + extractedData.actionItems.length;
  const nothingFound = !isExtracting && !extractionError && total === 0;
  const canContinue = !isExtracting && !extractionError && (extractAnimationComplete || nothingFound);

  return (
    <main className="page page--workspace" aria-label="Extracted decisions and action items">
      <Alert tone="info" title="Extracted, not yet verified">
        <p>
          These items were extracted from the transcript. None is confirmed until the next step checks it against the
          cited transcript evidence.
        </p>
      </Alert>

      {isExtracting && (
        <div className="ui-card ui-card--pad">
          <div className="loading-card" role="status">
            <span className="ui-spinner" aria-hidden="true" />
            <strong>Extracting decisions and action items from the transcript…</strong>
          </div>
          <SkeletonRows rows={3} label="Extracting" />
        </div>
      )}

      {extractionError && (
        <Alert tone="danger" title="Extraction failed">
          <p>{extractionError}</p>
        </Alert>
      )}

      {nothingFound && (
        <div className="ui-card">
          <EmptyState icon="sparkle" title="No decisions or action items were found">
            The extractor ran and found nothing it could cite. You can still continue: reviewers can add items by hand
            in the review step, and the minutes will say that nothing was extracted.
          </EmptyState>
        </div>
      )}

      {total > 0 && (
        <div className="extract-grid">
          <section className="ui-card" aria-labelledby="decisions-heading">
            <div className="ui-card__header">
              <h2 className="ui-card__title" id="decisions-heading">
                <Icon name="checkCircle" size={18} /> Decisions
              </h2>
              <Badge>{extractedData.decisions.length}</Badge>
            </div>
            <ul className="item-list">
              {extractedData.decisions.slice(0, visibleDecisions).map((decision, index) => (
                <li className="item-row ui-enter" key={index}>
                  <span className="item-row__kind">Decision</span>
                  <p className="item-row__text">{decision}</p>
                </li>
              ))}
              {extractedData.decisions.length === 0 && <li className="item-row item-row--empty">No decisions extracted.</li>}
            </ul>
          </section>

          <section className="ui-card" aria-labelledby="actions-heading">
            <div className="ui-card__header">
              <h2 className="ui-card__title" id="actions-heading">
                <Icon name="ledger" size={18} /> Action items
              </h2>
              <Badge>{extractedData.actionItems.length}</Badge>
            </div>
            <ul className="item-list">
              {extractedData.actionItems.slice(0, visibleActions).map((item, index) => (
                <li className="item-row ui-enter" key={index}>
                  <span className="item-row__kind">Action item</span>
                  <p className="item-row__text">{item.task}</p>
                  <div className="ui-meta">
                    <span>Owner: {item.owner}</span>
                    <span>Due: {item.dueDate}</span>
                  </div>
                </li>
              ))}
              {extractedData.actionItems.length === 0 && <li className="item-row item-row--empty">No action items extracted.</li>}
            </ul>
          </section>
        </div>
      )}

      <div className="page-footer-actions">
        <Button variant="primary" size="lg" iconRight="arrowRight" onClick={goToVerify} disabled={!canContinue}>
          Continue to Verify
        </Button>
      </div>
    </main>
  );
}

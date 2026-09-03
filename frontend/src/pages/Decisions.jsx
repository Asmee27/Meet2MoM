import {
  BadgeCheck,
  Quote,
  Clock3,
} from "lucide-react";

import { decisionsData } from "../services/api";

function Decisions() {
  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            DECISION INTELLIGENCE
          </span>

          <h1>Confirmed decisions</h1>

          <p>
            Decisions are extracted using
            contextual proposal and confirmation
            analysis.
          </p>
        </div>
      </div>

      <div className="decision-list">
        {decisionsData.map(
          (decision, index) => (
            <div
              className="decision-card"
              key={decision.id}
            >
              <div className="decision-top">
                <div className="decision-icon">
                  <BadgeCheck size={22} />
                </div>

                <div className="decision-main">
                  <span className="decision-number">
                    DECISION {index + 1}
                  </span>

                  <h3>
                    {decision.decision}
                  </h3>
                </div>

                <div className="confidence">
                  {decision.confidence}%
                  <span>confidence</span>
                </div>
              </div>

              <div className="decision-evidence">
                <Quote size={17} />

                <div>
                  <span>Confirmation evidence</span>

                  <p>
                    “{decision.evidence}”
                  </p>
                </div>
              </div>

              <div className="decision-footer">
                <span>
                  {decision.topic}
                </span>

                <span>
                  <Clock3 size={14} />
                  {decision.time}
                </span>
              </div>
            </div>
          )
        )}
      </div>
    </div>
  );
}

export default Decisions;
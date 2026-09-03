import {
  Download,
  FileText,
  BadgeCheck,
  ListChecks,
  Users,
} from "lucide-react";

function Summary() {
  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            MINUTES OF MEETING
          </span>

          <h1>Meeting summary</h1>

          <p>
            Structured AI-generated meeting
            intelligence ready for review and
            export.
          </p>
        </div>

        <button className="primary-button">
          <Download size={18} />
          Download MoM
        </button>
      </div>

      <div className="summary-layout">
        <section className="panel mom-document">
          <div className="mom-header">
            <div className="mom-logo">
              <FileText size={25} />
            </div>

            <div>
              <span>
                MINUTES OF MEETING
              </span>

              <h2>
                Customer Service Training Review
              </h2>

              <p>
                03 September 2026 · 4m 14s
              </p>
            </div>
          </div>

          <div className="mom-section">
            <h3>Meeting Overview</h3>

            <p>
              The discussion focused on customer
              service training, complaints raised
              in the survey and potential
              improvements to the current training
              approach.
            </p>
          </div>

          <div className="mom-section">
            <h3>Key Discussion Points</h3>

            <ul>
              <li>
                Customer feedback highlighted
                issues requiring additional staff
                training.
              </li>

              <li>
                A refresher course was discussed
                as one possible approach.
              </li>

              <li>
                Participants discussed reviewing
                the existing training process
                before implementing changes.
              </li>
            </ul>
          </div>

          <div className="mom-section">
            <h3>Final Decisions</h3>

            <div className="mom-highlight">
              <BadgeCheck size={19} />

              <span>
                Start by reviewing the current
                training approach and the problems
                identified in the survey.
              </span>
            </div>

            <div className="mom-highlight">
              <BadgeCheck size={19} />

              <span>
                Review the report together before
                deciding which issues require
                action.
              </span>
            </div>
          </div>

          <div className="mom-section">
            <h3>Action Items</h3>

            <div className="mom-highlight">
              <ListChecks size={19} />

              <span>
                Review the current training
                approach.
              </span>
            </div>

            <div className="mom-highlight">
              <ListChecks size={19} />

              <span>
                Identify key issues and highlights
                from the survey report.
              </span>
            </div>
          </div>
        </section>

        <aside className="summary-sidebar">
          <div className="panel">
            <span className="eyebrow">
              MEETING DATA
            </span>

            <div className="summary-stat">
              <Users size={18} />

              <div>
                <span>Speakers</span>
                <strong>5 detected</strong>
              </div>
            </div>

            <div className="summary-stat">
              <BadgeCheck size={18} />

              <div>
                <span>Decisions</span>
                <strong>2 confirmed</strong>
              </div>
            </div>

            <div className="summary-stat">
              <ListChecks size={18} />

              <div>
                <span>Action Items</span>
                <strong>2 extracted</strong>
              </div>
            </div>
          </div>
        </aside>
      </div>
    </div>
  );
}

export default Summary;
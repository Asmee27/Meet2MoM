import { Link } from "react-router-dom";

import {
  Clock3,
  Users,
  MessageSquareText,
  BadgeCheck,
  ArrowRight,
  Upload,
  Sparkles,
} from "lucide-react";

import StatCard from "../components/StatCard";
import {
  meetingInfo,
  decisionsData,
  topicsData,
} from "../services/api";

function Dashboard() {
  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            MEETING INTELLIGENCE
          </span>

          <h1>
            Turn conversations into
            actionable intelligence.
          </h1>

          <p>
            Automatically transcribe,
            understand and transform meetings
            into structured Minutes of Meeting.
          </p>
        </div>

        <Link
          to="/upload"
          className="primary-button"
        >
          <Upload size={18} />
          New Meeting
        </Link>
      </div>

      <div className="stats-grid">
        <StatCard
          title="Meeting Duration"
          value={meetingInfo.duration}
          description="Latest processed meeting"
          icon={Clock3}
        />

        <StatCard
          title="Speakers"
          value={meetingInfo.speakers}
          description="Detected automatically"
          icon={Users}
        />

        <StatCard
          title="Topics"
          value={topicsData.length}
          description="Semantic discussion groups"
          icon={MessageSquareText}
        />

        <StatCard
          title="Final Decisions"
          value={decisionsData.length}
          description="Contextually confirmed"
          icon={BadgeCheck}
        />
      </div>

      <div className="dashboard-grid">
        <section className="panel">
          <div className="panel-header">
            <div>
              <span className="eyebrow">
                LATEST MEETING
              </span>

              <h2>{meetingInfo.title}</h2>
            </div>

            <span className="success-pill">
              Processed
            </span>
          </div>

          <div className="meeting-meta">
            <div>
              <span>Date</span>
              <strong>
                {meetingInfo.date}
              </strong>
            </div>

            <div>
              <span>Duration</span>
              <strong>
                {meetingInfo.duration}
              </strong>
            </div>

            <div>
              <span>Language</span>
              <strong>
                {meetingInfo.language}
              </strong>
            </div>
          </div>

          <div className="meeting-actions">
            <Link
              to="/transcript"
              className="secondary-button"
            >
              View Transcript
              <ArrowRight size={17} />
            </Link>

            <Link
              to="/summary"
              className="primary-button"
            >
              View MoM
              <ArrowRight size={17} />
            </Link>
          </div>
        </section>

        <section className="panel intelligence-card">
          <div className="intelligence-icon">
            <Sparkles size={26} />
          </div>

          <h2>AI Intelligence Pipeline</h2>

          <p>
            Meet2MoM understands speakers,
            discussion topics, decisions and
            actionable tasks — not just raw
            transcription.
          </p>

          <div className="mini-features">
            <span>Speaker Intelligence</span>
            <span>Semantic Topics</span>
            <span>Decision Detection</span>
            <span>Action Extraction</span>
          </div>
        </section>
      </div>
    </div>
  );
}

export default Dashboard;
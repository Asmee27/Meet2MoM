import {
  MessageSquareText,
  Search,
} from "lucide-react";

import {
  meetingInfo,
  transcriptData,
} from "../services/api";

function Transcript() {
  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            TRANSCRIPT
          </span>

          <h1>Speaker-aware transcript</h1>

          <p>
            Review what was said, when it was
            said and which detected speaker
            said it.
          </p>
        </div>
      </div>

      <section className="panel">
        <div className="panel-header">
          <div>
            <h2>{meetingInfo.title}</h2>

            <p className="panel-description">
              {meetingInfo.duration} ·{" "}
              {meetingInfo.speakers} speakers
            </p>
          </div>

          <div className="search-box">
            <Search size={17} />
            <input
              type="text"
              placeholder="Search transcript"
            />
          </div>
        </div>

        <div className="transcript-list">
          {transcriptData.map((item) => (
            <div
              className="transcript-row"
              key={item.id}
            >
              <div className="speaker-avatar">
                <MessageSquareText
                  size={18}
                />
              </div>

              <div className="transcript-body">
                <div className="transcript-meta">
                  <strong>
                    {item.speaker}
                  </strong>

                  <span>{item.time}</span>
                </div>

                <p>{item.text}</p>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export default Transcript;
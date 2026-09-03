import {
  Layers3,
  Clock3,
} from "lucide-react";

import { topicsData } from "../services/api";

function Topics() {
  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            TOPIC INTELLIGENCE
          </span>

          <h1>Discussion topics</h1>

          <p>
            Semantic segmentation groups the
            meeting into meaningful discussion
            sections.
          </p>
        </div>
      </div>

      <div className="topic-grid">
        {topicsData.map((topic) => (
          <div
            className="topic-card"
            key={topic.id}
          >
            <div className="topic-number">
              <Layers3 size={19} />
              Topic {topic.id}
            </div>

            <h3>{topic.title}</h3>

            <div className="topic-meta">
              <span>
                <Clock3 size={15} />
                {topic.time}
              </span>

              <span>
                {topic.segments} segments
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default Topics;
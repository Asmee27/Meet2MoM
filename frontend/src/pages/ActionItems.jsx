import {
  ListChecks,
  UserRound,
  CalendarDays,
} from "lucide-react";

import { actionItemsData } from "../services/api";

function ActionItems() {
  return (
    <div>
      <div className="page-heading">
        <div>
          <span className="eyebrow">
            ACTION INTELLIGENCE
          </span>

          <h1>Action items</h1>

          <p>
            Tasks, owners and deadlines extracted
            automatically from meeting context.
          </p>
        </div>
      </div>

      <section className="panel">
        <div className="action-table">
          <div className="action-table-header">
            <span>Task</span>
            <span>Owner</span>
            <span>Deadline</span>
            <span>Status</span>
          </div>

          {actionItemsData.map((item) => (
            <div
              className="action-row"
              key={item.id}
            >
              <div className="action-task">
                <div className="small-icon">
                  <ListChecks size={17} />
                </div>

                <strong>{item.task}</strong>
              </div>

              <span className="table-value">
                <UserRound size={15} />
                {item.owner}
              </span>

              <span className="table-value">
                <CalendarDays size={15} />
                {item.deadline}
              </span>

              <span className="pending-pill">
                {item.status}
              </span>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}

export default ActionItems;
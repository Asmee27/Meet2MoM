import {
  CheckCircle2,
  Circle,
  LoaderCircle,
} from "lucide-react";

function ProcessingStep({
  title,
  description,
  status = "pending",
}) {
  const renderIcon = () => {
    if (status === "completed") {
      return <CheckCircle2 size={21} />;
    }

    if (status === "processing") {
      return (
        <LoaderCircle
          size={21}
          className="spin"
        />
      );
    }

    return <Circle size={21} />;
  };

  return (
    <div className={`processing-step ${status}`}>
      <div className="processing-icon">
        {renderIcon()}
      </div>

      <div>
        <h4>{title}</h4>
        <p>{description}</p>
      </div>

      <span className="step-status">
        {status}
      </span>
    </div>
  );
}

export default ProcessingStep;
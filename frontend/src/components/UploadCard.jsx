import { useRef, useState } from "react";
import {
  UploadCloud,
  FileVideo2,
  X,
} from "lucide-react";

function UploadCard({
  selectedFile,
  setSelectedFile,
}) {
  const inputRef = useRef(null);

  const [dragging, setDragging] =
    useState(false);

  const chooseFile = (file) => {
    if (!file) return;

    setSelectedFile(file);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setDragging(false);

    chooseFile(
      event.dataTransfer.files?.[0]
    );
  };

  return (
    <div>
      {!selectedFile ? (
        <div
          className={`upload-zone ${
            dragging ? "dragging" : ""
          }`}
          onClick={() =>
            inputRef.current?.click()
          }
          onDragOver={(event) => {
            event.preventDefault();
            setDragging(true);
          }}
          onDragLeave={() =>
            setDragging(false)
          }
          onDrop={handleDrop}
        >
          <input
            ref={inputRef}
            type="file"
            hidden
            accept=".mp4,.mp3,.wav,.m4a,.webm"
            onChange={(event) =>
              chooseFile(
                event.target.files?.[0]
              )
            }
          />

          <div className="upload-icon">
            <UploadCloud size={36} />
          </div>

          <h3>Upload your meeting</h3>

          <p>
            Drag and drop your recording here,
            or click to browse.
          </p>

          <span>
            MP4, MP3, WAV, M4A or WEBM
          </span>
        </div>
      ) : (
        <div className="selected-file">
          <div className="selected-file-info">
            <div className="file-icon">
              <FileVideo2 size={25} />
            </div>

            <div>
              <strong>
                {selectedFile.name}
              </strong>

              <p>
                {(
                  selectedFile.size /
                  (1024 * 1024)
                ).toFixed(2)}{" "}
                MB
              </p>
            </div>
          </div>

          <button
            className="remove-file"
            onClick={() =>
              setSelectedFile(null)
            }
          >
            <X size={19} />
          </button>
        </div>
      )}
    </div>
  );
}

export default UploadCard;
export const meetingInfo = {
  title: "Customer Service Training Review",
  date: "03 September 2026",
  duration: "4m 14s",
  speakers: 5,
  language: "English",
};

export const transcriptData = [
  {
    id: 1,
    speaker: "SPEAKER_00",
    time: "00:12",
    text: "Paul is the Human Resources Manager at Quartz Power Group.",
  },
  {
    id: 2,
    speaker: "SPEAKER_01",
    time: "00:31",
    text: "We need to look at the training and understand the problems raised by customers.",
  },
  {
    id: 3,
    speaker: "SPEAKER_02",
    time: "01:03",
    text: "The complaints show that staff may require additional support and training.",
  },
  {
    id: 4,
    speaker: "SPEAKER_03",
    time: "02:26",
    text: "Anna suggested that a refresher course could help address the problem.",
  },
  {
    id: 5,
    speaker: "SPEAKER_01",
    time: "03:16",
    text: "So I wondered if we could start with a review of the current training approach.",
  },
  {
    id: 6,
    speaker: "SPEAKER_04",
    time: "03:20",
    text: "Yes, we could do that.",
  },
];

export const topicsData = [
  {
    id: 1,
    title: "Training",
    time: "00:12 – 01:00",
    segments: 6,
  },
  {
    id: 2,
    title: "Complaints & Research",
    time: "01:00 – 02:03",
    segments: 9,
  },
  {
    id: 3,
    title: "Staff & Customers",
    time: "02:03 – 02:26",
    segments: 3,
  },
  {
    id: 4,
    title: "Anna & Refresher Course",
    time: "02:26 – 03:04",
    segments: 6,
  },
  {
    id: 5,
    title: "Sensitive Customer Handling",
    time: "03:04 – 03:16",
    segments: 3,
  },
  {
    id: 6,
    title: "Report Review & Highlights",
    time: "03:16 – 04:00",
    segments: 7,
  },
];

export const decisionsData = [
  {
    id: 1,
    decision:
      "Start with a review of the current training approach and identify issues raised in the survey.",
    evidence:
      "Yes, we could do that.",
    confidence: 85,
    topic: "Report Review & Highlights",
    time: "03:16",
  },
  {
    id: 2,
    decision:
      "Review the report together before determining what needs to be addressed.",
    evidence: "Yes, okay.",
    confidence: 85,
    topic: "Report Review & Highlights",
    time: "03:33",
  },
];

export const actionItemsData = [
  {
    id: 1,
    task: "Review current training approach",
    owner: "To be assigned",
    deadline: "Not specified",
    status: "Pending",
  },
  {
    id: 2,
    task: "Review survey report and identify key highlights",
    owner: "Anna + Team",
    deadline: "Not specified",
    status: "Pending",
  },
];

export async function uploadMeeting(file) {
  // FastAPI integration will replace this.

  return new Promise((resolve) => {
    setTimeout(() => {
      resolve({
        success: true,
        filename: file.name,
      });
    }, 1000);
  });
}
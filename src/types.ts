export type Role = "admin" | "operator";
export type IdentityCategory = "whitelist" | "blacklist";
export type EventType = "match" | "unknown" | "not_registered" | "blacklist";

export interface User {
  id: number;
  username: string;
  displayName: string;
  role: Role;
  active: boolean;
  createdAt: string;
}

export interface Identity {
  id: number;
  candidateNo: string | null;
  displayName: string;
  category: IdentityCategory;
  active: boolean;
  faceSampleCount: number;
  faceUrl: string | null;
  createdAt: string;
}

export interface ExamSession {
  id: number;
  name: string;
  code: string;
  startsAt: string;
  endsAt: string;
  status: "draft" | "active" | "closed";
  memberCount: number;
  cameraCount: number;
  presentCount: number;
}

export interface CameraHealth {
  cameraId: number;
  name: string;
  connected: boolean;
  status: string;
  message: string;
  fps: number;
  streamReadMs?: number;
  frameBufferAgeMs?: number | null;
  sessionId: number | null;
  lastFrameAt: string | null;
}

export interface Camera {
  id: number;
  name: string;
  rtspUrlSet: boolean;
  enabled: boolean;
  rotation: number;
  roi: number[][];
  sessionId: number | null;
  sessionName: string | null;
  previewUrl: string;
  health: CameraHealth | null;
}

export interface RecognitionEvent {
  id: number;
  eventType: EventType;
  cameraId: number;
  cameraName: string | null;
  sessionId: number | null;
  sessionName: string | null;
  identityId: number | null;
  identityName: string | null;
  candidateNo: string | null;
  identityCategory?: IdentityCategory | null;
  identityFaceUrl?: string | null;
  confidence: number | null;
  capturedAt: string;
  decisionLatencyMs: number | null;
  snapshotUrl: string;
  resolvedIdentityId: number | null;
  resolvedIdentityName?: string | null;
  resolvedBy?: string | null;
  resolvedAt?: string | null;
  resolutionNote?: string | null;
  telegramStatus: string;
}

export interface DashboardData {
  activeSessions: ExamSession[];
  cameras: Camera[];
  latestEvent: RecognitionEvent | null;
  recentEvents: RecognitionEvent[];
  counts: { identities: number; present: number; unresolved: number };
}

export interface SystemHealth {
  status: string;
  recognitionReady: boolean;
  inferenceProvider: string;
  cudaAvailable: boolean;
  modelError: string | null;
  modelSamples: number;
  recognitionThreshold: number;
  cameraHealth: CameraHealth[];
  storageBytes: number;
  version: string;
}

export interface EnrollmentFace {
  index: number;
  page: number;
  quality: number;
  eligible: boolean;
  preview: string;
  possibleDuplicate?: string | null;
  duplicateScore?: number | null;
}

export interface EnrollmentItem {
  token?: string;
  filename: string;
  suggestedName?: string;
  status: string;
  faces: EnrollmentFace[];
  message?: string | null;
}

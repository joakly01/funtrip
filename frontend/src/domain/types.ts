export interface Attachment {
  id: string;
  name: string;
  kind: "image" | "file" | "audio";
  size: number;
  mime?: string;
  warning?: string;
  transcript?: string;
  preview_text?: string;
  duration?: number;
  text_length?: number;
}
export interface Model {
  id: string;
  label: string;
  vision: boolean;
  configured: boolean;
}
export interface Message {
  role: string;
  text: string;
  attachments?: Attachment[];
  model?: string;
}
export interface Session {
  id: string;
  title: string;
  updated_at: string;
}
export interface RecordRow {
  id: string;
  kind: string;
  version: number;
  created_at: string;
  updated_at: string;
  status: string;
  body: Record<string, any>;
  layers?: string[];
  states?: Record<string, string>;
  history?: any[];
  reference_ids?: string[];
  dependent_ids?: string[];
  collection?: {
    shelf: string;
    topic: string;
    starred: boolean;
    organized: boolean;
    archive: boolean;
  };
}
export interface SelectedFile {
  path?: string;
  file?: File;
  name: string;
  size: number;
  duration?: number;
}
export const VERSION = "0.6.0";
export const newId = () =>
  "unified_" + Date.now() + "_" + Math.random().toString(36).slice(2, 10);

export interface User { id: string; tenant_id: string; name: string }
export interface KnowledgeBase { id: string; name: string; created_at: string; created_by?: string }
export interface IngestJob {
  id: string; status: string; current_stage: string; progress: number;
  error_code?: string | null; cancel_requested?: boolean;
}
export interface DocumentVersion { id: string; version_no: number; status: string; quality_status?: string }
export interface DocumentRecord {
  id: string; kb_id: string; name: string; created_at: string; status: string;
  current_version_id: string | null; deleted_at?: string | null;
  latest_version: DocumentVersion | null; job: IngestJob | null;
}
export interface Chunk { id: string; chunk_text: string; page_start: number; page_end: number }
export interface ChatSession { id: string; title: string; created_at: string }
export interface Message { id?: string; role: string; content: string; run_id: string | null; answer_version?: number }
export interface QueryRun {
  id: string; session_id: string; status: string; question: string;
  error_code?: string | null; created_at?: string;
}
export interface AnswerCitation {
  id?: string; run_id: string; document_id: string; document_version: string;
  document_name: string; chunk_id: string; page_start: number; page_end: number;
  section_path: string[]; source_excerpt: string; answer_version?: number;
}
export interface ChatHistory { messages: Message[]; runs: QueryRun[]; citations: AnswerCitation[] }
export interface SystemStatus {
  api: string; postgres: string; environment: string; scanner_configured: boolean;
  external_services_verified: boolean; web_search_enabled: boolean; worker_note: string;
}
export interface EvalDataset { id: string; name: string; version: string }
export interface EvalRun {
  id: string; dataset_id: string; status: string; created_at: string;
  error_code?: string | null; metrics_jsonb: Record<string, unknown>;
}

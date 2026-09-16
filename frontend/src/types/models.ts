// Espelho exato dos modelos Pydantic em app/models.py
// Se o backend mudar, este arquivo muda junto.

export type InterfaceType =
  | 'odata'
  | 'rfc'
  | 'servicenow'
  | 'salesforce'
  | 'workday'
  | 'ariba'
  | 'cap'
  | 'apim';

export interface IncidentRequest {
  description: string;
  interface_type?: InterfaceType;
  identifier?: string;
  logs?: string;
  payload?: string;
}

export interface DiagnosisResponse {
  probable_root_cause: string;
  confidence: number;        // 0.0 a 1.0
  next_steps: string[];
  report_markdown: string;
  matched_source: string | null;
}

// Tipo interno da UI — não existe no backend
export interface HistoryItem {
  description: string;
  interface_type: string;
  identifier: string;
  result: DiagnosisResponse;
  ts: Date;
}

// @copilot/core-ui - API publica (DA-95). Shells de produto importam SO
// daqui (o gate import_boundaries reprova import profundo de src/*).

export { HTTP_CONTRACT_VERSION, ApiError, apiErrorFrom, apiJson, configureApi, describeApiError } from './api/http';
export { getSession, login, logout, verifyEmail, verifyPhone, type SessionState } from './api/auth';
export { diagnose, getDomainPlugins } from './api/diagnosis';
export type {
  ApiErrorCode,
  CauseStatus,
  ContentLocale,
  CoreIncidentRequest,
  DiagnosisResponse,
  DomainPluginInfo,
  EscalationReason,
  EscalationSignal,
  EvidenceItem,
  Finding,
  JsonSchema,
} from './api/contracts';

export {
  LOCALE_COOKIE,
  LOCALE_LABELS,
  SUPPORTED_LOCALES,
  diagnosisLocale,
  initI18n,
  normalizeLocale,
  setLocale,
  type Catalog,
  type Locale,
  type ProductCatalogs,
} from './i18n';
export { formatNumber, formatPercent, formatTime, formatUSD } from './i18n/format';

export { Badge } from './components/Badge';
export { EscalationBanner, EvidenceList, Provenance } from './components/DiagnosisDetails';
export { DiagnosisLoading, DiagnosisResult } from './components/DiagnosisResult';
export { FindingsPanel } from './components/FindingsPanel';
export { LanguageSelect } from './components/LanguageSelect';
export { LoginView, type ProductBrand } from './components/LoginView';
export {
  SchemaForm,
  defaultsFromSchema,
  validateAgainstSchema,
  type FieldError,
  type FieldErrorCode,
  type FormValue,
} from './components/SchemaForm';
export { SessionGate, type SessionControls } from './components/SessionGate';

// Entrada fiscal e resultado do DF-e Copilot (DA-95).
//
// O formulario de contexto (ambiente, UF, linha do tempo) e' gerado do JSON
// Schema que o plugin `dfe` publica em GET /domain-plugins: o shell nao
// duplica o contrato do FiscalContext, so traduz os rotulos dos campos. A
// validacao local e' conveniencia; a verdade e' o backend (422).

import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import {
  ApiError,
  DiagnosisLoading,
  DiagnosisResult,
  SchemaForm,
  defaultsFromSchema,
  describeApiError,
  diagnose,
  diagnosisLocale,
  getDomainPlugins,
  validateAgainstSchema,
  type DiagnosisResponse,
  type DomainPluginInfo,
  type FieldError,
  type FormValue,
  type JsonSchema,
} from '@copilot/core-ui';

const PLUGIN = 'dfe';
const MAX_XML_BYTES = 50_000; // mesmo limite de payload do core (MAX_PAYLOAD_LENGTH)

type PluginState = { status: 'loading' } | { status: 'ready'; plugin: DomainPluginInfo } | { status: 'missing' } | { status: 'error'; message: string };

export function FiscalDiagnoseView({ onSessionExpired }: { onSessionExpired: () => void }) {
  const { t, i18n } = useTranslation();
  const [plugin, setPlugin] = useState<PluginState>({ status: 'loading' });
  const [description, setDescription] = useState('');
  const [xml, setXml] = useState('');
  const [context, setContext] = useState<FormValue>({});
  const [fieldErrors, setFieldErrors] = useState<FieldError[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DiagnosisResponse | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    getDomainPlugins()
      .then((plugins) => {
        const p = plugins.find((x) => x.name === PLUGIN);
        if (!p) return setPlugin({ status: 'missing' });
        setPlugin({ status: 'ready', plugin: p });
        setContext(defaultsFromSchema(p.context_schema));
      })
      .catch((e) => {
        if (e instanceof ApiError && e.status === 401) return onSessionExpired();
        setPlugin({ status: 'error', message: e instanceof ApiError ? describeApiError(e) : t('core.common.networkError') });
      });
  }, [onSessionExpired, t]);

  // Rotulo do campo no catalogo do produto: `context.fields.<caminho com _>`
  // (ex.: events.at -> events_at); sem chave, o titulo do schema.
  const slug = (path: string) => path.replaceAll('.', '_');
  const label = (path: string, s: JsonSchema) => {
    const key = `context.fields.${slug(path)}`;
    return i18n.exists(key) ? t(key) : (s.title ?? path);
  };
  const optionLabel = (path: string, option: string) => {
    const key = `context.options.${slug(path)}.${option}`;
    return i18n.exists(key) ? t(key) : option;
  };

  function loadFile(file: File | undefined) {
    if (!file) return;
    if (file.size > MAX_XML_BYTES) {
      setError(t('fiscal.xmlTooLarge', { max: MAX_XML_BYTES }));
      return;
    }
    file.text().then(setXml).catch(() => setError(t('fiscal.xmlUnreadable')));
  }

  async function submit() {
    if (plugin.status !== 'ready' || loading) return;
    if (!description.trim()) {
      setError(t('fiscal.needDescription'));
      return;
    }
    const errors = validateAgainstSchema(plugin.plugin.context_schema, context);
    setFieldErrors(errors);
    if (errors.length) {
      setError(t('fiscal.fixFields'));
      return;
    }
    setError('');
    setLoading(true);
    setResult(null);
    try {
      setResult(
        await diagnose({
          description: description.trim(),
          payload: xml.trim() || undefined,
          locale: diagnosisLocale(),
          domain_context: context,
        }),
      );
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) onSessionExpired();
      else setError(e instanceof ApiError ? describeApiError(e) : t('core.common.networkError'));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="view">
      <div className="page-title">{t('fiscal.title')}</div>
      <div className="page-sub">{t('fiscal.subtitle')}</div>

      {plugin.status === 'missing' && <div className="form-error">{t('fiscal.pluginMissing')}</div>}
      {plugin.status === 'error' && <div className="form-error">{plugin.message}</div>}

      <div className="form-card">
        <div className="field-row">
          <label className="field-label" htmlFor="dfe-desc">{t('fiscal.description')}</label>
          <textarea
            id="dfe-desc"
            className="field-input"
            rows={3}
            placeholder={t('fiscal.descriptionPlaceholder')}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
        </div>

        <div className="field-row">
          <label className="field-label" htmlFor="dfe-xml">{t('fiscal.xml')}</label>
          <textarea
            id="dfe-xml"
            className="field-input mono-input"
            rows={6}
            placeholder={t('fiscal.xmlPlaceholder')}
            value={xml}
            onChange={(e) => setXml(e.target.value)}
          />
          <div className="dfe-xml-actions">
            <button type="button" className="advanced-toggle" onClick={() => fileRef.current?.click()}>
              {t('fiscal.xmlLoad')}
            </button>
            <span className="dfe-hint">{t('fiscal.xmlHint')}</span>
            <input
              ref={fileRef}
              type="file"
              accept=".xml,text/xml,application/xml"
              hidden
              onChange={(e) => loadFile(e.target.files?.[0])}
            />
          </div>
        </div>

        {plugin.status === 'loading' && <div className="dfe-hint">{t('fiscal.loadingContract')}</div>}
        {plugin.status === 'ready' && (
          <>
            <div className="section-label">{t('fiscal.contextTitle')}</div>
            <SchemaForm
              schema={plugin.plugin.context_schema}
              value={context}
              onChange={setContext}
              label={label}
              optionLabel={optionLabel}
              errors={fieldErrors}
            />
          </>
        )}

        {error && <div className="form-error">{error}</div>}
        <div className="btn-row">
          <button className="submit-btn" onClick={submit} disabled={loading || plugin.status !== 'ready'}>
            {t('fiscal.submit')}
          </button>
        </div>
        <p className="dfe-readonly">{t('fiscal.readOnly')}</p>
      </div>

      {loading && <DiagnosisLoading />}
      {result && !loading && <DiagnosisResult result={result} />}
    </div>
  );
}

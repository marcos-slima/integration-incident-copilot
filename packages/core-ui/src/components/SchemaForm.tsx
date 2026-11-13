// SchemaForm (DA-95): formulario gerado do JSON Schema que o plugin de
// dominio publica em GET /domain-plugins. O core nao conhece os campos: o
// rotulo de cada um vem do shell do produto (`label`), e o schema decide o
// tipo de controle. Subconjunto suportado (o que os plugins usam):
// - `const`: campo fixo, nao exibido;
// - `enum` ou `pattern` do tipo ^(a|b|c)$: lista de opcoes;
// - `anyOf: [X, null]`: X opcional;
// - `array` de `object`: tabela de linhas com adicionar/remover;
// - o resto: texto (com `pattern`/`maxLength` validados aqui e no backend).
// A validacao local e' conveniencia: a verdade e' a do backend (422).

import { useTranslation } from 'react-i18next';
import type { JsonSchema } from '../api/contracts';

export type FormValue = Record<string, unknown>;
export type FieldErrorCode = 'required' | 'pattern' | 'maxLength' | 'enum' | 'maxItems';
export interface FieldError {
  path: string;
  code: FieldErrorCode;
}

type Labeler = (path: string, schema: JsonSchema) => string;
type OptionLabeler = (path: string, option: string) => string;

const CHOICES = /^\^\(([^()]+)\)\$$/;

function unwrap(s: JsonSchema): JsonSchema {
  if (s.anyOf) {
    const notNull = s.anyOf.filter((a) => a.type !== 'null');
    if (notNull.length === 1) return { ...notNull[0], title: s.title, description: s.description, default: s.default };
  }
  return s;
}

function choicesOf(s: JsonSchema): string[] | null {
  if (s.enum) return s.enum.map(String);
  const m = s.pattern ? CHOICES.exec(s.pattern) : null;
  return m ? m[1].split('|') : null;
}

/** Valores fixos (`const`) e defaults declarados no schema. */
export function defaultsFromSchema(schema: JsonSchema): FormValue {
  const out: FormValue = {};
  for (const [key, raw] of Object.entries(schema.properties ?? {})) {
    const s = unwrap(raw);
    if (s.const !== undefined) out[key] = s.const;
    else if (s.default !== undefined && s.default !== null) out[key] = s.default;
    else if (s.type === 'array') out[key] = [];
  }
  return out;
}

function checkValue(path: string, s: JsonSchema, v: unknown, required: boolean, errors: FieldError[]) {
  const empty = v === undefined || v === null || v === '';
  if (empty) {
    if (required) errors.push({ path, code: 'required' });
    return;
  }
  if (s.type === 'array' && Array.isArray(v)) {
    if (s.maxItems !== undefined && v.length > s.maxItems) errors.push({ path, code: 'maxItems' });
    v.forEach((row, i) => {
      if (s.items?.type === 'object') validateObject(`${path}.${i}`, s.items, row as FormValue, errors);
    });
    return;
  }
  if (typeof v !== 'string') return;
  const choices = choicesOf(s);
  if (choices && !choices.includes(v)) errors.push({ path, code: 'enum' });
  else if (s.pattern && !new RegExp(s.pattern).test(v)) errors.push({ path, code: 'pattern' });
  if (s.maxLength !== undefined && v.length > s.maxLength) errors.push({ path, code: 'maxLength' });
}

function validateObject(prefix: string, schema: JsonSchema, value: FormValue, errors: FieldError[]) {
  const req = new Set(schema.required ?? []);
  for (const [key, raw] of Object.entries(schema.properties ?? {})) {
    const path = prefix ? `${prefix}.${key}` : key;
    checkValue(path, unwrap(raw), value?.[key], req.has(key), errors);
  }
}

export function validateAgainstSchema(schema: JsonSchema, value: FormValue): FieldError[] {
  const errors: FieldError[] = [];
  validateObject('', schema, value, errors);
  return errors;
}

interface FieldProps {
  path: string;
  schema: JsonSchema;
  value: unknown;
  onChange: (v: unknown) => void;
  label: Labeler;
  optionLabel: OptionLabeler;
  error?: FieldErrorCode;
}

function ScalarField({ path, schema, value, onChange, label, optionLabel, error }: FieldProps) {
  const { t } = useTranslation();
  const choices = choicesOf(schema);
  const text = value === undefined || value === null ? '' : String(value);
  const id = `sf-${path.replaceAll('.', '-')}`;
  return (
    <div className="field-row">
      <label className="field-label" htmlFor={id}>{label(path, schema)}</label>
      {choices ? (
        <select id={id} className="field-input" value={text} onChange={(e) => onChange(e.target.value || undefined)}>
          <option value="">{t('core.form.choose')}</option>
          {choices.map((c) => <option key={c} value={c}>{optionLabel(path, c)}</option>)}
        </select>
      ) : (
        <input
          id={id}
          className="field-input mono-input"
          value={text}
          placeholder={schema.format === 'date-time' ? t('core.form.dateTimeHint') : undefined}
          onChange={(e) => onChange(e.target.value || undefined)}
        />
      )}
      {error && <div className="field-error">{t(`core.form.errors.${error}`)}</div>}
    </div>
  );
}

function ArrayField({ path, schema, value, onChange, label, optionLabel, errorsByPath }: FieldProps & { errorsByPath: Map<string, FieldErrorCode> }) {
  const { t } = useTranslation();
  const rows = Array.isArray(value) ? (value as FormValue[]) : [];
  const item = schema.items ?? {};
  const columns = Object.entries(item.properties ?? {});
  const full = schema.maxItems !== undefined && rows.length >= schema.maxItems;
  const update = (i: number, key: string, v: unknown) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, [key]: v } : r)));
  return (
    <div className="field-row">
      <div className="field-label">{label(path, schema)}</div>
      {rows.length === 0 && <p className="findings-empty">{t('core.form.emptyList')}</p>}
      {rows.map((row, i) => (
        <div key={i} className="sf-row">
          <div className="sf-fields">
          {columns.map(([key, raw]) => (
            <ScalarField
              key={key}
              path={`${path}.${key}`}
              schema={unwrap(raw)}
              value={row[key]}
              onChange={(v) => update(i, key, v)}
              label={label}
              optionLabel={optionLabel}
              error={errorsByPath.get(`${path}.${i}.${key}`)}
            />
          ))}
          </div>
          <button type="button" className="sf-remove" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            {t('core.form.remove')}
          </button>
        </div>
      ))}
      <button type="button" className="advanced-toggle" disabled={full} onClick={() => onChange([...rows, {}])}>
        {t('core.form.add')}
      </button>
    </div>
  );
}

interface SchemaFormProps {
  schema: JsonSchema;
  value: FormValue;
  onChange: (value: FormValue) => void;
  /** Rotulo do campo no idioma da UI (o shell traduz os campos do produto). */
  label?: Labeler;
  /** Rotulo de cada opcao de `enum` (default: o proprio valor). */
  optionLabel?: OptionLabeler;
  /** Campos que o shell preenche por outro controle. */
  hidden?: string[];
  errors?: FieldError[];
}

export function SchemaForm({ schema, value, onChange, label, optionLabel, hidden = [], errors = [] }: SchemaFormProps) {
  const errorsByPath = new Map(errors.map((e) => [e.path, e.code]));
  const labeler: Labeler = label ?? ((path, s) => s.title ?? path);
  const optioner: OptionLabeler = optionLabel ?? ((_path, option) => option);
  return (
    <div className="schema-form">
      {Object.entries(schema.properties ?? {}).map(([key, raw]) => {
        const s = unwrap(raw);
        if (s.const !== undefined || hidden.includes(key)) return null;
        const common = {
          path: key,
          schema: s,
          value: value[key],
          onChange: (v: unknown) => onChange({ ...value, [key]: v }),
          label: labeler,
          optionLabel: optioner,
          error: errorsByPath.get(key),
        };
        return s.type === 'array' && s.items?.type === 'object' ? (
          <ArrayField key={key} {...common} errorsByPath={errorsByPath} />
        ) : (
          <ScalarField key={key} {...common} />
        );
      })}
    </div>
  );
}

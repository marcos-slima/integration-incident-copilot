// DA-73: seletor de idioma. A escolha grava o cookie `iic_locale`, lido
// tambem pela Admin UI - trocar aqui vale para as duas interfaces.
import { useTranslation } from 'react-i18next';
import { LOCALE_LABELS, SUPPORTED_LOCALES, setLocale, type Locale } from '../i18n';

export function LanguageSelect({ compact = false }: { compact?: boolean }) {
  const { t, i18n } = useTranslation();
  const current = (i18n.resolvedLanguage || 'en') as Locale;
  return (
    <label className={compact ? 'lang-select compact' : 'lang-select'}>
      <span className="lang-select-label">{t('common.language')}</span>
      <select
        aria-label={t('common.language')}
        value={SUPPORTED_LOCALES.includes(current) ? current : 'en'}
        onChange={(e) => setLocale(e.target.value as Locale)}
      >
        {SUPPORTED_LOCALES.map((l) => (
          <option key={l} value={l}>{LOCALE_LABELS[l]}</option>
        ))}
      </select>
    </label>
  );
}

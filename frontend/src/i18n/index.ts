// DA-73: i18n da UI web. Idioma = escolha explicita (cookie `iic_locale`,
// compartilhado com a Admin UI) -> idioma do navegador -> fallback `en`.
// `?lng=xx` (ou cookie xx) liga o pseudo-locale de revisao visual: todo
// texto do catalogo aparece entre ⟦ ⟧ - o que ficar sem marca e texto fixo.

import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';
import en from './locales/en.json';
import ptBR from './locales/pt-BR.json';

export const LOCALE_COOKIE = 'iic_locale';
export const SUPPORTED_LOCALES = ['pt-BR', 'en'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const LOCALE_LABELS: Record<Locale, string> = { 'pt-BR': 'Português (BR)', en: 'English' }; // i18n-ignore: nome de cada idioma no proprio idioma

type Catalog = { [key: string]: string | Catalog };

function pseudo(catalog: Catalog): Catalog {
  const out: Catalog = {};
  for (const [k, v] of Object.entries(catalog)) {
    out[k] = typeof v === 'string' ? `⟦${v}⟧` : pseudo(v);
  }
  return out;
}

// pt-PT -> pt-BR, en-GB -> en: casa pelo idioma base (D5 do plano)
export function normalizeLocale(lng: string | undefined): string {
  const l = (lng || '').toLowerCase();
  if (l === 'xx') return 'xx';
  if (l.startsWith('pt')) return 'pt-BR';
  if (l.startsWith('en')) return 'en';
  return l;
}

void i18n
  .use(LanguageDetector)
  .use(initReactI18next)
  .init({
    resources: {
      'pt-BR': { translation: ptBR },
      en: { translation: en },
      xx: { translation: pseudo(en as Catalog) },
    },
    supportedLngs: [...SUPPORTED_LOCALES, 'xx'],
    fallbackLng: 'en',
    interpolation: { escapeValue: false }, // React ja escapa
    detection: {
      order: ['querystring', 'cookie', 'navigator'],
      lookupQuerystring: 'lng',
      lookupCookie: LOCALE_COOKIE,
      caches: [], // so a escolha explicita grava o cookie (setLocale)
      convertDetectedLanguage: normalizeLocale,
    },
    returnNull: false,
  });

function syncHtmlLang(lng: string) {
  document.documentElement.lang = lng === 'xx' ? 'en' : lng;
}
syncHtmlLang(i18n.resolvedLanguage || 'en');
i18n.on('languageChanged', syncHtmlLang);

/** Escolha explicita do usuario: grava o cookie (1 ano, todo o site). */
export function setLocale(lng: Locale) {
  document.cookie = `${LOCALE_COOKIE}=${lng}; path=/; max-age=31536000; SameSite=Lax`;
  void i18n.changeLanguage(lng);
}

/** DA-74: idioma pedido ao backend para o CONTEUDO do diagnostico. O
 * pseudo-locale e so de revisao de tela: o diagnostico vem em `en`. */
export function diagnosisLocale(lng: string | undefined = i18n.resolvedLanguage): Locale {
  const l = normalizeLocale(lng);
  return (SUPPORTED_LOCALES as readonly string[]).includes(l) ? (l as Locale) : 'en';
}

export default i18n;

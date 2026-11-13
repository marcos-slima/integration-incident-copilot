// DA-73 + DA-95: i18n compartilhado. Idioma = escolha explicita (cookie
// `iic_locale`, o mesmo da Admin UI) -> idioma do navegador -> `en`.
// `?lng=xx` liga o pseudo-locale de revisao visual (texto entre ⟦ ⟧).
//
// Cada shell chama `initI18n(catalogo_do_produto)`. Os textos do core ficam
// sob a chave `core.*`; um produto nao pode definir `core` no proprio catalogo.

import i18n from 'i18next';
import LanguageDetector from 'i18next-browser-languagedetector';
import { initReactI18next } from 'react-i18next';
import coreEn from './locales/en.json';
import corePtBR from './locales/pt-BR.json';

export const LOCALE_COOKIE = 'iic_locale';
export const SUPPORTED_LOCALES = ['pt-BR', 'en'] as const;
export type Locale = (typeof SUPPORTED_LOCALES)[number];
export const LOCALE_LABELS: Record<Locale, string> = { 'pt-BR': 'Português (BR)', en: 'English' }; // i18n-ignore: nome de cada idioma no proprio idioma

export type Catalog = { [key: string]: string | Catalog };
export type ProductCatalogs = Record<Locale, Catalog>;

function pseudo(catalog: Catalog): Catalog {
  const out: Catalog = {};
  for (const [k, v] of Object.entries(catalog)) {
    out[k] = typeof v === 'string' ? `⟦${v}⟧` : pseudo(v);
  }
  return out;
}

// pt-PT -> pt-BR, en-GB -> en: casa pelo idioma base
export function normalizeLocale(lng: string | undefined): string {
  const l = (lng || '').toLowerCase();
  if (l === 'xx') return 'xx';
  if (l.startsWith('pt')) return 'pt-BR';
  if (l.startsWith('en')) return 'en';
  return l;
}

function syncHtmlLang(lng: string) {
  document.documentElement.lang = lng === 'xx' ? 'en' : lng;
}

/** Inicializa o i18n com o catalogo do produto + o do core (`core.*`). */
export function initI18n(product: ProductCatalogs): typeof i18n {
  for (const loc of SUPPORTED_LOCALES) {
    if ('core' in product[loc]) throw new Error(`catalogo do produto (${loc}) nao pode definir 'core'`);
  }
  const en = { ...product.en, core: coreEn as Catalog };
  void i18n
    .use(LanguageDetector)
    .use(initReactI18next)
    .init({
      resources: {
        'pt-BR': { translation: { ...product['pt-BR'], core: corePtBR as Catalog } },
        en: { translation: en },
        xx: { translation: pseudo(en) },
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
  syncHtmlLang(i18n.resolvedLanguage || 'en');
  i18n.on('languageChanged', syncHtmlLang);
  return i18n;
}

/** Escolha explicita do usuario: grava o cookie (1 ano, todo o site). */
export function setLocale(lng: Locale) {
  document.cookie = `${LOCALE_COOKIE}=${lng}; path=/; max-age=31536000; SameSite=Lax`;
  void i18n.changeLanguage(lng);
}

/** DA-74: idioma do CONTEUDO do diagnostico. O pseudo-locale pede `en`. */
export function diagnosisLocale(lng: string | undefined = i18n.resolvedLanguage): Locale {
  const l = normalizeLocale(lng);
  return (SUPPORTED_LOCALES as readonly string[]).includes(l) ? (l as Locale) : 'en';
}

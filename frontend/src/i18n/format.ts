// DA-73: formatacao sempre pelo locale resolvido (Intl), nunca fixa.
import i18n from '.';

function locale(): string {
  const l = i18n.resolvedLanguage || 'en';
  return l === 'xx' ? 'en' : l;
}

export function formatTime(d: Date): string {
  return new Intl.DateTimeFormat(locale(), { timeStyle: 'medium' }).format(d);
}

export function formatNumber(n: number, maxDigits = 2): string {
  return new Intl.NumberFormat(locale(), { maximumFractionDigits: maxDigits }).format(n);
}

export function formatPercent(ratio: number): string {
  return new Intl.NumberFormat(locale(), { style: 'percent', maximumFractionDigits: 0 }).format(ratio);
}

export function formatUSD(n: number, digits = 2): string {
  return new Intl.NumberFormat(locale(), {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(n);
}

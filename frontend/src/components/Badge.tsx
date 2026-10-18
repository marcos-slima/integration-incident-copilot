import { useTranslation } from 'react-i18next';
import { formatPercent } from '../i18n/format';

// Badge de confiança — exibe Alta/Média/Baixa com cor correspondente.
//
// CONCEITO: Props com interface TypeScript
// Em JavaScript (index.html): function Badge({value}) { ... }
// Em TypeScript: você define uma interface para as props —
// o TypeScript sabe exatamente o que o componente aceita.

interface BadgeProps {
  value: number; // 0.0 a 1.0
}

// Retorna informações de exibição baseadas no valor de confiança
function getConfidenceInfo(value: number): {
  labelKey: string;
  colorClass: string;
} {
  if (value >= 0.7) return { labelKey: 'badge.high', colorClass: 'badge-high' };
  if (value >= 0.4) return { labelKey: 'badge.mid',  colorClass: 'badge-mid'  };
  return               { labelKey: 'badge.low',  colorClass: 'badge-low'  };
}

// O componente recebe BadgeProps e retorna JSX (React.JSX.Element)
export function Badge({ value }: BadgeProps) {
  const { t } = useTranslation();
  const { labelKey, colorClass } = getConfidenceInfo(value);

  return (
    <span className={`badge ${colorClass}`}>
      <span className="badge-dot" />
      {t('badge.value', { label: t(labelKey), pct: formatPercent(value) })}
    </span>
  );
}

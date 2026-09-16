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
  label: string;
  colorClass: string;
} {
  if (value >= 0.7) return { label: 'Alta',  colorClass: 'badge-high' };
  if (value >= 0.4) return { label: 'Média', colorClass: 'badge-mid'  };
  return               { label: 'Baixa', colorClass: 'badge-low'  };
}

// O componente recebe BadgeProps e retorna JSX (React.JSX.Element)
export function Badge({ value }: BadgeProps) {
  const pct = Math.round(value * 100);
  const { label, colorClass } = getConfidenceInfo(value);

  return (
    <span className={`badge ${colorClass}`}>
      <span className="badge-dot" />
      {label} — {pct}%
    </span>
  );
}

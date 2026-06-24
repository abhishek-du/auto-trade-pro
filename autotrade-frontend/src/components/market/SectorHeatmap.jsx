const SECTORS = [
  { label: 'IT',     symbol: '^CNXIT'     },
  { label: 'Bank',   symbol: '^NSEBANK'   },
  { label: 'Pharma', symbol: '^CNXPHARMA' },
  { label: 'Auto',   symbol: '^CNXAUTO'   },
  { label: 'FMCG',   symbol: '^CNXFMCG'   },
  { label: 'Metal',  symbol: '^CNXMETAL'  },
  { label: 'Energy', symbol: '^CNXENERGY' },
  { label: 'Infra',  symbol: '^CNXINFRA'  },
  { label: 'Realty', symbol: '^CNXREALTY' },
];

function sectorBg(pct) {
  if (pct == null) return 'rgba(30,45,69,0.6)';
  if (pct >=  2)   return 'rgba(16,185,129,0.75)';
  if (pct >   0)   return 'rgba(16,185,129,0.35)';
  if (pct === 0)   return 'rgba(78,98,128,0.5)';
  if (pct > -2)    return 'rgba(244,63,94,0.35)';
  return             'rgba(244,63,94,0.75)';
}

function sectorText(pct) {
  if (pct == null) return 'text-muted';
  if (pct >=  2)   return 'text-white';
  if (pct >   0)   return 'text-profit';
  if (pct === 0)   return 'text-muted';
  if (pct > -2)    return 'text-loss';
  return             'text-white';
}

export default function SectorHeatmap({ prices }) {
  return (
    <div className="glass-panel border border-border rounded-xl p-4 space-y-3">
      <p className="text-slate-200 text-sm font-semibold">Sector Heatmap</p>
      <div className="grid grid-cols-3 gap-2">
        {SECTORS.map(({ label, symbol }) => {
          const d   = prices?.[symbol];
          const pct = d?.change_pct ?? null;
          return (
            <div
              key={symbol}
              className="flex flex-col items-center justify-center rounded-lg py-3 px-2 transition-colors"
              style={{ background: sectorBg(pct) }}
            >
              <span className={`text-xs font-bold ${sectorText(pct)}`}>{label}</span>
              <span className={`text-[11px] tabular-nums mt-0.5 font-semibold ${sectorText(pct)}`}>
                {pct != null ? `${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%` : 'N/A'}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

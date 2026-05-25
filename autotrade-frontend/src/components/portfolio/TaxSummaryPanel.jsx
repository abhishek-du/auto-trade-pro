import { Shield, AlertTriangle, Lightbulb } from 'lucide-react'
import { formatINR } from '../../utils/indianFormat'

function TaxCard({ label, gains, tax, rate, color, border }) {
  return (
    <div className="p-4 rounded-xl border" style={{ borderColor: border, background: `${color}08` }}>
      <p className="text-xs font-semibold uppercase tracking-wider mb-3" style={{ color }}>{label}</p>
      <div className="space-y-2 text-sm">
        <div className="flex justify-between">
          <span className="text-muted">Realised Gains</span>
          <span className={`font-semibold ${gains >= 0 ? 'text-profit' : 'text-loss'}`}>{formatINR(gains)}</span>
        </div>
        <div className="flex justify-between">
          <span className="text-muted">Tax Rate</span>
          <span className="text-slate-300">{rate}</span>
        </div>
        <div className="flex justify-between border-t border-border/60 pt-2">
          <span className="text-muted font-semibold">Tax Payable</span>
          <span className="text-warn font-bold text-base">{formatINR(tax)}</span>
        </div>
      </div>
    </div>
  )
}

export default function TaxSummaryPanel({ tax }) {
  if (!tax) return null

  const { stcg_gains, ltcg_gains, stcg_tax, ltcg_tax, total_tax, ltcg_exempt, ltcg_taxable } = tax

  const tlhCandidates = ltcg_gains < 0 || stcg_gains < 0

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <TaxCard
          label="Short-Term Capital Gains (< 1 yr)"
          gains={stcg_gains}
          tax={stcg_tax}
          rate="20%"
          color="#f59e0b"
          border="rgba(245,158,11,0.3)"
        />
        <TaxCard
          label="Long-Term Capital Gains (≥ 1 yr)"
          gains={ltcg_gains}
          tax={ltcg_tax}
          rate="12.5% above ₹1.25 L"
          color="#06b6d4"
          border="rgba(6,182,212,0.3)"
        />
      </div>

      {/* LTCG exemption detail */}
      {ltcg_gains > 0 && (
        <div className="p-3 rounded-lg bg-cyan/5 border border-cyan/20 text-xs space-y-1">
          <p className="text-cyan font-semibold">LTCG Exemption Breakdown</p>
          <div className="flex justify-between text-muted">
            <span>Total LTCG gains</span><span className="text-slate-300">{formatINR(ltcg_gains)}</span>
          </div>
          <div className="flex justify-between text-muted">
            <span>Exempt (Section 112A)</span><span className="text-profit">–{formatINR(ltcg_exempt)}</span>
          </div>
          <div className="flex justify-between text-muted border-t border-border/60 pt-1">
            <span>Taxable LTCG</span><span className="text-warn">{formatINR(ltcg_taxable)}</span>
          </div>
        </div>
      )}

      {/* Total */}
      <div className="flex items-center justify-between p-4 rounded-xl border border-warn/30 bg-warn/5">
        <div className="flex items-center gap-2">
          <Shield size={16} className="text-warn" />
          <span className="text-sm font-semibold text-slate-200">Total Estimated Tax</span>
        </div>
        <span className="text-xl font-bold text-warn">{formatINR(total_tax)}</span>
      </div>

      {/* Tax loss harvesting hint */}
      {tlhCandidates && (
        <div className="flex gap-2 p-3 rounded-lg bg-purple-500/5 border border-purple-500/20">
          <Lightbulb size={14} className="text-purple-400 shrink-0 mt-0.5" />
          <p className="text-xs text-purple-300">
            You have unrealised losses. Consider tax-loss harvesting — selling loss-making positions before March 31 can offset your gains and reduce tax.
          </p>
        </div>
      )}

      <p className="text-[10px] text-muted/60 text-center">
        Estimates based on realised gains only. Consult a CA for final tax filing. STT (0.1%) is not included.
      </p>
    </div>
  )
}

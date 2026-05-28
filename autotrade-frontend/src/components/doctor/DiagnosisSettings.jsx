export default function DiagnosisSettings({ riskProfile, setRiskProfile, annualIncome, setAnnualIncome }) {
  return (
    <div className="flex items-center gap-3 flex-wrap">
      <div className="flex items-center gap-2">
        <label className="text-muted text-xs whitespace-nowrap">Risk Profile</label>
        <select
          value={riskProfile}
          onChange={e => setRiskProfile(e.target.value)}
          className="text-xs bg-surface border border-border rounded-lg px-2 py-1.5 text-slate-200 focus:outline-none focus:border-accent/50"
        >
          <option value="conservative">Conservative</option>
          <option value="moderate_conservative">Moderate Conservative</option>
          <option value="moderate">Moderate</option>
          <option value="moderate_aggressive">Moderate Aggressive</option>
          <option value="aggressive">Aggressive</option>
          <option value="very_aggressive">Very Aggressive</option>
        </select>
      </div>
      <div className="flex items-center gap-2">
        <label className="text-muted text-xs whitespace-nowrap">Annual Income</label>
        <div className="flex items-center gap-1">
          <span className="text-muted text-xs">₹</span>
          <input
            type="number"
            value={annualIncome}
            onChange={e => setAnnualIncome(Number(e.target.value))}
            className="w-28 text-xs bg-surface border border-border rounded-lg px-2 py-1.5 text-slate-200 focus:outline-none focus:border-accent/50"
            step={100000}
            min={0}
          />
        </div>
      </div>
    </div>
  )
}

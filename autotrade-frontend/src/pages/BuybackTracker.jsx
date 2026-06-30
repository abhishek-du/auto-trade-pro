import { useState, useEffect, useCallback } from 'react';
import { apiFetch } from '../api/client';
import {
  TrendingUp, RefreshCw, AlertCircle, ArrowUpRight, Clock,
  Info, AlertTriangle, CheckCircle2, XCircle, ChevronDown, ChevronUp,
  CalendarDays, IndianRupee, Smartphone, ReceiptText,
} from 'lucide-react';

function fmt(n) {
  if (n == null) return '—';
  return '₹' + new Intl.NumberFormat('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n);
}

function fmtCr(n) {
  if (n == null) return '—';
  return '₹' + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(n) + ' Cr';
}

function StatusBadge({ status }) {
  const cfg = {
    OPEN:     { cls: 'bg-emerald-500/15 text-emerald-400 border-emerald-500/30', label: 'Open' },
    UPCOMING: { cls: 'bg-blue-500/15 text-blue-400 border-blue-500/30',         label: 'Upcoming' },
    CLOSED:   { cls: 'bg-slate-500/15 text-slate-400 border-slate-600',          label: 'Closed' },
  }[status] ?? { cls: 'bg-slate-700/30 text-slate-400 border-slate-600', label: status };
  return (
    <span className={`text-[11px] font-semibold px-2 py-0.5 rounded-full border ${cfg.cls}`}>
      {cfg.label}
    </span>
  );
}

function TypeBadge({ type }) {
  const isOpen = type === 'OPEN_MARKET';
  return (
    <span className={`text-[10px] font-semibold px-1.5 py-0.5 rounded border ${
      isOpen
        ? 'bg-purple-500/15 text-purple-400 border-purple-500/30'
        : 'bg-amber-500/15 text-amber-400 border-amber-500/30'
    }`}>
      {isOpen ? 'Open Market' : 'Tender Offer'}
    </span>
  );
}

function SpreadBar({ pct }) {
  if (pct == null) return <span className="text-slate-500 text-sm">—</span>;
  const positive = pct >= 0;
  const width = Math.min(Math.abs(pct), 100);
  return (
    <div className="flex items-center gap-2">
      <span className={`font-bold text-sm tabular-nums ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
        {positive ? '+' : ''}{pct.toFixed(2)}%
      </span>
      <div className="flex-1 h-1.5 bg-slate-700 rounded-full overflow-hidden max-w-24">
        <div
          className={`h-full rounded-full transition-all ${positive ? 'bg-emerald-500' : 'bg-red-500'}`}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}

// ── Eligibility helpers ────────────────────────────────────────────────────────

function todayStr() {
  return new Date().toISOString().slice(0, 10);
}

function eligibilityStatus(b) {
  const today = todayStr();
  const rec   = b.record_date;
  const open  = b.open_date;
  const close = b.close_date;

  if (rec && rec > today)  return 'eligible_upcoming';   // record date is in future → can still buy
  if (rec && rec <= today) return 'record_passed';        // must have already held shares
  return 'unknown';
}

function tenderWindowStatus(b) {
  const today = todayStr();
  const open  = b.open_date;
  const close = b.close_date;
  if (!open) return 'unknown';
  if (open > today)  return 'not_open_yet';
  if (close && close < today) return 'closed';
  return 'open_now';  // between open and close dates
}

// ── Timeline component ─────────────────────────────────────────────────────────

function Timeline({ b }) {
  const today = todayStr();
  const steps = [
    {
      label: 'Board Announcement',
      desc:  'Company's board approves buyback price, size, and schedule',
      date:  null,
      done:  true,
    },
    {
      label: 'Record Date',
      desc:  'Aapke demat account mein shares hone chahiye ISKE DIN YA ISSE PEHLE. Jo us din hold karta hai woh tender kar sakta hai.',
      date:  b.record_date,
      done:  b.record_date ? b.record_date <= today : false,
    },
    {
      label: 'Tender Window Opens',
      desc:  'Broker app → Corporate Actions / IPO & Buyback → Buyback → Quantity enter karke submit karo',
      date:  b.open_date,
      done:  b.open_date ? b.open_date <= today : false,
    },
    {
      label: 'Tender Window Closes',
      desc:  'Is date ke baad application accept nahi hogi. Last date par bhi apply ho sakta hai.',
      date:  b.close_date,
      done:  b.close_date ? b.close_date < today : false,
    },
    {
      label: 'Acceptance & Settlement',
      desc:  'Company accepted shares ka paisa deti hai (~T+7 after close). Baaki shares demat mein wapas aate hain.',
      date:  null,
      done:  false,
    },
  ];

  return (
    <div className="relative pl-5">
      {/* vertical line */}
      <div className="absolute left-[9px] top-2 bottom-2 w-px bg-slate-700" />
      <div className="space-y-4">
        {steps.map((s, i) => (
          <div key={i} className="flex gap-3 items-start">
            <div className={`relative z-10 mt-0.5 w-[18px] h-[18px] rounded-full border-2 flex items-center justify-center shrink-0 ${
              s.done
                ? 'bg-emerald-500 border-emerald-500'
                : 'bg-[#080D1A] border-slate-600'
            }`}>
              {s.done && <CheckCircle2 size={10} className="text-white" />}
            </div>
            <div className="pb-1">
              <p className={`text-xs font-semibold ${s.done ? 'text-emerald-400' : 'text-slate-200'}`}>
                {s.label}
                {s.date && (
                  <span className={`ml-2 font-normal ${s.done ? 'text-emerald-600' : 'text-slate-400'}`}>
                    {s.date}
                  </span>
                )}
              </p>
              <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">{s.desc}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── How to Apply panel ────────────────────────────────────────────────────────

function HowToApply({ b }) {
  const elig   = eligibilityStatus(b);
  const window = tenderWindowStatus(b);
  const today  = todayStr();

  const brokers = [
    { name: 'Zerodha',  path: 'Console (console.zerodha.com) → Portfolio → Corporate Actions → Buyback' },
    { name: 'Groww',    path: 'App → Investments → Corporate Actions → Buyback' },
    { name: 'AngelOne', path: 'App → IPO → Buyback / OFS tab' },
    { name: 'Upstox',   path: 'App → Portfolio → Corporate Actions' },
    { name: 'ICICI',    path: 'ICICIDirect → IPO → Buyback' },
  ];

  return (
    <div className="mt-4 space-y-3 text-xs">
      {/* Eligibility banner */}
      {elig === 'record_passed' ? (
        <div className="flex items-start gap-2 p-3 bg-amber-500/10 border border-amber-500/25 rounded-lg text-amber-300">
          <AlertTriangle size={13} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Record Date {b.record_date} — Already Passed</p>
            <p className="text-amber-400/70 mt-0.5">
              Aapko us date par ya usse pehle shares hold karne the. Ab naye buyers apply nahi kar sakte.
              Agar aapke paas pehle se shares hain, toh tender window mein apply kar sakte hain.
            </p>
          </div>
        </div>
      ) : elig === 'eligible_upcoming' ? (
        <div className="flex items-start gap-2 p-3 bg-emerald-500/10 border border-emerald-500/25 rounded-lg text-emerald-300">
          <CheckCircle2 size={13} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Record Date {b.record_date} — Abhi Tak Nahi Aaya</p>
            <p className="text-emerald-400/70 mt-0.5">
              Aap abhi shares khareedo aur {b.record_date} tak hold karo — tum eligible ho jaoge tender ke liye.
            </p>
          </div>
        </div>
      ) : null}

      {/* Tender window status */}
      {window === 'open_now' && (
        <div className="flex items-start gap-2 p-3 bg-blue-500/10 border border-blue-500/25 rounded-lg text-blue-300">
          <Smartphone size={13} className="shrink-0 mt-0.5" />
          <div>
            <p className="font-semibold">Tender Window OPEN — Apply Karo Abhi ({b.open_date} – {b.close_date})</p>
            <p className="text-blue-400/70 mt-0.5">
              Agar aapke paas shares hain, neeche apne broker ke steps follow karo aur apply karo.
            </p>
          </div>
        </div>
      )}
      {window === 'not_open_yet' && (
        <div className="flex items-start gap-2 p-3 bg-slate-700/40 border border-slate-600/40 rounded-lg text-slate-300">
          <Clock size={13} className="shrink-0 mt-0.5" />
          <p>Tender window {b.open_date} ko open hogi. Tabh broker app mein Corporate Actions section mein dikhega.</p>
        </div>
      )}

      {/* Step by step */}
      <div className="bg-[#080D1A] rounded-lg p-3 space-y-3">
        <p className="text-slate-200 font-semibold flex items-center gap-1.5">
          <ReceiptText size={12} className="text-indigo-400" /> Step-by-Step: Kaise Apply Karein
        </p>
        <ol className="space-y-2 text-slate-400">
          {[
            `Record Date (${b.record_date || '—'}) par ya pehle shares apne demat mein rakho`,
            `Tender window open hone par apne broker app mein jaao`,
            `Corporate Actions / IPO & Buyback section dhundo`,
            `"${b.company_name}" ka buyback select karo`,
            `Kitne shares tender karne hain woh quantity enter karo`,
            `Submit karo — confirmation mil jaega`,
            `Company payment karti hai ~7-10 din baad. Baaki shares demat mein wapas`,
          ].map((step, i) => (
            <li key={i} className="flex gap-2">
              <span className="w-4 h-4 rounded-full bg-indigo-600/30 text-indigo-400 text-[10px] font-bold flex items-center justify-center shrink-0 mt-0.5">{i + 1}</span>
              <span>{step}</span>
            </li>
          ))}
        </ol>
      </div>

      {/* Broker paths */}
      <div className="bg-[#080D1A] rounded-lg p-3 space-y-2">
        <p className="text-slate-200 font-semibold flex items-center gap-1.5">
          <Smartphone size={12} className="text-purple-400" /> Broker-wise: Kahan Milega Yeh Option
        </p>
        <div className="space-y-1.5">
          {brokers.map(br => (
            <div key={br.name} className="flex gap-2">
              <span className="text-slate-300 font-medium w-20 shrink-0">{br.name}</span>
              <span className="text-slate-500">{br.path}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Tax note */}
      <div className="flex items-start gap-2 p-3 bg-red-500/5 border border-red-500/20 rounded-lg text-red-300/80">
        <IndianRupee size={12} className="shrink-0 mt-0.5" />
        <p>
          <strong className="text-red-300">Tax Alert (Oct 2024+):</strong> Buyback se milne wala paisa ab
          "Deemed Dividend" hai — aapke income tax slab ke hisab se tax lagega (capital gains nahi).
          30% slab mein hain toh net profit significantly kam ho jaega. Accountant se confirm karo.
        </p>
      </div>
    </div>
  );
}

// ── Main card ─────────────────────────────────────────────────────────────────

function BuybackCard({ b }) {
  const [expanded, setExpanded] = useState(false);
  const hasPrice      = b.buyback_price > 0;
  const isOpportunity = b.opportunity && b.market_price && hasPrice;

  return (
    <div className={`relative bg-[#0F1629] border rounded-xl p-5 transition-all ${
      isOpportunity
        ? 'border-emerald-500/40 shadow-[0_0_20px_rgba(16,185,129,0.06)]'
        : 'border-slate-700/50'
    }`}>
      {isOpportunity && (
        <div className="absolute -top-2.5 left-4">
          <span className="bg-emerald-500 text-white text-[10px] font-bold px-2 py-0.5 rounded-full flex items-center gap-1">
            <ArrowUpRight size={10} /> ARBITRAGE OPPORTUNITY
          </span>
        </div>
      )}

      {/* Header */}
      <div className="flex items-start justify-between gap-3 mb-4">
        <div>
          <div className="flex items-center gap-2 mb-1">
            <span className="text-slate-100 font-bold text-base">
              {b.symbol.replace('.NS', '').replace('.BO', '')}
            </span>
            <StatusBadge status={b.status} />
            <TypeBadge type={b.buyback_type} />
          </div>
          <p className="text-slate-400 text-xs">{b.company_name}</p>
        </div>
        {b.total_size_cr && (
          <div className="text-right shrink-0">
            <p className="text-slate-300 font-semibold text-sm">{fmtCr(b.total_size_cr)}</p>
            <p className="text-slate-500 text-[10px]">total size</p>
          </div>
        )}
      </div>

      {/* Price comparison */}
      <div className="grid grid-cols-3 gap-3 mb-4">
        <div className="bg-[#080D1A] rounded-lg p-3 text-center">
          <p className="text-slate-400 text-[10px] uppercase tracking-wide mb-1">Buyback Price</p>
          <p className="text-white font-bold text-lg">{hasPrice ? fmt(b.buyback_price) : '—'}</p>
        </div>
        <div className="bg-[#080D1A] rounded-lg p-3 text-center">
          <p className="text-slate-400 text-[10px] uppercase tracking-wide mb-1">Market Price</p>
          <p className={`font-bold text-lg ${b.market_price ? 'text-white' : 'text-slate-500'}`}>
            {b.market_price ? fmt(b.market_price) : 'N/A'}
          </p>
        </div>
        <div className={`rounded-lg p-3 text-center ${isOpportunity ? 'bg-emerald-500/10' : 'bg-[#080D1A]'}`}>
          <p className="text-slate-400 text-[10px] uppercase tracking-wide mb-1">Spread</p>
          <div className="flex justify-center">
            {b.spread_pct != null ? <SpreadBar pct={b.spread_pct} /> : <span className="text-slate-500 text-sm">—</span>}
          </div>
        </div>
      </div>

      {/* Dates timeline row */}
      <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-slate-500 mb-3">
        {b.record_date && (
          <span className="flex items-center gap-1">
            <CalendarDays size={11} className="text-amber-500" />
            Record: <span className="text-amber-300 font-medium ml-0.5">{b.record_date}</span>
          </span>
        )}
        {b.open_date && (
          <span className="flex items-center gap-1">
            <CalendarDays size={11} className="text-blue-500" />
            Tender Open: <span className="text-blue-300 font-medium ml-0.5">{b.open_date}</span>
          </span>
        )}
        {b.close_date && (
          <span className="flex items-center gap-1">
            <CalendarDays size={11} className="text-slate-500" />
            Close: <span className="text-slate-300 font-medium ml-0.5">{b.close_date}</span>
          </span>
        )}
        {b.last_refreshed && (
          <span className="flex items-center gap-1 ml-auto text-slate-600">
            <Clock size={10} /> {new Date(b.last_refreshed).toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
          </span>
        )}
      </div>

      {isOpportunity && (
        <div className="mb-3 p-3 bg-emerald-500/5 border border-emerald-500/20 rounded-lg text-xs text-emerald-300">
          <strong>Spread:</strong> Market ({fmt(b.market_price)}) vs buyback ({fmt(b.buyback_price)}) →{' '}
          <strong>{fmt(b.buyback_price - b.market_price)}/share (+{b.spread_pct?.toFixed(2)}%)</strong> — subject to acceptance ratio &amp; tax.
        </div>
      )}

      {/* Expand/collapse toggle */}
      <button
        onClick={() => setExpanded(v => !v)}
        className="w-full flex items-center justify-center gap-1.5 py-1.5 text-xs text-slate-500 hover:text-slate-300 border-t border-slate-700/50 mt-1 transition"
      >
        {expanded ? <ChevronUp size={13} /> : <ChevronDown size={13} />}
        {expanded ? 'Hide Guide' : 'How to Apply + Full Timeline'}
      </button>

      {expanded && (
        <div className="mt-3 space-y-4">
          <div>
            <p className="text-slate-400 text-xs font-semibold mb-2 uppercase tracking-wide">Process Timeline</p>
            <Timeline b={b} />
          </div>
          <HowToApply b={b} />
        </div>
      )}
    </div>
  );
}

function RiskWarning() {
  return (
    <div className="bg-[#0F1629] border border-amber-500/25 rounded-xl p-5 space-y-4">
      <h3 className="text-amber-400 font-semibold text-sm flex items-center gap-2">
        <AlertTriangle size={15} /> Buyback Arbitrage — Know the Risks
      </h3>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4 text-xs">
        {/* Acceptance Ratio */}
        <div className="bg-[#080D1A] rounded-lg p-3 space-y-1.5">
          <p className="text-slate-200 font-semibold flex items-center gap-1.5">
            <XCircle size={12} className="text-red-400" /> Acceptance Ratio Risk
          </p>
          <p className="text-slate-400 leading-relaxed">
            Company buys only a fraction of tendered shares. Example: if AR = 34% and you tender 100 shares,
            only 34 get bought at buyback price. The remaining 66 come back to your demat —
            often at a lower market price after the buyback closes.
          </p>
        </div>

        {/* Tax */}
        <div className="bg-[#080D1A] rounded-lg p-3 space-y-1.5">
          <p className="text-slate-200 font-semibold flex items-center gap-1.5">
            <AlertCircle size={12} className="text-amber-400" /> Tax: Deemed Dividend (Oct 2024+)
          </p>
          <p className="text-slate-400 leading-relaxed">
            Post 1 Oct 2024, buyback proceeds are taxed as <strong className="text-amber-300">deemed dividend</strong> at
            your income-tax slab rate — not as capital gains. For the 30% slab, this significantly
            reduces the net arbitrage profit. Factor in tax before entering.
          </p>
        </div>

        {/* How to apply */}
        <div className="bg-[#080D1A] rounded-lg p-3 space-y-1.5">
          <p className="text-slate-200 font-semibold flex items-center gap-1.5">
            <CheckCircle2 size={12} className="text-emerald-400" /> How to Tender
          </p>
          <p className="text-slate-400 leading-relaxed">
            Hold shares in demat <strong className="text-slate-200">on or before Record Date</strong>.
            When the tender window opens (usually 5–10 days), go to your broker app →
            Corporate Actions / IPO & Buyback → submit shares before Close Date.
          </p>
        </div>
      </div>
    </div>
  );
}

function EmptyState({ onRefresh, refreshing }) {
  return (
    <div className="space-y-5">
      <div className="text-center py-14 space-y-4">
        <div className="w-14 h-14 rounded-full bg-slate-800 flex items-center justify-center mx-auto">
          <TrendingUp size={24} className="text-slate-500" />
        </div>
        <div>
          <p className="text-slate-200 font-semibold text-lg">No Active Buybacks Right Now</p>
          <p className="text-slate-500 text-sm mt-1 max-w-md mx-auto">
            NSE is not showing any open or upcoming buyback offers at this time.
            Buybacks are announced by companies at their own discretion — they don't
            run continuously.
          </p>
        </div>
        <div className="bg-[#0F1629] border border-slate-700/50 rounded-xl p-4 max-w-sm mx-auto text-left space-y-2 text-xs text-slate-400">
          <p className="text-slate-300 font-medium flex items-center gap-1.5">
            <Info size={13} /> How to watch for new buybacks
          </p>
          <ul className="space-y-1.5 list-disc list-inside">
            <li>NSE Corporate Actions → Announcements → Buyback</li>
            <li>BSE Corporate Filings (BSE India website)</li>
            <li>SEBI EDGAR for offer documents</li>
            <li>Click <strong className="text-slate-200">Refresh Data</strong> to re-check NSE</li>
          </ul>
        </div>
        <button
          onClick={onRefresh}
          disabled={refreshing}
          className="inline-flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Checking NSE…' : 'Refresh Data'}
        </button>
      </div>
      <RiskWarning />
    </div>
  );
}

export default function BuybackTracker() {
  const [data,       setData]       = useState([]);
  const [loading,    setLoading]    = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error,      setError]      = useState(null);
  const [filter,     setFilter]     = useState('ALL');
  const [lastCheck,  setLastCheck]  = useState(null);

  const load = useCallback(async () => {
    try {
      const res = await apiFetch('/api/v1/buyback/');
      setData(Array.isArray(res) ? res : []);
      setError(null);
      setLastCheck(new Date());
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const id = setInterval(load, 30_000);
    return () => clearInterval(id);
  }, [load]);

  async function handleRefresh() {
    setRefreshing(true);
    try {
      await apiFetch('/api/v1/buyback/refresh', { method: 'POST' });
      await load();
    } catch (e) {
      setError(e.message);
    } finally {
      setRefreshing(false);
    }
  }

  const filtered = data.filter(b => {
    if (filter === 'OPPORTUNITY')  return b.opportunity;
    if (filter === 'TENDER')       return b.buyback_type === 'TENDER';
    if (filter === 'OPEN_MARKET')  return b.buyback_type === 'OPEN_MARKET';
    return true;
  });

  const opportunities = data.filter(b => b.opportunity).length;

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-2">
            <TrendingUp className="text-emerald-400" size={24} />
            Buyback Tracker
          </h1>
          <p className="text-slate-400 text-sm mt-1">
            Live NSE buyback offers — spread vs current market price
            {lastCheck && (
              <span className="text-slate-600 ml-2">
                · checked {lastCheck.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit' })}
              </span>
            )}
          </p>
        </div>
        <button
          onClick={handleRefresh}
          disabled={refreshing}
          className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white text-sm font-semibold rounded-xl transition"
        >
          <RefreshCw size={14} className={refreshing ? 'animate-spin' : ''} />
          {refreshing ? 'Checking NSE…' : 'Refresh Data'}
        </button>
      </div>

      {/* Stats — only when data exists */}
      {!loading && data.length > 0 && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: 'Active Offers',  value: data.length,                                                  color: 'text-white' },
            { label: 'Opportunities',  value: opportunities,                                                 color: 'text-emerald-400' },
            { label: 'Tender Offers',  value: data.filter(b => b.buyback_type === 'TENDER').length,          color: 'text-amber-400' },
            { label: 'Open Market',    value: data.filter(b => b.buyback_type === 'OPEN_MARKET').length,     color: 'text-purple-400' },
          ].map(s => (
            <div key={s.label} className="bg-[#0F1629] border border-slate-700/50 rounded-xl p-4 text-center">
              <p className={`text-2xl font-bold ${s.color}`}>{s.value}</p>
              <p className="text-slate-400 text-xs mt-1">{s.label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Filters */}
      {data.length > 0 && (
        <div className="flex gap-2 flex-wrap">
          {[
            { key: 'ALL',         label: 'All Offers' },
            { key: 'OPPORTUNITY', label: `Arbitrage (${opportunities})` },
            { key: 'TENDER',      label: 'Tender Offer' },
            { key: 'OPEN_MARKET', label: 'Open Market' },
          ].map(f => (
            <button
              key={f.key}
              onClick={() => setFilter(f.key)}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                filter === f.key
                  ? 'bg-indigo-600 text-white'
                  : 'bg-[#0F1629] border border-slate-700/50 text-slate-400 hover:text-slate-200'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 text-red-400 bg-red-500/10 border border-red-500/20 rounded-xl p-4">
          <AlertCircle size={16} />
          {error}
        </div>
      )}

      {/* Content */}
      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2].map(i => (
            <div key={i} className="bg-[#0F1629] border border-slate-700/50 rounded-xl p-5 animate-pulse">
              <div className="h-4 bg-slate-700 rounded w-1/3 mb-3" />
              <div className="h-3 bg-slate-800 rounded w-2/3 mb-4" />
              <div className="grid grid-cols-3 gap-3">
                {[1, 2, 3].map(j => <div key={j} className="h-16 bg-slate-800 rounded-lg" />)}
              </div>
            </div>
          ))}
        </div>
      ) : data.length === 0 ? (
        <EmptyState onRefresh={handleRefresh} refreshing={refreshing} />
      ) : filtered.length === 0 ? (
        <div className="text-center py-12 text-slate-500">
          <p className="text-sm">No offers match this filter.</p>
        </div>
      ) : (
        <div className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {filtered.map(b => <BuybackCard key={b.id} b={b} />)}
          </div>
          <RiskWarning />
        </div>
      )}
    </div>
  );
}

import { ChevronDown } from 'lucide-react';
import { motion } from 'framer-motion';
import { fmt } from '../../utils/tradeFormat';

export default function SectorGroupHeader({ sector, count, totalPnl, collapsed, onToggle }) {
  const isGain = totalPnl >= 0;
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={!collapsed}
      className="w-full flex items-center justify-between px-3 py-2 rounded-lg bg-white/[0.02] hover:bg-white/[0.04] border border-border/50 transition-colors text-left"
    >
      <div className="flex items-center gap-2">
        <motion.span
          animate={{ rotate: collapsed ? -90 : 0 }}
          transition={{ duration: 0.15 }}
          className="text-muted"
        >
          <ChevronDown size={14} aria-hidden="true" />
        </motion.span>
        <span className="text-sm font-semibold text-slate-200">{sector}</span>
        <span className="text-[11px] text-muted">{count} position{count === 1 ? '' : 's'}</span>
      </div>
      <span className={`text-xs font-semibold tabular-nums ${isGain ? 'text-profit' : 'text-loss'}`}>
        {isGain ? '+' : ''}{fmt(totalPnl)}
      </span>
    </button>
  );
}

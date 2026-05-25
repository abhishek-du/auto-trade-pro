import { TrendingUp, TrendingDown, ArrowUpRight, ArrowDownRight, Minus } from 'lucide-react'

const MOOD_CONFIG = {
  STRONGLY_BULLISH: { label: 'Strongly Bullish', textCls: 'text-emerald-400', bgCls: 'bg-emerald-500/15 border-emerald-500/30', Icon: TrendingUp    },
  BULLISH:          { label: 'Bullish',           textCls: 'text-profit',      bgCls: 'bg-profit/15 border-profit/30',           Icon: ArrowUpRight  },
  NEUTRAL:          { label: 'Neutral',           textCls: 'text-slate-400',   bgCls: 'bg-slate-500/15 border-slate-500/30',     Icon: Minus         },
  BEARISH:          { label: 'Bearish',           textCls: 'text-loss',        bgCls: 'bg-loss/15 border-loss/30',               Icon: ArrowDownRight},
  STRONGLY_BEARISH: { label: 'Strongly Bearish',  textCls: 'text-red-400',     bgCls: 'bg-red-500/15 border-red-500/30',         Icon: TrendingDown  },
}

export default function MarketMoodBadge({ mood, size = 'md' }) {
  const cfg  = MOOD_CONFIG[mood] || MOOD_CONFIG.NEUTRAL
  const { label, textCls, bgCls, Icon } = cfg
  const iconSz = size === 'sm' ? 11 : 14
  const textSz = size === 'sm' ? 'text-[10px]' : 'text-xs'
  const px     = size === 'sm' ? 'px-2 py-0.5' : 'px-2.5 py-1'

  return (
    <span className={`inline-flex items-center gap-1.5 font-semibold border rounded-full ${bgCls} ${textCls} ${textSz} ${px}`}>
      <Icon size={iconSz} />
      {label}
    </span>
  )
}

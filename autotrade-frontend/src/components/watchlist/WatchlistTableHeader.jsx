import { ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react'

const COLUMNS = [
  { label: 'Stock',       field: 'name',             align: 'left',   width: '180px' },
  { label: 'Sector',      field: 'sector',           align: 'left',   width: '90px'  },
  { label: 'LTP',         field: 'price',            align: 'right',  width: '90px'  },
  { label: 'Change',      field: 'change',           align: 'right',  width: '80px'  },
  { label: 'Change %',    field: 'change_pct',       align: 'right',  width: '75px'  },
  { label: 'Volume',      field: 'volume',           align: 'right',  width: '90px'  },
  { label: 'Vol Ratio',   field: 'volume_ratio',     align: 'right',  width: '75px', title: 'Today vs 10-day avg' },
  { label: 'Day H/L',     field: 'day_range_pct',    align: 'right',  width: '110px' },
  { label: '52W Pos',     field: 'from_52w_high',    align: 'right',  width: '90px', title: '% below 52W high'   },
  { label: 'Signal',      field: 'signal_confidence',align: 'center', width: '90px'  },
  { label: '',            field: '_chart',           align: 'center', width: '40px'  },
]

function SortIcon({ field, sortBy, sortDir }) {
  if (field !== sortBy) return <ChevronsUpDown size={11} className="text-muted/50 ml-0.5" />
  return sortDir === 'asc'
    ? <ChevronUp   size={11} className="text-cyan ml-0.5" />
    : <ChevronDown size={11} className="text-cyan ml-0.5" />
}

export default function WatchlistTableHeader({ sortBy, sortDir, onSort }) {
  return (
    <thead>
      <tr className="border-b border-border glass-panel/80 sticky top-0 z-10">
        {COLUMNS.map(({ label, field, align, width, title }) => (
          <th
            key={field}
            title={title}
            onClick={() => field !== '_chart' && onSort(field)}
            style={{ minWidth: width }}
            className={[
              'px-3 py-2.5 text-muted text-[10px] font-semibold uppercase tracking-wider',
              field !== '_chart' ? 'cursor-pointer hover:text-slate-300' : '',
              'select-none whitespace-nowrap',
              align === 'right'  ? 'text-right'  :
              align === 'center' ? 'text-center' : 'text-left',
            ].join(' ')}
          >
            <span className="inline-flex items-center gap-0.5">
              {label}
              {field !== '_chart' && <SortIcon field={field} sortBy={sortBy} sortDir={sortDir} />}
            </span>
          </th>
        ))}
      </tr>
    </thead>
  )
}

import { useEffect, useMemo, useState } from 'react';
import { BookOpen, FileText } from 'lucide-react';
import LoadingSpinner from '../components/LoadingSpinner';

function renderInline(text, keyPrefix) {
  const parts = text.split(/(`[^`]+`)/g);
  return parts.map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={`${keyPrefix}-code-${index}`}
          className="px-1.5 py-0.5 rounded bg-surface border border-border text-cyan text-[0.95em]"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={`${keyPrefix}-text-${index}`}>{part}</span>;
  });
}

function slugify(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/(^-|-$)/g, '');
}

function renderMarkdown(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    if (line.startsWith('```')) {
      const lang = line.slice(3).trim();
      const code = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) {
        code.push(lines[i]);
        i += 1;
      }
      if (i < lines.length) i += 1;
      blocks.push({ type: 'code', lang, content: code.join('\n') });
      continue;
    }

    if (/^#{1,6}\s+/.test(line)) {
      const level = line.match(/^#+/)[0].length;
      blocks.push({ type: 'heading', level, content: line.replace(/^#{1,6}\s+/, '') });
      i += 1;
      continue;
    }

    if (/^- /.test(line)) {
      const items = [];
      while (i < lines.length && /^- /.test(lines[i])) {
        items.push(lines[i].replace(/^- /, ''));
        i += 1;
      }
      blocks.push({ type: 'ul', items });
      continue;
    }

    if (/^\d+\. /.test(line)) {
      const items = [];
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\. /, ''));
        i += 1;
      }
      blocks.push({ type: 'ol', items });
      continue;
    }

    if (/^> /.test(line)) {
      const quote = [];
      while (i < lines.length && /^> /.test(lines[i])) {
        quote.push(lines[i].replace(/^> /, ''));
        i += 1;
      }
      blocks.push({ type: 'quote', content: quote.join(' ') });
      continue;
    }

    const paragraph = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].startsWith('```') &&
      !/^#{1,6}\s+/.test(lines[i]) &&
      !/^- /.test(lines[i]) &&
      !/^\d+\. /.test(lines[i]) &&
      !/^> /.test(lines[i])
    ) {
      paragraph.push(lines[i]);
      i += 1;
    }
    blocks.push({ type: 'p', content: paragraph.join(' ') });
  }

  return blocks.map((block, index) => {
    if (block.type === 'heading') {
      const classMap = {
        1: 'text-3xl md:text-4xl font-extrabold text-slate-50 tracking-tight',
        2: 'text-xl md:text-2xl font-bold text-slate-100 mt-10',
        3: 'text-base md:text-lg font-semibold text-cyan mt-7',
        4: 'text-sm md:text-base font-semibold text-slate-200 mt-5 uppercase tracking-wide',
      };
      const Tag = `h${Math.min(block.level, 4)}`;
      return (
        <Tag
          key={`block-${index}`}
          id={block.level === 2 ? slugify(block.content) : undefined}
          className={classMap[Math.min(block.level, 4)]}
        >
          {block.content}
        </Tag>
      );
    }

    if (block.type === 'code') {
      return (
        <pre
          key={`block-${index}`}
          className="overflow-x-auto rounded-xl border border-border bg-surface px-4 py-4 text-xs text-slate-300"
        >
          <code>{block.content}</code>
        </pre>
      );
    }

    if (block.type === 'ul') {
      return (
        <ul key={`block-${index}`} className="space-y-2 pl-5 list-disc text-slate-300">
          {block.items.map((item, itemIndex) => (
            <li key={`ul-${index}-${itemIndex}`}>{renderInline(item, `ul-${index}-${itemIndex}`)}</li>
          ))}
        </ul>
      );
    }

    if (block.type === 'ol') {
      return (
        <ol key={`block-${index}`} className="space-y-2 pl-5 list-decimal text-slate-300">
          {block.items.map((item, itemIndex) => (
            <li key={`ol-${index}-${itemIndex}`}>{renderInline(item, `ol-${index}-${itemIndex}`)}</li>
          ))}
        </ol>
      );
    }

    if (block.type === 'quote') {
      return (
        <blockquote
          key={`block-${index}`}
          className="border-l-4 border-accent pl-4 py-1 text-slate-300 italic"
        >
          {renderInline(block.content, `quote-${index}`)}
        </blockquote>
      );
    }

    return (
      <p key={`block-${index}`} className="leading-7 text-slate-300">
        {renderInline(block.content, `p-${index}`)}
      </p>
    );
  });
}

export default function Documentation() {
  const [content, setContent] = useState('');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  useEffect(() => {
    fetch('/docs/PROJECT_DOCUMENTATION.md')
      .then((res) => {
        if (!res.ok) throw new Error('Failed to load documentation');
        return res.text();
      })
      .then((text) => {
        setContent(text);
        setError(false);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, []);

  const headings = useMemo(
    () =>
      content
        .split('\n')
        .filter((line) => /^##\s+/.test(line))
        .map((line) => line.replace(/^##\s+/, '')),
    [content]
  );

  if (loading) return <LoadingSpinner message="Loading project documentation…" />;

  if (error) {
    return (
      <div className="rounded-2xl border border-loss/30 bg-loss/10 p-6">
        <p className="text-loss font-semibold">Documentation file could not be loaded.</p>
      </div>
    );
  }

  return (
    <div className="space-y-6 fade-in">
      <section
        className="rounded-2xl border border-border p-6 md:p-8"
        style={{ background: 'linear-gradient(135deg,#0F1829 0%,#131E30 100%)' }}
      >
        <div className="flex items-start justify-between gap-6 flex-wrap">
          <div className="space-y-3 max-w-3xl">
            <div className="inline-flex items-center gap-2 rounded-full border border-accent/30 bg-accent/10 px-3 py-1 text-xs font-semibold uppercase tracking-widest text-accent">
              <BookOpen size={14} />
              Project Documentation
            </div>
            <h2 className="text-3xl font-extrabold text-slate-50">Frontend and backend technical guide</h2>
            <p className="text-slate-300 leading-7">
              This page loads the markdown documentation generated from the current codebase.
              It explains the project architecture, modules, features, data flow, infrastructure,
              and the reasoning behind the main technical choices.
            </p>
          </div>

          <a
            href="/docs/PROJECT_DOCUMENTATION.md"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-2 rounded-xl border border-border bg-surface px-4 py-3 text-sm font-medium text-slate-200 hover:border-accent/40 hover:text-white transition-colors"
          >
            <FileText size={16} />
            Open Raw Markdown
          </a>
        </div>
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-[280px_minmax(0,1fr)] gap-6 items-start">
        <aside className="rounded-2xl border border-border bg-panel p-5 xl:sticky xl:top-6">
          <p className="text-xs font-semibold uppercase tracking-widest text-muted mb-4">Sections</p>
          <div className="space-y-2">
            {headings.map((heading) => (
              <a
                key={heading}
                href={`#${slugify(heading)}`}
                className="block text-sm text-slate-300 hover:text-white transition-colors"
              >
                {heading}
              </a>
            ))}
          </div>
        </aside>

        <article className="rounded-2xl border border-border bg-panel p-6 md:p-8">
          <div className="space-y-4">{renderMarkdown(content)}</div>
        </article>
      </section>
    </div>
  );
}

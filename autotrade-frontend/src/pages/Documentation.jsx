import { useEffect, useRef } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import mermaid from 'mermaid';
import architectureDoc from '../assets/docs/trading_agent_architecture.md?raw';

mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
});

function Mermaid({ chart }) {
  const containerRef = useRef(null);

  useEffect(() => {
    if (containerRef.current && chart) {
      mermaid.render(`mermaid-${Math.random().toString(36).substr(2, 9)}`, chart)
        .then(({ svg }) => {
          containerRef.current.innerHTML = svg;
        })
        .catch(err => console.error(err));
    }
  }, [chart]);

  return <div ref={containerRef} className="my-6 flex justify-center bg-slate-800/30 p-6 rounded-xl border border-slate-700/50 overflow-x-auto" />;
}

export default function Documentation() {
  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-slate-100">System Documentation</h1>
      </div>
      
      <div className="bg-[#0f172a] border border-slate-700/50 rounded-xl p-6 md:p-8 text-slate-300 shadow-xl overflow-hidden">
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            h1: ({node, ...props}) => <h1 className="text-3xl font-bold text-slate-100 mb-6 pb-2 border-b border-slate-700/50" {...props} />,
            h2: ({node, ...props}) => <h2 className="text-2xl font-semibold text-slate-200 mb-4 mt-10 pb-1 border-b border-slate-700/50" {...props} />,
            h3: ({node, ...props}) => <h3 className="text-xl font-medium text-indigo-400 mb-3 mt-8" {...props} />,
            p: ({node, ...props}) => <p className="mb-5 leading-relaxed text-slate-300" {...props} />,
            ul: ({node, ...props}) => <ul className="list-disc list-outside ml-6 mb-6 space-y-2 text-slate-300 marker:text-slate-500" {...props} />,
            ol: ({node, ...props}) => <ol className="list-decimal list-outside ml-6 mb-6 space-y-2 text-slate-300 marker:text-slate-500" {...props} />,
            li: ({node, ...props}) => <li className="pl-1" {...props} />,
            strong: ({node, ...props}) => <strong className="font-semibold text-slate-200" {...props} />,
            em: ({node, ...props}) => <em className="italic text-slate-400" {...props} />,
            code: ({node, inline, className, children, ...props}) => {
              const match = /language-(\w+)/.exec(className || '');
              if (!inline && match && match[1] === 'mermaid') {
                return <Mermaid chart={String(children).replace(/\n$/, '')} />;
              }
              return inline 
                ? <code className="bg-slate-800/80 text-indigo-300 px-1.5 py-0.5 rounded text-sm font-mono border border-slate-700/50" {...props}>{children}</code>
                : <code className="block bg-[#080d1a] p-4 rounded-lg overflow-x-auto text-sm font-mono text-slate-300 my-6 border border-slate-700/50 shadow-inner" {...props}>{children}</code>;
            },
            blockquote: ({node, ...props}) => <blockquote className="border-l-4 border-indigo-500 pl-4 py-2 italic text-slate-400 my-6 bg-indigo-900/10 rounded-r shadow-sm" {...props} />,
            table: ({node, ...props}) => <div className="overflow-x-auto my-6 rounded-lg border border-slate-700/50"><table className="w-full text-sm border-collapse" {...props} /></div>,
            thead: ({node, ...props}) => <thead className="bg-slate-800/60" {...props} />,
            th: ({node, ...props}) => <th className="text-left font-semibold text-slate-300 px-4 py-2.5 border-b border-slate-700/50 whitespace-nowrap" {...props} />,
            td: ({node, ...props}) => <td className="px-4 py-2.5 border-b border-slate-800/70 text-slate-300 align-top" {...props} />,
            tr: ({node, ...props}) => <tr className="hover:bg-slate-800/30" {...props} />,
            a: ({node, ...props}) => <a className="text-cyan-400 hover:text-cyan-300 underline decoration-cyan-700" target="_blank" rel="noreferrer" {...props} />,
            hr: ({node, ...props}) => <hr className="border-slate-700/50 my-8" {...props} />,
          }}
        >
          {architectureDoc}
        </ReactMarkdown>
      </div>
    </div>
  );
}

export default function LoadingSpinner({ message = 'Analysing market...' }) {
  return (
    <div className="flex flex-col items-center justify-center h-full min-h-48 gap-4">
      <div className="relative w-12 h-12">
        <div className="absolute inset-0 rounded-full border-4 border-panel" />
        <div className="absolute inset-0 rounded-full border-4 border-transparent border-t-accent animate-spin" />
      </div>
      <p className="text-muted text-sm tracking-wide">{message}</p>
    </div>
  );
}

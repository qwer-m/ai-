import classNames from 'classnames';
import { FaCheckCircle, FaExclamationTriangle, FaInfoCircle, FaSpinner } from 'react-icons/fa';

export type InlineStatusType = 'info' | 'success' | 'warning' | 'error' | 'loading';

type Props = {
  type: InlineStatusType;
  text: string;
  className?: string;
  compact?: boolean;
};

function resolveIcon(type: InlineStatusType) {
  if (type === 'success') return <FaCheckCircle />;
  if (type === 'warning' || type === 'error') return <FaExclamationTriangle />;
  if (type === 'loading') return <FaSpinner className="inline-status-spin" />;
  return <FaInfoCircle />;
}

export function InlineStatusBanner({ type, text, className, compact = false }: Props) {
  if (!text) return null;
  return (
    <div
      className={classNames(
        'inline-status-banner',
        `inline-status-${type}`,
        compact ? 'inline-status-compact' : null,
        className,
      )}
      role="status"
      aria-live={type === 'error' ? 'assertive' : 'polite'}
    >
      <span className="inline-status-icon">{resolveIcon(type)}</span>
      <span className="inline-status-text">{text}</span>
    </div>
  );
}


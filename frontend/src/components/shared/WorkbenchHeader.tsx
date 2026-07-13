import type { ReactNode } from 'react';

type Props = {
  title: string;
  subtitle?: string;
  right?: ReactNode;
  className?: string;
};

export function WorkbenchHeader({ title, subtitle, right, className }: Props) {
  return (
    <div className={`workbench-header-card ${className || ''}`.trim()}>
      <div className="workbench-header-main">
        <h4 className="workbench-title mb-1">{title}</h4>
        {subtitle ? <p className="workbench-subtitle mb-0">{subtitle}</p> : null}
      </div>
      {right ? <div className="workbench-header-right">{right}</div> : null}
    </div>
  );
}

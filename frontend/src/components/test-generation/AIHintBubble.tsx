import { useEffect, useState } from 'react';
import { FaLightbulb } from 'react-icons/fa';

type AIHintBubbleProps = {
  onClose: () => void;
};

export function AIHintBubble({ onClose }: AIHintBubbleProps) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const lastVisit = window.localStorage.getItem('tg_last_visit');
    const isNewUser = !lastVisit || (Date.now() - Number(lastVisit) > 3 * 24 * 60 * 60 * 1000);

    if (isNewUser) setShow(true);
    window.localStorage.setItem('tg_last_visit', String(Date.now()));

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  if (!show) return null;

  return (
    <div
      className="position-absolute bg-white shadow-lg border rounded-3 p-3"
      style={{
        top: '-15px',
        left: '-15px',
        width: '280px',
        zIndex: 100,
        borderLeft: '4px solid #0d6efd',
      }}
    >
      <div className="d-flex justify-content-between align-items-start mb-2">
        <strong className="text-primary d-flex align-items-center gap-2">
          <FaLightbulb /> AI 助手建议
        </strong>
        <button onClick={onClose} className="btn-close btn-close-sm" aria-label="Close" />
      </div>
      <p className="small text-secondary mb-0" style={{ lineHeight: '1.5' }}>
        上传更完整的需求文档或原型图，并明确预期用例数量，可获得更全面的测试覆盖。
      </p>
      <div
        style={{
          position: 'absolute',
          bottom: '-8px',
          left: '20px',
          width: 0,
          height: 0,
          borderLeft: '8px solid transparent',
          borderRight: '8px solid transparent',
          borderTop: '8px solid #fff',
          filter: 'drop-shadow(0 2px 1px rgba(0,0,0,0.05))',
        }}
      />
    </div>
  );
}

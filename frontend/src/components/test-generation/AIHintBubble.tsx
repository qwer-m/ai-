import { useEffect, useState } from 'react';
import { FaLightbulb } from 'react-icons/fa';

type AIHintBubbleProps = {
  onClose: () => void;
};

export function AIHintBubble({ onClose }: AIHintBubbleProps) {
  const [show, setShow] = useState(false);

  useEffect(() => {
    const lastVisit = window.localStorage.getItem('tg_last_visit');
    const isNewUser = !lastVisit || Date.now() - Number(lastVisit) > 3 * 24 * 60 * 60 * 1000;

    if (isNewUser) {
      setShow(true);
    }
    window.localStorage.setItem('tg_last_visit', String(Date.now()));

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [onClose]);

  if (!show) {
    return null;
  }

  return (
    <div className="test-generation-hint position-absolute shadow-lg border rounded-3 p-3">
      <div className="d-flex justify-content-between align-items-start mb-2">
        <strong className="text-primary d-flex align-items-center gap-2">
          <FaLightbulb /> AI 助手建议
        </strong>
        <button onClick={onClose} className="btn-close btn-close-sm" aria-label="Close" />
      </div>
      <p className="small text-secondary mb-0 test-generation-hint-text">
        上传更完整的需求文档或原型图，并明确预期用例数量，可以显著提升用例覆盖率。
      </p>
      <div className="test-generation-hint-tail" />
    </div>
  );
}

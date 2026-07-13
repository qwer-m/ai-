import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react';

export function useApiTestingLayout() {
  const [showSidebar, setShowSidebar] = useState(true);
  const [sidebarWidth, setSidebarWidth] = useState(260);
  const [isResizingSidebar, setIsResizingSidebar] = useState(false);
  const [requestHeight, setRequestHeight] = useState(300);
  const [isDragging, setIsDragging] = useState(false);
  const mainContentRef = useRef<HTMLDivElement>(null);
  const sidebarResizeStartRef = useRef<{ x: number; width: number } | null>(null);

  useEffect(() => {
    const handleGlobalMouseMove = (event: globalThis.MouseEvent) => {
      if (!isDragging || !mainContentRef.current) return;

      const containerRect = mainContentRef.current.getBoundingClientRect();
      const headerOffset = 95;
      const resizerHeight = 8;
      const minResponseHeight = 200;

      const newHeight = event.clientY - containerRect.top - headerOffset;
      const maxHeight = containerRect.height - headerOffset - resizerHeight - minResponseHeight;

      if (newHeight > 100 && newHeight < maxHeight) {
        setRequestHeight(newHeight);
      }
    };

    const handleGlobalMouseUp = () => {
      setIsDragging(false);
    };

    if (isDragging) {
      document.addEventListener('mousemove', handleGlobalMouseMove);
      document.addEventListener('mouseup', handleGlobalMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleGlobalMouseMove);
      document.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, [isDragging]);

  useEffect(() => {
    const handleGlobalMouseMove = (event: globalThis.MouseEvent) => {
      if (!isResizingSidebar || !sidebarResizeStartRef.current) return;
      const dx = event.clientX - sidebarResizeStartRef.current.x;
      const next = Math.max(220, Math.min(560, sidebarResizeStartRef.current.width + dx));
      setSidebarWidth(next);
    };

    const handleGlobalMouseUp = () => {
      setIsResizingSidebar(false);
      sidebarResizeStartRef.current = null;
    };

    if (isResizingSidebar) {
      document.addEventListener('mousemove', handleGlobalMouseMove);
      document.addEventListener('mouseup', handleGlobalMouseUp);
    }

    return () => {
      document.removeEventListener('mousemove', handleGlobalMouseMove);
      document.removeEventListener('mouseup', handleGlobalMouseUp);
    };
  }, [isResizingSidebar]);

  const handleRequestBarMouseDown = useCallback((event: MouseEvent) => {
    setIsDragging(true);
    event.preventDefault();
  }, []);

  const handleSidebarResizerMouseDown = useCallback(
    (event: MouseEvent) => {
      setIsResizingSidebar(true);
      sidebarResizeStartRef.current = { x: event.clientX, width: sidebarWidth };
      event.preventDefault();
    },
    [sidebarWidth],
  );

  return {
    showSidebar,
    setShowSidebar,
    sidebarWidth,
    isResizingSidebar,
    requestHeight,
    isDragging,
    handleRequestBarMouseDown,
    handleSidebarResizerMouseDown,
    mainContentRef,
  };
}

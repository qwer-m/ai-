import { useEffect } from 'react';
import type { DragEvent, RefObject } from 'react';
import { MAX_FILE_SIZE } from './testGenerationCaseUtils';
import { persistUploadedFile } from './useTestGenerationPersistence';

type Args = {
  projectId: number | null;
  file: File | null;
  showHint: boolean;
  uploadZoneRef: RefObject<HTMLDivElement | null>;
  setShowHint: (value: boolean) => void;
  setIsDragActive: (value: boolean) => void;
  setFile: (file: File | null) => void;
  setFileResult: (value: any) => void;
  setFileStreamingContent: (value: string) => void;
  setProtoFile: (file: File | null) => void;
  setToastType: (type: 'success' | 'error') => void;
  setToastMsg: (msg: string | null) => void;
  setSavedFileName: (value: string) => void;
};

export function useTestGenerationFileHandlers({
  projectId,
  file,
  showHint,
  uploadZoneRef,
  setShowHint,
  setIsDragActive,
  setFile,
  setFileResult,
  setFileStreamingContent,
  setProtoFile,
  setToastType,
  setToastMsg,
  setSavedFileName,
}: Args) {
  useEffect(() => {
    const zone = uploadZoneRef.current;
    if (!zone) return;
    let timer: any;
    const handleMouseEnter = () => {
      if (!file && !showHint) {
        timer = setTimeout(() => {
          if (!file) setShowHint(true);
        }, 5000);
      }
    };
    const handleMouseLeave = () => {
      if (timer) clearTimeout(timer);
    };
    zone.addEventListener('mouseenter', handleMouseEnter);
    zone.addEventListener('mouseleave', handleMouseLeave);
    return () => {
      zone.removeEventListener('mouseenter', handleMouseEnter);
      zone.removeEventListener('mouseleave', handleMouseLeave);
      if (timer) clearTimeout(timer);
    };
  }, [file, showHint, setShowHint, uploadZoneRef]);

  const validateAndSetFile = (f: File | null) => {
    if (!f) {
      setFile(null);
      setFileResult(null);
      setFileStreamingContent('');
      return;
    }
    if (f.size > MAX_FILE_SIZE) {
      setToastType('error');
      setToastMsg('文件大小超过限制 (Max 50MB)');
      return;
    }
    setFile(f);
    setFileResult(null);
    setFileStreamingContent('');
    setSavedFileName(f.name);
    void persistUploadedFile(projectId, 'tg_file', f);
  };

  const handleFileChange = (f: File | null) => validateAndSetFile(f);

  const handleProtoFileChange = (f: File | null) => {
    setProtoFile(f);
    void persistUploadedFile(projectId, 'tg_protoFile', f);
  };

  const handleDragOver = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragActive(true);
  };

  const handleDragLeave = () => {
    setIsDragActive(false);
  };

  const handleDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setIsDragActive(false);
    if (e.dataTransfer.files?.length) validateAndSetFile(e.dataTransfer.files[0]);
  };

  return {
    handleFileChange,
    handleProtoFileChange,
    handleDragOver,
    handleDragLeave,
    handleDrop,
    validateAndSetFile,
  };
}

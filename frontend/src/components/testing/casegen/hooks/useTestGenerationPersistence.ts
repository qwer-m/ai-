import { useEffect } from 'react';
import { getFileFromDB, saveFileToDB } from '../../../../utils/storage';
import type { TestGenerationMode } from '../../../test-generation/types';

type Args = {
  projectId: number | null;
  loading: boolean;
  mode: TestGenerationMode;
  requirement: string;
  docType: string;
  compress: boolean;
  expectedCount: number;
  appendCount: number;
  textResult: any;
  textStreamingContent: string;
  fileResult: any;
  fileStreamingContent: string;
  savedFileName: string;
  setFile: (file: File | null) => void;
  setProtoFile: (file: File | null) => void;
  setToastType: (type: 'success' | 'error') => void;
  setToastMsg: (msg: string | null) => void;
};

const getKey = (projectId: number | null, base: string) => (projectId ? `${base}_${projectId}` : base);

export function useTestGenerationPersistence({
  projectId,
  loading,
  mode,
  requirement,
  docType,
  compress,
  expectedCount,
  appendCount,
  textResult,
  textStreamingContent,
  fileResult,
  fileStreamingContent,
  savedFileName,
  setFile,
  setProtoFile,
  setToastType,
  setToastMsg,
}: Args) {
  useEffect(() => {
    window.localStorage.removeItem('tg_result');
    window.localStorage.removeItem('tg_streamingContent');
  }, []);

  useEffect(() => {
    window.localStorage.setItem('tg_mode', mode);
  }, [mode]);

  useEffect(() => {
    window.localStorage.setItem(getKey(projectId, 'tg_requirement'), requirement);
  }, [requirement, projectId]);

  useEffect(() => {
    window.localStorage.setItem('tg_docType', docType);
  }, [docType]);

  useEffect(() => {
    window.localStorage.setItem('tg_compress', String(compress));
  }, [compress]);

  useEffect(() => {
    window.localStorage.setItem(getKey(projectId, 'tg_expectedCount'), String(expectedCount));
  }, [expectedCount, projectId]);

  useEffect(() => {
    window.localStorage.setItem(getKey(projectId, 'tg_appendCount'), String(appendCount));
  }, [appendCount, projectId]);

  useEffect(() => {
    const key = getKey(projectId, 'tg_text_result');
    textResult ? window.localStorage.setItem(key, JSON.stringify(textResult)) : window.localStorage.removeItem(key);
  }, [textResult, projectId]);

  useEffect(() => {
    window.localStorage.setItem(getKey(projectId, 'tg_text_streaming_content'), textStreamingContent);
  }, [textStreamingContent, projectId]);

  useEffect(() => {
    const key = getKey(projectId, 'tg_file_result');
    fileResult ? window.localStorage.setItem(key, JSON.stringify(fileResult)) : window.localStorage.removeItem(key);
  }, [fileResult, projectId]);

  useEffect(() => {
    window.localStorage.setItem(getKey(projectId, 'tg_file_streaming_content'), fileStreamingContent);
  }, [fileStreamingContent, projectId]);

  useEffect(() => {
    window.localStorage.setItem(getKey(projectId, 'tg_savedFileName'), savedFileName);
  }, [savedFileName, projectId]);

  useEffect(() => {
    setFile(null);
    setProtoFile(null);
    if (!projectId) return;
    let active = true;
    getFileFromDB(`tg_file_${projectId}`).then((f) => { if (active) setFile(f || null); }).catch(() => {});
    getFileFromDB(`tg_protoFile_${projectId}`).then((f) => { if (active) setProtoFile(f || null); }).catch(() => {});
    return () => { active = false; };
  }, [projectId, setFile, setProtoFile]);

  useEffect(() => {
    const onOnline = () => {};
    const onOffline = () => {
      setToastType('error');
      setToastMsg('Network connection lost, please check your network settings.');
    };
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    return () => {
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
    };
  }, [setToastMsg, setToastType]);

  useEffect(() => {
    const beforeUnload = (e: BeforeUnloadEvent) => {
      if (loading) {
        e.preventDefault();
        e.returnValue = '';
        return '';
      }
    };
    window.addEventListener('beforeunload', beforeUnload);
    return () => window.removeEventListener('beforeunload', beforeUnload);
  }, [loading]);
}

export const persistUploadedFile = async (projectId: number | null, key: 'tg_file' | 'tg_protoFile', file: File | null) => {
  if (!projectId) return;
  await saveFileToDB(`${key}_${projectId}`, file);
};

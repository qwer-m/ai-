import { useCallback, useEffect, useRef, useState, type MouseEvent, type RefObject } from 'react';
import type { EnvConfig } from '../../../standard-api-testing/utils/types';

type UseApiTestingEnvironmentParams = {
  apiPath: string;
  inputRef: RefObject<HTMLInputElement | null>;
};

export function useApiTestingEnvironment({ apiPath, inputRef }: UseApiTestingEnvironmentParams) {
  const [savedEnvs, setSavedEnvs] = useState<EnvConfig[]>(() => {
    try {
      if (typeof window === 'undefined') return [];
      const saved = localStorage.getItem('api_testing_saved_envs_v1');
      if (!saved) return [];
      const parsed = JSON.parse(saved);
      return Array.isArray(parsed) ? parsed : [];
    } catch (error) {
      console.error('Failed to load saved envs:', error);
      return [];
    }
  });
  const [activeEnvTag, setActiveEnvTag] = useState<string | null>(null);
  const [showPopup, setShowPopup] = useState(false);
  const [showEnvModal, setShowEnvModal] = useState(false);
  const [editingEnv, setEditingEnv] = useState<EnvConfig | null>(null);
  const popupTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const getEnvBaseUrlValue = useCallback(
    (tag: string) => {
      const env = savedEnvs.find((item) => item.baseUrl === tag);
      if (!env) return '';
      const varName = tag.replace(/[\{\}\s]/g, '');
      const variable = env.variables?.find((item) => item.key === varName);
      return variable ? variable.value : '';
    },
    [savedEnvs],
  );

  const setEnvBaseUrlValue = useCallback(
    (tag: string, value: string) => {
      const varName = tag.replace(/[\{\}\s]/g, '');
      setSavedEnvs((prev) => {
        const envIndex = prev.findIndex((item) => item.baseUrl === tag);
        if (envIndex === -1) {
          return [
            ...prev,
            {
              id: Date.now().toString(),
              name: varName,
              baseUrl: tag,
              variables: [{ key: varName, value, enabled: true }],
            },
          ];
        }

        const next = [...prev];
        const env = { ...next[envIndex] };
        const variables = env.variables ? [...env.variables] : [];
        const variableIndex = variables.findIndex((item) => item.key === varName);
        if (variableIndex === -1) {
          variables.push({ key: varName, value, enabled: true });
        } else {
          variables[variableIndex] = { ...variables[variableIndex], value };
        }
        env.variables = variables;
        next[envIndex] = env;
        return next;
      });
    },
    [setSavedEnvs],
  );

  const handleApiPathBlur = useCallback(() => {
    if (!apiPath || !apiPath.trim()) return;

    const match = apiPath.match(/^(\{\{\s*(.+?)\s*\}\})/);
    if (!match) return;

    const tag = match[1];
    const envName = match[2];
    if (savedEnvs.some((item) => item.baseUrl === tag)) return;

    const newEnv: EnvConfig = {
      id: Date.now().toString(),
      name: envName,
      baseUrl: tag,
      variables: [],
    };
    setSavedEnvs((prev) => [...prev, newEnv]);
  }, [apiPath, savedEnvs]);

  const handleInputMouseMove = useCallback(
    (event: MouseEvent<HTMLInputElement>) => {
      if (!activeEnvTag || !inputRef.current) return;

      const input = inputRef.current;
      const rect = input.getBoundingClientRect();
      const mouseX = event.clientX - rect.left;

      const style = window.getComputedStyle(input);
      const font = style.font;
      const paddingLeft = Number.parseFloat(style.paddingLeft) || 0;
      const borderLeft = Number.parseFloat(style.borderLeftWidth) || 0;
      const scrollLeft = input.scrollLeft;

      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d');
      if (!context) return;
      context.font = font;

      const match = apiPath.match(/^([\s\S]*?)(\{\{\s*(.*?)\s*\}\})/);
      let startX = borderLeft + paddingLeft - scrollLeft;
      let endX = startX;

      if (match && match[2] === activeEnvTag) {
        const prefixWidth = context.measureText(match[1]).width;
        const tagWidth = context.measureText(activeEnvTag).width;
        startX += prefixWidth;
        endX = startX + tagWidth;
      } else {
        const tagWidth = context.measureText(activeEnvTag).width;
        endX = startX + tagWidth;
      }

      if (mouseX >= startX && mouseX <= endX) {
        if (popupTimerRef.current) {
          clearTimeout(popupTimerRef.current);
          popupTimerRef.current = null;
        }
        setShowPopup(true);
        return;
      }

      if (showPopup && !popupTimerRef.current) {
        popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
      }
    },
    [activeEnvTag, apiPath, inputRef, showPopup],
  );

  const handleInputMouseLeave = useCallback(() => {
    if (!showPopup) return;
    if (popupTimerRef.current) clearTimeout(popupTimerRef.current);
    popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
  }, [showPopup]);

  const handlePopupMouseEnter = useCallback(() => {
    if (popupTimerRef.current) {
      clearTimeout(popupTimerRef.current);
      popupTimerRef.current = null;
    }
    setShowPopup(true);
  }, []);

  const handlePopupMouseLeave = useCallback(() => {
    if (popupTimerRef.current) clearTimeout(popupTimerRef.current);
    popupTimerRef.current = setTimeout(() => setShowPopup(false), 300);
  }, []);

  useEffect(() => {
    const match = apiPath.match(/^([\s\S]*?)(\{\{\s*(.*?)\s*\}\})/);
    if (match) {
      setActiveEnvTag(match[2]);
    } else {
      setActiveEnvTag(null);
      setShowPopup(false);
    }
  }, [apiPath]);

  useEffect(() => {
    try {
      localStorage.setItem('api_testing_saved_envs_v1', JSON.stringify(savedEnvs));
    } catch (error) {
      console.error('Failed to save envs:', error);
    }
  }, [savedEnvs]);

  const handleSaveEnv = useCallback(() => {
    setShowEnvModal(true);
    setEditingEnv(null);
  }, []);

  const handleUpdateEnv = useCallback((env: EnvConfig) => {
    setSavedEnvs((prev) => {
      const exists = prev.find((item) => item.id === env.id);
      if (exists) return prev.map((item) => (item.id === env.id ? env : item));
      return [...prev, env];
    });
    setEditingEnv(null);
  }, []);

  const handleDeleteEnv = useCallback(
    (id: string) => {
      if (!window.confirm('Delete environment?')) return;
      setSavedEnvs((prev) => prev.filter((item) => item.id !== id));
      if (editingEnv?.id === id) setEditingEnv(null);
    },
    [editingEnv],
  );

  return {
    savedEnvs,
    setSavedEnvs,
    activeEnvTag,
    showPopup,
    setShowPopup,
    showEnvModal,
    setShowEnvModal,
    editingEnv,
    setEditingEnv,
    getEnvBaseUrlValue,
    setEnvBaseUrlValue,
    handleApiPathBlur,
    handleInputMouseMove,
    handleInputMouseLeave,
    handlePopupMouseEnter,
    handlePopupMouseLeave,
    handleSaveEnv,
    handleUpdateEnv,
    handleDeleteEnv,
  };
}

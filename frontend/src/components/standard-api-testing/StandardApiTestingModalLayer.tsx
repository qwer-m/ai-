import type { Dispatch, ReactNode, SetStateAction } from 'react';
import { CookieManagerModal } from './modals/CookieManagerModal';
import { EnvManagerModal } from './modals/EnvManagerModal';
import { SaveRequestModal } from './modals/SaveRequestModal';
import type { EnvConfig } from './utils/types';

type SaveForm = {
  name: string;
  description: string;
  parentId: number | null;
};

type StandardApiTestingModalLayerProps = {
  showSaveModal: boolean;
  setShowSaveModal: Dispatch<SetStateAction<boolean>>;
  saveForm: SaveForm;
  setSaveForm: Dispatch<SetStateAction<SaveForm>>;
  renderFolderOptions: (parentId: number | null) => ReactNode;
  handleConfirmSave: () => void;
  showCookieModal: boolean;
  setShowCookieModal: Dispatch<SetStateAction<boolean>>;
  cookieJar: Record<string, string>;
  setCookieJar: Dispatch<SetStateAction<Record<string, string>>>;
  showEnvModal: boolean;
  setShowEnvModal: Dispatch<SetStateAction<boolean>>;
  editingEnv: EnvConfig | null;
  setEditingEnv: Dispatch<SetStateAction<EnvConfig | null>>;
  savedEnvs: EnvConfig[];
  handleDeleteEnv: (id: string) => void;
  handleUpdateEnv: (env: EnvConfig) => void;
};

/**
 * 组件职责：
 * 聚合页面弹窗层（保存请求、Cookie 管理、环境管理），
 * 避免主页面文件在 JSX 末尾堆叠大量弹窗细节。
 */
export function StandardApiTestingModalLayer({
  showSaveModal,
  setShowSaveModal,
  saveForm,
  setSaveForm,
  renderFolderOptions,
  handleConfirmSave,
  showCookieModal,
  setShowCookieModal,
  cookieJar,
  setCookieJar,
  showEnvModal,
  setShowEnvModal,
  editingEnv,
  setEditingEnv,
  savedEnvs,
  handleDeleteEnv,
  handleUpdateEnv,
}: StandardApiTestingModalLayerProps) {
  return (
    <>
      <SaveRequestModal
        show={showSaveModal}
        onHide={() => setShowSaveModal(false)}
        saveForm={saveForm}
        setSaveForm={setSaveForm}
        renderFolderOptions={renderFolderOptions}
        onConfirmSave={handleConfirmSave}
      />

      <CookieManagerModal
        show={showCookieModal}
        onHide={() => setShowCookieModal(false)}
        cookieJar={cookieJar}
        setCookieJar={setCookieJar}
      />

      <EnvManagerModal
        show={showEnvModal}
        onHide={() => setShowEnvModal(false)}
        editingEnv={editingEnv}
        setEditingEnv={setEditingEnv}
        savedEnvs={savedEnvs}
        onDeleteEnv={handleDeleteEnv}
        onUpdateEnv={handleUpdateEnv}
      />
    </>
  );
}

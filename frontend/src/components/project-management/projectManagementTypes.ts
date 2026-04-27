export type Project = {
  id: number;
  name: string;
  description?: string | null;
  parent_id?: number | null;
  created_at?: string;
  level?: number;
};

export type ProjectManagementProps = {
  projects: Project[];
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
  onSelectProject: (id: number) => void;
  onLog: (msg: string) => void;
};

export type ProjectFormState = {
  name: string;
  description: string;
  parentId: number | null;
};

export type RagDatasetType = 'validation' | 'test' | 'challenge' | 'regression';

export type RagDatasetRow = {
  id: number;
  name: string;
  type: RagDatasetType;
  description?: string | null;
  sample_count?: number;
};


import type { SavedInterface } from './types';

export type DragOverPosition = 'top' | 'bottom' | 'middle';

type DropPlan = {
  nextItems: SavedInterface[];
  newParentId: number | null;
  draggedId: number;
};

function wouldCreateFolderCycle(
  items: SavedInterface[],
  draggedFolderId: number,
  newParentId: number | null,
) {
  // 校验目录拖拽后是否形成“父拖入子”的循环结构。
  let current = newParentId;
  while (current) {
    if (current === draggedFolderId) return true;
    const parent = items.find((item) => item.id === current)?.parentId;
    current = parent || null;
  }
  return false;
}

export function computeDragOverPosition(
  isFolder: boolean,
  y: number,
  height: number,
): DragOverPosition {
  // 文件夹采用三段（上/中/下），请求采用两段（上/下）判定。
  if (isFolder) {
    if (y < height * 0.25) return 'top';
    if (y > height * 0.75) return 'bottom';
    return 'middle';
  }

  if (y < height * 0.5) return 'top';
  return 'bottom';
}

export function planInterfaceDrop(
  items: SavedInterface[],
  draggedId: number,
  targetId: number | null,
  position: DragOverPosition,
): DropPlan | null {
  // 纯函数：只计算拖拽结果，不触发副作用，便于复用与测试。
  const draggedItem = items.find((item) => item.id === draggedId);
  if (!draggedItem) return null;
  if (draggedId === targetId) return null;

  let newParentId: number | null = null;
  const targetItem = targetId !== null ? items.find((item) => item.id === targetId) : undefined;

  if (targetItem) {
    if (position === 'middle' && targetItem.type === 'folder') {
      newParentId = targetId;
    } else {
      newParentId = targetItem.parentId;
    }
  }

  if (draggedItem.type === 'folder' && wouldCreateFolderCycle(items, draggedId, newParentId)) {
    // 无效拖拽：目录形成循环时直接拒绝。
    return null;
  }

  const nextItems = [...items];
  const draggedIndex = nextItems.findIndex((item) => item.id === draggedId);
  if (draggedIndex < 0) return null;
  nextItems.splice(draggedIndex, 1);

  const updatedItem: SavedInterface = { ...draggedItem, parentId: newParentId };

  if (targetId === null) {
    nextItems.push(updatedItem);
  } else if (targetItem) {
    const targetIndex = nextItems.findIndex((item) => item.id === targetId);
    if (targetIndex < 0) {
      nextItems.push(updatedItem);
    } else if (position === 'middle' && targetItem.type === 'folder') {
      nextItems.push(updatedItem);
    } else if (position === 'top') {
      nextItems.splice(targetIndex, 0, updatedItem);
    } else {
      nextItems.splice(targetIndex + 1, 0, updatedItem);
    }
  } else {
    nextItems.push(updatedItem);
  }

  return {
    nextItems,
    newParentId,
    draggedId,
  };
}

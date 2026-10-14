export const getMethodColor = (method: string) => {
  switch (method) {
    case 'GET':
      return '#198754';
    case 'POST':
      return '#8B4513';
    case 'PUT':
      return '#6f42c1';
    case 'DELETE':
      return '#b02a37';
    default:
      return '#6c757d';
  }
};

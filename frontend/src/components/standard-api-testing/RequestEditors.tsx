import { Button, Form } from 'react-bootstrap';
import { FaTrash } from 'react-icons/fa';

export type KvItem = { key: string; value: string; desc: string };
export type FormDataItem = KvItem & { type: 'text' | 'file'; src?: string };

export const parseBulkText = (text: string): KvItem[] => {
  return text
    .split('\n')
    .map((line) => {
      const index = line.indexOf(':');
      if (index === -1) return { key: line.trim(), value: '', desc: '' };
      return {
        key: line.substring(0, index).trim(),
        value: line.substring(index + 1).trim(),
        desc: '',
      };
    })
    .filter((i) => i.key || i.value);
};

export const stringifyBulkItems = (items: KvItem[]) => {
  return items
    .filter((i) => i.key || i.value)
    .map((i) => `${i.key}:${i.value}`)
    .join('\n');
};

type KvEditorProps = {
  items: KvItem[];
  onChange: (items: KvItem[]) => void;
  isBulk: boolean;
  onToggleBulk: () => void;
  bulkText: string;
  onBulkChange: (val: string) => void;
};

export function KvEditor({
  items,
  onChange,
  isBulk,
  onToggleBulk,
  bulkText,
  onBulkChange,
}: KvEditorProps) {
  const handleChange = (index: number, field: keyof KvItem, val: string) => {
    const newItems = [...items];
    newItems[index] = { ...newItems[index], [field]: val };
    if (index === items.length - 1 && (newItems[index].key || newItems[index].value)) {
      newItems.push({ key: '', value: '', desc: '' });
    }
    onChange(newItems);
  };

  const handleDelete = (index: number) => {
    if (items.length <= 1) {
      onChange([{ key: '', value: '', desc: '' }]);
      return;
    }
    onChange(items.filter((_, i) => i !== index));
  };

  if (isBulk) {
    return (
      <div className="w-100 h-100 d-flex flex-column">
        <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
          <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>
            Key-Value Edit
          </Button>
        </div>
        <Form.Control
          as="textarea"
          className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
          style={{ resize: 'none', outline: 'none' }}
          value={bulkText}
          onChange={(e) => onBulkChange(e.target.value)}
          placeholder="key:value"
          spellCheck={false}
        />
      </div>
    );
  }

  return (
    <div className="w-100 overflow-hidden d-flex flex-column">
      <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
        <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>
          Bulk Edit
        </Button>
      </div>
      <table className="table table-sm table-bordered border-start-0 border-end-0 mb-0 align-middle small w-100" style={{ tableLayout: 'fixed' }}>
        <thead className="text-secondary bg-light">
          <tr>
            <th style={{ width: '30%', fontWeight: '500' }} className="ps-3 border-start-0">键 (Key)</th>
            <th style={{ width: '30%', fontWeight: '500' }} className="ps-2">值 (Value)</th>
            <th style={{ width: '40%', fontWeight: '500' }} className="ps-2 border-end-0">描述 (Description)</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => (
            <tr key={idx} className="border-bottom">
              <td className="ps-3 border-start-0">
                <Form.Control size="sm" placeholder="Key" value={item.key} onChange={(e) => handleChange(idx, 'key', e.target.value)} className="border-0 shadow-none bg-transparent px-0" />
              </td>
              <td className="ps-2">
                <Form.Control size="sm" placeholder="Value" value={item.value} onChange={(e) => handleChange(idx, 'value', e.target.value)} className="border-0 shadow-none bg-transparent px-0" />
              </td>
              <td className="ps-2 position-relative border-end-0">
                <Form.Control size="sm" placeholder="Description" value={item.desc} onChange={(e) => handleChange(idx, 'desc', e.target.value)} className="border-0 shadow-none bg-transparent px-0" style={{ paddingRight: '24px' }} />
                {items.length > 1 && (
                  <Button
                    variant="link"
                    className="position-absolute top-50 end-0 translate-middle-y text-muted p-0 pe-2 opacity-50 hover-opacity-100"
                    style={{ zIndex: 5 }}
                    onClick={() => handleDelete(idx)}
                  >
                    <FaTrash size={12} />
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

type FormDataEditorProps = {
  items: FormDataItem[];
  onChange: (items: FormDataItem[]) => void;
  isBulk: boolean;
  onToggleBulk: () => void;
  bulkText: string;
  onBulkChange: (val: string) => void;
};

export function FormDataEditor({
  items,
  onChange,
  isBulk,
  onToggleBulk,
  bulkText,
  onBulkChange,
}: FormDataEditorProps) {
  const handleChange = (
    index: number,
    field: keyof FormDataItem,
    val: string,
  ) => {
    const newItems = [...items];
    newItems[index] = { ...newItems[index], [field]: val };
    if (index === items.length - 1 && (newItems[index].key || newItems[index].value)) {
      newItems.push({ key: '', value: '', desc: '', type: 'text' });
    }
    onChange(newItems);
  };

  const handleFileChange = (index: number, file: File | null) => {
    const newItems = [...items];
    if (file) {
      newItems[index] = { ...newItems[index], src: file.name };
      const reader = new FileReader();
      reader.onload = (e) => {
        const res = e.target?.result as string;
        newItems[index].value = res;
        onChange(newItems);
      };
      reader.readAsDataURL(file);
    } else {
      newItems[index] = { ...newItems[index], src: '', value: '' };
      onChange(newItems);
    }
  };

  const handleDelete = (index: number) => {
    if (items.length <= 1) {
      onChange([{ key: '', value: '', desc: '', type: 'text' }]);
      return;
    }
    onChange(items.filter((_, i) => i !== index));
  };

  if (isBulk) {
    return (
      <div className="w-100 h-100 d-flex flex-column">
        <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
          <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>
            Key-Value Edit
          </Button>
        </div>
        <Form.Control
          as="textarea"
          className="flex-grow-1 border-0 p-3 font-monospace small bg-transparent"
          style={{ resize: 'none', outline: 'none' }}
          value={bulkText}
          onChange={(e) => onBulkChange(e.target.value)}
          placeholder="key:value"
          spellCheck={false}
        />
      </div>
    );
  }

  return (
    <div className="w-100 overflow-hidden">
      <div className="d-flex justify-content-end bg-light border-bottom px-2 py-1">
        <Button variant="link" size="sm" className="text-decoration-none" onClick={onToggleBulk}>
          Bulk Edit
        </Button>
      </div>
      <table className="table table-sm table-bordered border-start-0 border-end-0 mb-0 align-middle small w-100" style={{ tableLayout: 'fixed' }}>
        <thead className="text-secondary bg-light">
          <tr>
            <th style={{ width: '25%', fontWeight: '500' }} className="ps-3 border-start-0">键 (Key)</th>
            <th style={{ width: '10%', fontWeight: '500' }} className="ps-2">类型</th>
            <th style={{ width: '30%', fontWeight: '500' }} className="ps-2">值 (Value)</th>
            <th style={{ width: '35%', fontWeight: '500' }} className="ps-2 border-end-0">描述 (Description)</th>
          </tr>
        </thead>
        <tbody>
          {items.map((item, idx) => (
            <tr key={idx} className="border-bottom">
              <td className="ps-3 border-start-0">
                <Form.Control size="sm" placeholder="Key" value={item.key} onChange={(e) => handleChange(idx, 'key', e.target.value)} className="border-0 shadow-none bg-transparent px-0" />
              </td>
              <td className="ps-2">
                <Form.Select size="sm" value={item.type} onChange={(e) => handleChange(idx, 'type', e.target.value as FormDataItem['type'])} className="border-0 shadow-none bg-transparent px-0 text-secondary" style={{ fontSize: '0.875rem' }}>
                  <option value="text">Text</option>
                  <option value="file">File</option>
                </Form.Select>
              </td>
              <td className="ps-2">
                {item.type === 'file' ? (
                  <div className="d-flex align-items-center">
                    <Form.Control
                      type="file"
                      size="sm"
                      className="border-0 shadow-none bg-transparent px-0"
                      onChange={(e) => {
                        const target = e.target as HTMLInputElement;
                        handleFileChange(idx, target.files?.[0] || null);
                      }}
                    />
                    {item.src && <span className="small text-muted ms-2 text-truncate" style={{ maxWidth: '100px' }} title={item.src}>{item.src}</span>}
                  </div>
                ) : (
                  <Form.Control size="sm" placeholder="Value" value={item.value} onChange={(e) => handleChange(idx, 'value', e.target.value)} className="border-0 shadow-none bg-transparent px-0" />
                )}
              </td>
              <td className="ps-2 position-relative border-end-0">
                <Form.Control size="sm" placeholder="Description" value={item.desc} onChange={(e) => handleChange(idx, 'desc', e.target.value)} className="border-0 shadow-none bg-transparent px-0" style={{ paddingRight: '24px' }} />
                {items.length > 1 && (
                  <Button
                    variant="link"
                    className="position-absolute top-50 end-0 translate-middle-y text-muted p-0 pe-2 opacity-50 hover-opacity-100"
                    style={{ zIndex: 5 }}
                    onClick={() => handleDelete(idx)}
                  >
                    <FaTrash size={12} />
                  </Button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

from __future__ import annotations

from pathlib import Path

_PARTS = (
    'config_routes_runtime_impl_parts/config_routes_runtime_impl_part1.py.part',
    'config_routes_runtime_impl_parts/config_routes_runtime_impl_part2.py.part'
)

_base_dir = Path(__file__).resolve().parent
_source_chunks: list[str] = []
for _rel in _PARTS:
    _source_chunks.append((_base_dir / _rel).read_text(encoding='utf-8'))

exec(compile(''.join(_source_chunks), __file__, 'exec'), globals(), globals())

del Path, _PARTS, _base_dir, _source_chunks, _rel

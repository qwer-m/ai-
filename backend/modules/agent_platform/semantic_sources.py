"""按实际入口的静态传递依赖固定产物语义，不使用整仓构建摘要。"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable


_BACKEND_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=512)
def _source_tree(path: str, modified_ns: int, size: int) -> ast.Module:
    """只缓存实际文件版本的语法树；文件变化不会沿用旧语义。"""
    return ast.parse(Path(path).read_text(encoding="utf-8-sig"), filename=path)


def source_dependency_manifest(
    roots: list[tuple[str, str]], *, backend_root: Path = _BACKEND_ROOT,
) -> dict[str, str]:
    """读取真实源码的定义、常量和静态导入闭包；不导入或执行待分析模块。

    动态注册处理器须由调用方把实际绑定的函数作为根，不能从一个动态名称猜测。
    外部依赖由 SDK/运行库版本契约记录；本地找不到明确声明的根则直接拒绝。
    """
    modules: dict[str, tuple[dict[str, ast.AST], dict[str, str], Path]] = {}
    manifest: dict[str, str] = {}

    def module_file(name: str) -> Path | None:
        candidate = backend_root.joinpath(*name.split("."))
        for path in (candidate.with_suffix(".py"), candidate / "__init__.py"):
            if path.is_file():
                return path
        return None

    def imports(nodes: list[ast.AST], name: str, path: Path) -> dict[str, str]:
        aliases: dict[str, str] = {}
        package = name if path.name == "__init__.py" else name.rpartition(".")[0]
        for node in nodes:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    aliases[alias.asname or alias.name.split(".")[0]] = alias.name if alias.asname else alias.name.split(".")[0]
            elif isinstance(node, ast.ImportFrom):
                target = node.module or ""
                if node.level:
                    target = importlib.util.resolve_name("." * node.level + target, package)
                for alias in node.names:
                    if alias.name == "*":
                        aliases[f"*:{target}"] = target
                    else:
                        aliases[alias.asname or alias.name] = f"{target}.{alias.name}"
        return aliases

    def load(name: str) -> tuple[dict[str, ast.AST], dict[str, str], Path] | None:
        if name in modules:
            return modules[name]
        path = module_file(name)
        if path is None:
            return None
        stat = path.stat()
        tree = _source_tree(str(path), stat.st_mtime_ns, stat.st_size)
        symbols: dict[str, ast.AST] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols[node.name] = node
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    for item in ast.walk(target):
                        if isinstance(item, ast.Name):
                            symbols[item.id] = node
        result = (symbols, imports(list(tree.body), name, path), path)
        modules[name] = result
        return result

    def qualified_target(target: str) -> tuple[str, str] | None:
        pieces = target.split(".")
        if module_file(target) is not None:
            return target, "*"
        for boundary in range(len(pieces) - 1, 0, -1):
            name = ".".join(pieces[:boundary])
            if module_file(name) is not None:
                return name, ".".join(pieces[boundary:])
        return None

    def visit(name: str, symbol: str, *, required: bool = False) -> None:
        module = load(name)
        if module is None:
            if required:
                raise ValueError(f"缺少可验证的产物入口源码: {name}.{symbol}")
            return
        symbols, aliases, path = module
        if symbol == "*":
            identity = f"{path.relative_to(backend_root).as_posix()}:*"
            if identity in manifest:
                return
            manifest[identity] = hashlib.sha256(path.read_bytes()).hexdigest()
            for key in symbols:
                visit(name, key)
            return
        first = symbol.split(".")[0]
        node = symbols.get(first)
        if node is None:
            target = aliases.get(first)
            if target:
                suffix = symbol[len(first):]
                reference = qualified_target(target + suffix)
                if reference:
                    visit(*reference, required=required)
                    return
            candidates = []
            for alias, source in aliases.items():
                if not alias.startswith("*:"):
                    continue
                imported = load(source)
                if imported is not None and (first in imported[0] or first in imported[1]):
                    candidates.append((source, symbol))
            if len(candidates) == 1:
                visit(*candidates[0], required=required)
                return
            if required:
                raise ValueError(f"产物入口没有静态源码定义: {name}.{symbol}")
            return
        # 类整体进入依赖；方法、属性、继承和装饰器修改不能绕过身份校验。
        identity = f"{path.relative_to(backend_root).as_posix()}:{first}"
        if identity in manifest:
            return
        encoded = ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
        manifest[identity] = hashlib.sha256(encoded).hexdigest()
        local_aliases = {**aliases, **imports(list(ast.walk(node)), name, path)}
        parents = {child: parent for parent in ast.walk(node) for child in ast.iter_child_nodes(parent)}
        for item in ast.walk(node):
            if isinstance(item, ast.Name) and isinstance(item.ctx, ast.Load):
                if item.id in symbols:
                    visit(name, item.id)
                elif item.id in local_aliases:
                    reference = qualified_target(local_aliases[item.id])
                    if reference:
                        # module.attr 由下方属性链精确处理；模块作为值传递时才保守纳入整模块。
                        if reference[1] != "*" or not isinstance(parents.get(item), ast.Attribute):
                            visit(*reference)
                else:
                    for alias, source in local_aliases.items():
                        if alias.startswith("*:"):
                            imported = load(source)
                            if imported is not None and (item.id in imported[0] or item.id in imported[1]):
                                visit(source, item.id)
            elif isinstance(item, ast.Attribute):
                parts = []
                current: ast.AST = item
                while isinstance(current, ast.Attribute):
                    parts.append(current.attr)
                    current = current.value
                if isinstance(current, ast.Name) and current.id in local_aliases:
                    target = local_aliases[current.id] + "." + ".".join(reversed(parts))
                    reference = qualified_target(target)
                    if reference:
                        visit(*reference)

    for name, symbol in roots:
        visit(name, symbol, required=True)
    if not manifest:
        raise ValueError("产物语义缺少真实生产源码依赖")
    return dict(sorted(manifest.items()))


def callable_source_manifest(callbacks: list[Callable[..., Any]]) -> dict[str, str]:
    """只接受真实可定位的 Python 入口，不把匿名或运行时生成函数当作稳定契约。"""
    roots = []
    for callback in callbacks:
        if not inspect.isfunction(callback) and not inspect.isclass(callback):
            raise ValueError("产物依赖必须是可验证的生产函数或类型")
        if "<locals>" in callback.__qualname__:
            raise ValueError("产物依赖不能使用缺少独立源码身份的局部函数")
        roots.append((callback.__module__, callback.__qualname__))
    # 分析器自身也是语义契约；算法升级使旧产物自然失效。
    roots.append((__name__, "source_dependency_manifest"))
    return source_dependency_manifest(roots)

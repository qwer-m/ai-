"""静态只读清理地图：记录真实源码引用，不导入生产模块、不接触数据库。"""
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
import ast,hashlib,json
ROOT=Path(__file__).resolve().parents[3]
BACKEND=ROOT/'backend';DOMAIN=BACKEND/'modules/agent_platform'
generation={p.stem for p in DOMAIN.glob('test_generation_*.py')}|{'planning_dependencies'}
trees={};contents={};imports=[];parse_errors=[]
for path in BACKEND.rglob('*.py'):
 if any(part in {'.venv','venv','__pycache__','node_modules'} for part in path.parts):continue
 try:
  content=path.read_text(encoding='utf-8-sig');tree=ast.parse(content)
 except (SyntaxError,UnicodeError) as error:
  parse_errors.append({'path':str(path.relative_to(ROOT)),'error':str(error)});continue
 rel=path.relative_to(ROOT).as_posix();trees[rel]=tree;contents[rel]=content
 for node in ast.walk(tree):
  if isinstance(node,ast.ImportFrom):
   for name in node.names:
    module_target=(node.module or '').rsplit('.',1)[-1]
    target=module_target if module_target in generation else name.name
    if target in generation:
     imports.append({'path':rel,'line':node.lineno,'target':target,
         'imported_name':name.name,'alias':name.asname,'scope':'test' if '/tests/' in rel else 'qa' if '/scripts/qa/' in rel else 'production'})
  elif isinstance(node,ast.Import):
   for name in node.names:
    if name.name.rsplit('.',1)[-1] in generation:
     imports.append({'path':rel,'line':node.lineno,'target':name.name.rsplit('.',1)[-1],
       'imported_name':'<module>','alias':name.asname,'scope':'test' if '/tests/' in rel else 'qa' if '/scripts/qa/' in rel else 'production'})

def functions(rel):
 return [{'name':node.name,'line':node.lineno,'end_line':node.end_lineno,'kind':type(node).__name__}
   for node in trees[rel].body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]
def specs(rel):
 result=defaultdict(list)
 for statement in trees[rel].body:
  name=(statement.target.id if isinstance(statement,ast.AnnAssign) and isinstance(statement.target,ast.Name)
        else statement.targets[0].id if isinstance(statement,ast.Assign) and isinstance(statement.targets[0],ast.Name) else '')
  value=getattr(statement,'value',None)
  if name not in {'BUILTIN_AGENT_SPECS','BUILTIN_TOOL_SPECS','BUILTIN_WORKFLOW_SPECS'} or not isinstance(value,(ast.Tuple,ast.List)):continue
  for entry in value.elts:
   if not isinstance(entry,ast.Dict):continue
   record={'line':entry.lineno,'end_line':entry.end_lineno}
   for key,val in zip(entry.keys,entry.values,strict=True):
    if isinstance(key,ast.Constant) and key.value in {'agent_key','tool_key','workflow_key','handler_key','name'} and isinstance(val,ast.Constant):record[key.value]=val.value
   result[name].append(record)
 return dict(result)
modules=[]
for stem in sorted(generation):
 rel=f'backend/modules/agent_platform/{stem}.py';content=contents[rel]
 incoming=[entry for entry in imports if entry['target']==stem]
 modules.append({'path':rel,'sha256':hashlib.sha256(content.encode('utf8')).hexdigest(),'lines':len(content.splitlines()),
  'production_incoming_external_to_generation':[entry for entry in incoming if entry['scope']=='production' and Path(entry['path']).stem not in generation],
  'generation_incoming':[entry for entry in incoming if entry['scope']=='production' and Path(entry['path']).stem in generation],
  'test_incoming_paths':sorted({entry['path'] for entry in incoming if entry['scope']=='test'}),
  'qa_incoming_paths':sorted({entry['path'] for entry in incoming if entry['scope']=='qa'}),
  'functions':functions(rel)})
groups={scope:sorted({entry['path'] for entry in imports if entry['scope']==scope}) for scope in ('production','test','qa')}
external=[entry for entry in imports if entry['scope']=='production' and Path(entry['path']).stem not in generation]
spec_path='backend/modules/agent_platform/test_generation_builtin_specs.py'
report={'observed_at':datetime.now(timezone.utc).isoformat(),'mode':'静态源码只读；不导入业务、不调用模型、不访问DB',
 'generation_module_count':len(modules),'generation_module_lines':sum(m['lines'] for m in modules),
 'module_scope_includes_planning_dependencies':True,'modules':modules,'incoming_imports':imports,
 'external_production_imports':external,'dependent_file_groups':groups,
 'generation_specs':specs(spec_path),'evaluation_specs':specs('backend/modules/agent_platform/test_case_evaluation_workflow.py'),
 'parse_errors':parse_errors,
 'limits':['这是显式Python import图；反射、handler字符串和直接读取源码的回归须与报告中的注册表/规格入口共同审查。',
 '可删模块以新生成入口替代且解除registry/conflict_confirmation导入为前提；不建议删除共享平台引擎。']}
target=Path(__file__).with_name('cleanup-dependency-map.json');target.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({'output':str(target),'modules':len(modules),'lines':report['generation_module_lines'],
 'spec_counts':{k:len(v) for k,v in report['generation_specs'].items()},'dependent_counts':{k:len(v) for k,v in groups.items()},
 'external_production_imports':external,'parse_errors':parse_errors},ensure_ascii=False))

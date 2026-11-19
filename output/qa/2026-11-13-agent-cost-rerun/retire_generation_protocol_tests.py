"""退役明确旧生成协议的可执行测试/QA；保留所有真实fixture和历史输出。"""
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json
ROOT=Path(__file__).resolve().parents[3]
BASE=Path(__file__).parent
mapping=json.loads((BASE/'cleanup-dependency-map.json').read_text(encoding='utf8'))
mixed={'test_agent_platform_contracts.py','test_native_test_generation.py','test_runtime_retry.py',
       'test_context_compression.py','test_artifact_read_cost_real_data.py','test_agent_program_input_real_data.py'}
def hash_file(path):return hashlib.sha256(path.read_bytes()).hexdigest()
fixture_root=(ROOT/'backend/tests/agent_platform/fixtures').resolve()
fixtures_before={p.relative_to(ROOT).as_posix():hash_file(p) for p in fixture_root.rglob('*') if p.is_file()}
candidates=[p for p in mapping['dependent_file_groups']['test'] if Path(p).name not in mixed]
candidates+=mapping['dependent_file_groups']['qa']
records=[]
for relative in candidates:
 path=(ROOT/relative).resolve()
 assert path.is_relative_to(ROOT.resolve()) and path.suffix=='.py' and '/fixtures/' not in path.as_posix()
 assert path.is_relative_to((ROOT/'backend/tests/agent_platform').resolve()) or path.is_relative_to((ROOT/'backend/scripts/qa/verify').resolve())
 if not path.exists():
  records.append({'path':relative,'status':'执行前已不存在，未归为本次删除'});continue
 body=path.read_bytes();records.append({'path':relative,'status':'待删除','sha256':hashlib.sha256(body).hexdigest(),
   'lines':len(body.decode('utf-8-sig').splitlines()),'bytes':len(body),
   'reason':'只验证已退役生成协议；通用能力由独立平台/评估/导出/记忆测试保留，新协议另以真实来源回归'})
manifest={'observed_at':datetime.now(timezone.utc).isoformat(),'scope':'仅旧snapshot明确依赖的测试/QA；不改生产',
 'model_requests_sent':0,'business_database_writes':0,'preserved_fixture_count':len(fixtures_before),
 'preserved_fixture_hashes':fixtures_before,'mixed_files_retained':sorted(mixed),'files':records}
target=BASE/'retired-generation-tests-manifest.json'
target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
for record in records:
 if record['status']!='待删除':continue
 path=(ROOT/record['path']).resolve();assert hash_file(path)==record['sha256'];path.unlink();record['status']='已删除'
fixtures_after={p.relative_to(ROOT).as_posix():hash_file(p) for p in fixture_root.rglob('*') if p.is_file()}
assert fixtures_after==fixtures_before
manifest['fixtures_unchanged']=True
manifest['deleted_count']=sum(r['status']=='已删除' for r in records)
manifest['deleted_lines']=sum(r.get('lines',0) for r in records if r['status']=='已删除')
manifest['deleted_by_scope']={k:sum(r['status']=='已删除' and k in r['path'] for r in records) for k in ('/tests/','/scripts/qa/')}
target.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print(json.dumps({k:manifest[k] for k in ('deleted_count','deleted_lines','deleted_by_scope','preserved_fixture_count','fixtures_unchanged')},ensure_ascii=False))

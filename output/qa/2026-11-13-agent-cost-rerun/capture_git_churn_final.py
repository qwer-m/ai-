"""冻结当前HEAD至工作区的Git事实；生产、测试、QA脚本和真实fixture分别核算。"""
from collections import defaultdict
from datetime import datetime,timezone
import hashlib,json,subprocess
from pathlib import Path
ROOT=Path.cwd();OUT=ROOT/'output/qa/2026-11-13-agent-cost-rerun';PREVIOUS=OUT/'git-churn-after-cleanup.json'
prior=json.loads(PREVIOUS.read_text(encoding='utf8'));previous_kinds={r['path']:r['kind'] for r in prior['tracked_changes']+prior['untracked']}

def git(*arguments):
 p=subprocess.run(['git','-c','core.quotePath=false',*arguments],cwd=ROOT,capture_output=True,check=True)
 return p.stdout,p.stderr

def kind(path):
 if path in previous_kinds:return previous_kinds[path]
 if path.startswith(('output/','runtime/','backend/runtime/')):return 'runtime_artifact'
 if path.startswith('backend/tests/') and '/fixtures/' in path:return 'real_test_fixtures'
 if path.startswith(('backend/tests/','frontend/tests/')) or '/__tests__/' in path or '.test.' in Path(path).name or '.spec.' in Path(path).name:return 'test_code'
 if path.startswith(('backend/scripts/','scripts/')):return 'qa_and_ops_scripts'
 if path.startswith(('docs/','doc/')) or Path(path).suffix.lower()=='.md':return 'docs'
 if path.startswith(('backend/','frontend/')):return 'production_code_config'
 raise ValueError('存在未分类路径，需明确后再核算：'+path)

def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def source_fingerprints(tracked,untracked):
 return {r['path']:digest(ROOT/r['path']) if (ROOT/r['path']).is_file() else None for r in tracked+untracked if r['kind'] in {'production_code_config','test_code'}}

head=git('rev-parse','HEAD')[0].decode().strip();assert head=='559b0457a6db951fbef8c353da28cb53defe5f86'
numstat,stderr=git('diff','HEAD','--numstat');raw_numstat=OUT/'git-churn-final.numstat.txt';raw_numstat.write_bytes(numstat);(OUT/'git-churn-final.numstat.stderr.log').write_bytes(stderr)
# 先创建本次原始清单文件，避免统计结果生成自身导致路径清单变化。
raw_untracked=OUT/'git-churn-final.untracked.z';raw_status=OUT/'git-churn-final.status.z';raw_untracked.touch(exist_ok=True);raw_status.touch(exist_ok=True)
untracked_bytes,_=git('ls-files','--others','--exclude-standard','-z');raw_untracked.write_bytes(untracked_bytes);raw_status.write_bytes(git('status','--porcelain=v1','-z','--untracked-files=all')[0])
tracked=[]
for line in numstat.decode('utf8').splitlines():
 a,d,path=line.split('\t',2)
 if ' => ' in path:raise ValueError('发现重命名，必须按原始rename numstat显式解析，不可误算：'+path)
 tracked.append({'path':path,'added':int(a) if a!='-' else 0,'deleted':int(d) if d!='-' else 0,'binary':a=='-' or d=='-','kind':kind(path),'fully_deleted':not (ROOT/path).exists()})
untracked=[]
for path in [p.decode('utf8') for p in untracked_bytes.split(b'\0') if p]:
 payload=(ROOT/path).read_bytes();row={'path':path,'kind':kind(path),'bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
 try:
  if b'\0' in payload:raise UnicodeError()
  row['text_lines']=len(payload.decode('utf-8-sig').splitlines());row['binary']=False
 except UnicodeError:row['binary']=True
 untracked.append(row)
fingerprints=source_fingerprints(tracked,untracked)
summary={}
for category in ('production_code_config','qa_and_ops_scripts','test_code','real_test_fixtures','docs','runtime_artifact'):
 ts=[r for r in tracked if r['kind']==category];us=[r for r in untracked if r['kind']==category]
 value={'tracked_files':len(ts),'fully_deleted_files':sum(row['fully_deleted'] for row in ts),'added':sum(r['added'] for r in ts),'deleted':sum(r['deleted'] for r in ts),'untracked_files':len(us),'untracked_text_lines':sum(r.get('text_lines',0) for r in us),'untracked_binary_bytes':sum(r['bytes'] for r in us if r['binary'])}
 value['tracked_net_lines']=value['added']-value['deleted'];value['net_lines_including_untracked_text']=value['tracked_net_lines']+value['untracked_text_lines'];summary[category]=value
catalog=json.loads((OUT/'catalog-retirement-applied.json').read_text(encoding='utf8'))
catalog_summary={}
for stage in ('before','after'):
 rows=catalog[stage]['active_generation'];modes=defaultdict(int)
 for row in rows:modes[row['mode']]+=1
 catalog_summary[stage]={'active_generation_total':len(rows),'execution_modes':dict(modes),'enabled_obsolete_builtin_tools':catalog[stage]['enabled_obsolete_builtin_tools']}
head_after=git('rev-parse','HEAD')[0].decode().strip();numstat_after=git('diff','HEAD','--numstat')[0];fingerprints_after=source_fingerprints(tracked,untracked)
assert head==head_after and numstat==numstat_after and fingerprints==fingerprints_after,'源码快照在采样期间变化，应重新核算'
totals={'tracked_added_lines':sum(r['added'] for r in tracked),'tracked_deleted_lines':sum(r['deleted'] for r in tracked),'tracked_net_lines':sum(r['added']-r['deleted'] for r in tracked),'untracked_text_lines':sum(r.get('text_lines',0) for r in untracked),'tracked_changed_files':len(tracked),'fully_deleted_files':sum(row['fully_deleted'] for row in tracked),'untracked_files':len(untracked)}
report={'head':head,'observed_at_utc':datetime.now(timezone.utc).isoformat(),'scope':prior['scope'],'classification_basis':'沿用git-churn-after-cleanup.json的全部已有路径分类；新增路径按相同目录职责归类。backend/scripts为QA/运维脚本，backend/tests/fixtures为真实fixture，output为运行证据，均与生产代码分开。','classification_rules':{'production_code_config':'backend/frontend应用与配置，排除tests/scripts/fixtures；文件名test_generation不使运行时代码变成测试','qa_and_ops_scripts':'backend/scripts与根scripts','test_code':'backend/tests或frontend测试路径，排除fixtures','real_test_fixtures':'backend/tests/**/fixtures/**，压缩包只计字节不解压冒充源码行','docs':'文档路径','runtime_artifact':'output/runtime等保存的证据、草稿和构建产物'},'summary':summary,'tracked_changes':tracked,'fully_deleted_paths':[row['path'] for row in tracked if row['fully_deleted']],'untracked':untracked,'totals':totals,'source_worktree_content_sha256':fingerprints,'source_snapshot_stable_during_capture':True,'raw_evidence':{'numstat':str(raw_numstat.relative_to(ROOT)),'untracked_nul_delimited':str(raw_untracked.relative_to(ROOT)),'status_nul_delimited':str(raw_status.relative_to(ROOT)),'commands':[['git','-c','core.quotePath=false','diff','HEAD','--numstat'],['git','ls-files','--others','--exclude-standard','-z'],['git','status','--porcelain=v1','-z','--untracked-files=all']]},'catalog_retirement_from_actual_receipt':catalog_summary,'catalog_receipt':'output/qa/2026-11-13-agent-cost-rerun/catalog-retirement-applied.json','interpretation_limits':['HEAD至当前工作区含此前多次改造，所有deleted不能全部归为本轮旧兼容拆除。','生产净增包含新增未跟踪生产源码；fixtures和output体积与代码行分开。','保留有效通用DAG执行器；退役的是测试生成旧DAG定义及无用内置工具，不以删通用引擎凑行数。','Git已跟踪文件的删除行有原始numstat凭据；此前未跟踪文件删除不能由当前Git差异追溯为删行。','此报告不声称重构净删生产代码：若净值为正，应如实说明净增。'],'initial_audit':prior.get('initial_audit'),'not_all_churn_is_compatibility_removal':True}
(OUT/'git-churn-final.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps({'summary':summary,'totals':totals,'catalog':catalog_summary,'stable':True},ensure_ascii=False))


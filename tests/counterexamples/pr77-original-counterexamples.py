import subprocess,pathlib,tempfile,sys,hashlib,json,importlib.util,copy,sqlite3
repo='/Users/samsonkim/designwins-orchestrator/harness';rev='5668f15550517a481c3ca9e2467ce38db48873a5';root=pathlib.Path(tempfile.mkdtemp(prefix='neo-pr77-original-'));src=root/'src';hashes={}
files=subprocess.check_output(['git','-C',repo,'ls-tree','-r','--name-only',rev,'harness/normserve'],text=True).splitlines()+['harness/__init__.py','tests/test_normserve.py']
for f in files:
 source_rev='3718c73ce9549aeeb8db2aa8eba7bb2c342b4205' if f=='tests/test_normserve.py' else rev; b=subprocess.check_output(['git','-C',repo,'show',source_rev+':'+f]);p=src/f;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);hashes[f]=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
sys.path.insert(0,str(src));from harness.normserve.loader import Store,load_rows
spec=importlib.util.spec_from_file_location('fixture',src/'tests/test_normserve.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
vault,sha=m.make_real_vault(root)
def row():
 r=m.sample_structured_rows(sha)[0];r['value_role']='min';r['evidence'][0]['quoted_text']='2.3';return r
out={'commit':rev,'private_root':str(root),'source_blobs':hashes,'tests':{}}
def run(label,r,v=vault,opn='ST1S15',store=None,rules=None):
 s=store or Store(str(root/(label+'.db')))
 try:
  result=load_rows(s,[r],vault_root=v,opn_default=opn,ratified_rules=rules);data=result.summary();data['rows']=s.claims_for_opn(opn)
 except Exception as e:data={'error':type(e).__name__+': '+str(e)}
 out['tests'][label]=data;return s
run('positive',row())
r=row();r['value']=999;r['evidence'][0]['quoted_text']='999 V';run('invented_cell',r)
r=row();r['evidence'][0].update(table_index=999,row_index=999,column_index=999);r['field_binding']['table']=999;run('nonexistent_coordinates',r)
r=row();r['value']=5.5;r['evidence'][0]['quoted_text']='5.5';run('max_cell_labeled_min',r)
r=row();r['field']='vout_min_v';r['field_binding']['column_key']='vout';run('vin_labeled_vout',r)
r=row();r.update(value=999,page_1based=999,verdict='vault_sha_recompute');r['evidence'][0].update(quoted_text='999 V',page_1based=999);run('vault_only',r)
r=row();r['applicability'].update(cell_text='UNRELATED-OPN',structure_page=999);run('invented_opn',r,opn='UNRELATED-OPN')
r=row();r['conditions']=[{'verbatim':'TA999','precedence':'local','source_page':1}];run('invented_conditions',r)
r=row();r['conditions']=[dict(m.COND_TA105_COLUMN)];run('different_conditions_first',r)
s=run('null_replay_first',row());run('null_replay_second',row(),store=s)
s=run('condition_first',row());r=row();r['conditions']=[dict(m.COND_TA105_COLUMN)];run('condition_second',r,store=s)
s=run('conflict_first',row());r=row();r['value']=5.5;r['evidence'][0]['quoted_text']='5.5';run('conflict_second',r,store=s)
s=run('page_first',row());r=row();r.update(value=4.7,page_1based=3,verbatim='VIN | Operating input voltage range | 4.7 | 5.5 | V');r['evidence'][0].update(page_1based=3,quoted_text='4.7');run('page_second',r,store=s)
# Strong printed-row positive; adversarial versions use source text already printed elsewhere.
for label,lines,value,quoted,opn in [
 ('unit_conflict',['ST1S15','TA = 25 C','VIN Min Unit','VIN | 9mV'],9,'9mV','ST1S15'),
 ('unit_header_conflict',['ST1S15','TA = 25 C','VIN Min Unit mV','VIN | 9'],9,'9','ST1S15'),
 ('negative_sign',['ST1S15','TA = 25 C','VIN Min Unit V','VIN | -9 V'],9,'-9 V','ST1S15'),
 ('rounded_value',['ST1S15','TA = 25 C','VIN Min Unit V','VIN | 9.0 V'],9.04,'9.0 V','ST1S15'),
 ('negated_identity',['Not applicable to ST1S15','TA = 25 C','VIN Min Unit V','VIN | 9 V'],9,'9 V','ST1S15')]:
 p=root/label;p.mkdir();v,sh=m.make_real_vault(p,[lines]);r=m.structured(m.SAMPLE_ROWS[0],sh,quoted,'vin','min',lines[2],page=1,conditions=[{'verbatim':'TA = 25 C','precedence':'local','source_page':1}]);r.update(value=value,value_role='min',verbatim=lines[3]);run(label,r,v,opn)
s=run('source_conflict_first',row());r=row();r.update(value=4.7,verbatim='VIN | Operating input voltage range | 4.7 | 5.5 | V');r['evidence'][0]['quoted_text']='4.7';run('source_conflict_second',r,store=s)
for label,lines,value,quoted in [
 ('header_mV_declared_V',['ST1S15','TA = 25 C','VIN Min Unit mV','VIN | min | 9'],9,'9'),
 ('negated_identity_valid_row',['Not applicable to ST1S15','TA = 25 C','VIN Min Unit V','VIN | min | 9 V'],9,'9 V'),
 ('cross_field_cell',['ST1S15','TA = 25 C','VOUT Min Unit V','VOUT | min | 1.2 V | VIN | min | 9 V'],9,'9 V')]:
 p=root/label;p.mkdir();v,sh=m.make_real_vault(p,[lines]);key='vout' if label=='cross_field_cell' else 'vin';r=m.structured(m.SAMPLE_ROWS[0],sh,quoted,key,'min',lines[2],page=1,conditions=[{'verbatim':'TA = 25 C','precedence':'local','source_page':1}]);r.update(field=key+'_min_v',value=value,value_role='min',verbatim=lines[3]);run(label,r,v)
for old in ['47cac64070013cf7e8574a65ab65cb665e6b2b48','61e952970fab22ceae6aea0a9e6c37a8568fcf95']:
 ns={};exec(subprocess.check_output(['git','-C',repo,'show',old+':harness/normserve/schema.py'],text=True),ns)
 for populated in [False,True]:
  p=root/(old[:7]+str(populated)+'.db');c=sqlite3.connect(p);c.executescript(ns['SCHEMA_V1'])
  if populated:c.execute("INSERT INTO norm_claims(opn,doc_sha,field,unit,page,verbatim,verified_by,rule_version,schema_version,loaded_at) VALUES(?,?,?,?,?,?,?,?,?,?)",('ST1S15',sha,'vin','V',1,'fixture','quote_pass','fixture','norm-serve.v1',0))
  c.commit();c.close()
  try:s=Store(str(p));a={'opened':True}
  except Exception as e:a={'opened':False,'error':str(e)}
  out['tests']['old_'+old[:7]+'_'+str(populated)]=a
p=pathlib.Path('evidence/orchestrator-priorities-20261031/pr77-original-counterexamples.json');p.write_text(json.dumps(out,indent=2));print(json.dumps({k:{x:y for x,y in v.items() if x!='rows'} for k,v in out['tests'].items()},indent=2));print('RECEIPT',p,hashlib.sha256(p.read_bytes()).hexdigest())

import ast,copy,hashlib,json,pathlib,subprocess,sys,tempfile
repo='/Users/samsonkim/designwins-orchestrator/harness';sha='f3df0c2d9e15f2eafb1b2c46af2a4981a72f0ea9';root=pathlib.Path(tempfile.mkdtemp(prefix='neo-f3df-review-'));src=root/'src';hashes={}
files=subprocess.check_output(['git','-C',repo,'ls-tree','-r','--name-only',sha,'harness/normserve'],text=True).splitlines()+['harness/__init__.py','tests/test_normserve.py']
for rel in files:
 b=subprocess.check_output(['git','-C',repo,'show',sha+':'+rel]);p=src/rel;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b);hashes[rel]=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
sys.path.insert(0,str(src));from harness.normserve.loader import Store,load_rows
ns={'json':json};tree=ast.parse((src/'tests/test_normserve.py').read_text());names={'ST1S15_SHA','VIN_VERBATIM','VOUT_VERBATIM','SAMPLE_ROWS','VOUT_COND_VIN','COND_TA25_INHERITED'};nodes=[]
for n in tree.body:
 if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id in names for t in n.targets):nodes.append(n)
 if isinstance(n,ast.FunctionDef) and n.name in {'make_pdf_bytes','make_rows','structured'}:nodes.append(n)
exec(compile(ast.Module(body=nodes,type_ignores=[]),'<pinned-fixture-functions>','exec'),ns)
b=ns['make_pdf_bytes']([ns['VIN_VERBATIM'],ns['VIN_VERBATIM']]);docsha=hashlib.sha256(b).hexdigest();vault=root/'vault';blob=vault/'blobs'/docsha[:2]/docsha[2:4]/(docsha+'.pdf');blob.parent.mkdir(parents=True);blob.write_bytes(b)
def row():return ns['structured'](ns['SAMPLE_ROWS'][0],docsha,'2.3 | 5.5 | V','vin','min',value_role='range')
def report(st,r,opn='ST1S15'):
 v=load_rows(st,[r],vault_root=str(vault),opn_default=opn);return {'loaded':v.loaded,'duplicates':v.duplicates,'held':len(v.holds),'rejected':len(v.rejections),'holds':v.holds,'rejections':v.rejections,'count':st.con.execute('select count(*) from norm_claims').fetchone()[0]}
out={'commit':sha,'private_root':str(root),'source_blobs':hashes,'tests':{}}
s=Store(str(root/'replay.db'));r=row();out['tests']['null_condition_replay']=[report(s,r),report(s,r)]
s=Store(str(root/'conflict.db'));r=row();r['condition_verbatim']='same condition';a=report(s,r);r2=copy.deepcopy(r);r2['value']=5.5;out['tests']['conflicting_value']=[a,report(s,r2)]
s=Store(str(root/'pages.db'));r=row();r['condition_verbatim']='same condition';a=report(s,r);r2=copy.deepcopy(r);r2['page_1based']=2;r2['evidence'][0]['page_1based']=2;out['tests']['distinct_page']=[a,report(s,r2)]
for label,mut,opn in [('forged_cell',lambda r:(r.update(value=999),r['evidence'][0].update(quoted_text='999 V')),'ST1S15'),('invented_opn_conditions',lambda r:(r['applicability'].update(cell_text='UNRELATED-OPN',structure_page=999),r.update(conditions=[{'verbatim':'TA=999C invented','precedence':'local','source_page':999}])),'UNRELATED-OPN'),('vault_only_invented_claim',lambda r:(r.update(method='vault_sha_recompute',value=999,page_1based=999),r['evidence'][0].update(quoted_text='999 V',page_1based=999,table_index=-999,row_index=-999,column_index=-999),r['field_binding'].update(table=-999)),'ST1S15')]:
 s=Store(str(root/(label+'.db')));r=row();mut(r);out['tests'][label]=report(s,r,opn)
for rev in ['47cac64070013cf7e8574a65ab65cb665e6b2b48','61e952970fab22ceae6aea0a9e6c37a8568fcf95']:
 import sqlite3
 text=subprocess.check_output(['git','-C',repo,'show',rev+':harness/normserve/schema.py'],text=True);n={};exec(text,n)
 for populated in [False,True]:
  path=root/(rev[:7]+str(populated)+'.db');con=sqlite3.connect(path);con.executescript(n['SCHEMA_V1']);con.commit();con.close()
  if populated:
   s=Store(str(path));report(s,row());s.con.close()
  try:s=Store(str(path));o={'opened':True,'rows':s.con.execute('select count(*) from norm_claims').fetchone()[0]};s.con.close()
  except Exception as e:o={'opened':False,'error':str(e)}
  out['tests']['old_store_'+rev[:7]+'_'+str(populated)]=o
p=pathlib.Path('evidence/orchestrator-priorities-20261031/f3df-disposable-regressions.json');p.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({'commit':sha,'private_root':str(root),'tests':out['tests']},indent=2))

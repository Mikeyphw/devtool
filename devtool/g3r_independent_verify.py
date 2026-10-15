#!/usr/bin/env python3
"""Independent read-only verifier for versioned current-source Devtool evidence.

Returns PASS for reproducible source-bound candidate evidence, *never* for
historical G3 native qualification or formal release.
"""
from __future__ import annotations
import argparse,hashlib,json,subprocess,sys
from collections import Counter
from functools import lru_cache
from pathlib import Path

def sha(b:bytes)->str:return hashlib.sha256(b).hexdigest()
@lru_cache(maxsize=4096)
def file_sha(p:Path)->str:return sha(p.read_bytes())
def load(p:Path):return json.loads(p.read_bytes())
def chk(v,desc):
 if not v:raise ValueError(desc)

def git(root:Path,*args)->str:
 cp=subprocess.run(['git','-c',f'safe.directory={root}',*args],cwd=root,capture_output=True,text=True,timeout=30)
 chk(cp.returncode==0,'git failed '+str(args)+': '+cp.stderr)
 return cp.stdout.strip()
@lru_cache(maxsize=4096)
def head_content(root,rel):
 cp=subprocess.run(['git','-c',f'safe.directory={root}','show',f'HEAD:{rel}'],cwd=root,capture_output=True,timeout=30)
 chk(cp.returncode==0,'reference missing in current Git HEAD: '+rel)
 return cp.stdout

def resolved(root:Path,rel:str)->Path:
 chk(isinstance(rel,str) and rel and '\x00' not in rel and not Path(rel).is_absolute(),'invalid reference '+repr(rel))
 p=(root/rel).resolve(strict=False)
 chk(p.is_relative_to(root) and p.is_file(),'missing/escaped '+rel)
 return p

def verify(out:Path,root:Path)->dict:
 out=out.resolve(strict=True);root=root.resolve(strict=True)
 receipts=(out/'SHA256SUMS').read_text().splitlines()
 listed=set()
 for line in receipts:
  want,delim,name=line.partition('  ')
  chk(delim and len(want)==64 and len(name)>0,'invalid checksum receipt')
  p=resolved(out,name)
  chk(p!=out/'SHA256SUMS' and name not in listed,'duplicate or self-referential checksum')
  listed.add(name);chk(file_sha(p)==want,'evidence receipt mismatch '+name)
 actual={p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}-{'SHA256SUMS'}
 chk(actual==listed,f'evidence file inventory drift extra={actual-listed} missing={listed-actual}')
 # Independent source obligation structure evaluation from the checked-out HEAD.
 from .g3r_obligation_coverage import evaluate as evaluate_coverage
 expected_structure=evaluate_coverage(root)
 recorded_structure=load(out/'INDEPENDENT_LEDGER_STRUCTURE.json')
 chk(recorded_structure==expected_structure,'obligation structure proof differs from current source')
 receipt=load(out/'EVIDENCE_RECORD.json')
 runtime=load(out/'INDEPENDENT_SEMANTIC_AND_NATIVE_RUNS.json')
 for key,run in runtime.get('checks',{}).items():
  if 'output_file' in run:
   log=resolved(out/'runs',run['output_file'])
   chk(file_sha(log)==run['output_sha256'],'subprocess log SHA mismatch: '+key)
   chk(run['status'] in ('PASS','FAIL','BLOCKED_TIMEOUT'),'invalid process status '+key)
   if run['status']=='PASS':chk(run['exit_code']==0,'false-success process receipt '+key)
   if run['status']=='FAIL':chk(isinstance(run['exit_code'],int) and run['exit_code']!=0,'false-failure receipt '+key)
 reconciliation=load(out/'SOURCE_RECONCILIATION.json')
 head=git(root,'rev-parse','HEAD');tree=git(root,'rev-parse','HEAD^{tree}')
 chk(receipt['source_commit']==head==reconciliation['source_commit'],'source SHA mismatch')
 chk(receipt['source_tree']==tree==reconciliation['source_tree'],'source tree mismatch')
 chk(not git(root,'status','--porcelain=v1','--untracked-files=all'),'source checkout is dirty')
 chk(receipt['native_g3_status']=='NOT_QUALIFIED' and receipt['release_status']=='NOT_QUALIFIED','false qualification claim')
 chk(receipt['historical_attestation_replaced'] is False,'historical replacement claim')
 all_orig=reconciliation['historical_files_sha256']
 for rel,want in all_orig.items():
  archived=resolved(out/'original',rel)
  current=resolved(root,rel)
  chk(file_sha(archived)==want==file_sha(current),'immutable original changed '+rel)
  chk(sha(head_content(root,rel))==want,'historical-file current Git mismatch '+rel)
 families=load(out/'CURRENT_G3_BINDINGS.json');rows=families['rows']
 chk(len(rows)==reconciliation['current_source_bindings'],'G3 binding count drift')
 keys={'android-source':('ANDROID_G3_SOURCE_DISPOSITIONS.json','source_id','ANDROID_G3_FINAL_ATTESTATION.json'),
 'life-source':('LIFE_G3_SOURCE_DISPOSITIONS.json','source_id','LIFE_G3_FINAL_ATTESTATION.json'),
 'life-feature':('LIFE_G3_FEATURE_DISPOSITIONS.json','obligation_id','LIFE_G3_FINAL_ATTESTATION.json'),
 'life-promise':('LIFE_G3_PROMISE_DISPOSITIONS.json','promise_id','LIFE_G3_FINAL_ATTESTATION.json')}
 expected=[];roots={};integrity=[]
 for family,(disposition,idkey,attname) in keys.items():
  disp=load(out/'original'/disposition); att=load(out/'original'/attname)
  items=[];old=[]
  for row in disp['rows']:
   refs=row['evidence'];pins=row['evidence_sha256']
   chk(bool(refs) and len(set(refs))==len(refs) and set(refs)==set(pins),'incomplete historical evidence binding '+family)
   for rel in refs:
    p=resolved(root,rel);curr=file_sha(p);comm=sha(head_content(root,rel))
    expected.append((family,str(row[idkey]),rel,pins[rel],curr,curr==pins[rel],comm==curr))
    items.append(f"{row[idkey]}\0{rel}\0{curr}")
    old.append(f"{row[idkey]}\0{rel}\0{pins[rel]}")
  new_root=sha(('\n'.join(sorted(items))+'\n').encode());root_old=sha(('\n'.join(sorted(old))+'\n').encode())
  roots[family]=new_root
  key=('evidence_root_sha256' if family=='android-source' else family.split('-')[1]+'_evidence_root_sha256')
  chk(root_old==att[key],'historical attestation root invalid '+family)
  origkey='source_dispositions_sha256' if family.endswith('-source') else family.split('-')[1]+'_dispositions_sha256'
  chk(att[origkey]==file_sha(out/'original'/disposition),'historical attestation-to-disposition mismatch '+family)
  ck= families['family_counts'][family]
  chk(ck['rows']==len(disp['rows']) and ck['bindings']==len(items) and ck['current_root_sha256']==new_root,'candidate count/root mismatch '+family)
 chk(roots==families['family_roots'],'G3 current root comparison failed')
 record=[(x['family'],str(x['obligation_id']),x['path'],x['historical_sha256'],x['current_sha256'],x['drift']=='UNCHANGED',x['git_head_match']) for x in rows]
 chk(Counter(record)==Counter(expected),'G3 candidate row reconciliation mismatch')
 hist=load(out/'UNMODIFIED_HISTORICAL_GATE_PREFLIGHT.json')
 stale=sum(not r[5] for r in expected)
 chk(hist['issue_count']==stale==reconciliation['historical_preflight_issues'],'historical preflight issue count mismatch')
 chk(hist['status']==reconciliation['historical_preflight_status']==('blocked' if stale else 'consistent'),'historical preflight status mismatch')
 prior=load(out/'HISTORICAL_PRIOR_SEAL_COMPARISON.json')
 for prefix in ('ANDROID','LIFE'):
  att=load(out/'original'/(prefix+'_G3_FINAL_ATTESTATION.json'))
  matches=[x for x in prior if x['consumer']==prefix]
  chk(len(matches)==len(att['prior_seals']),'prior seal count mismatch '+prefix)
  for rel,pin in att['prior_seals'].items():
   match=[x for x in matches if x['path']==rel]
   chk(len(match)==1,'prior seal reference multiplicity '+rel)
   curr=file_sha(resolved(root,rel));x=match[0]
   chk(x['historical_sha256']==pin and x['current_sha256']==curr and x['matches_historical']==(curr==pin),'prior seal comparison drift '+rel)
   chk(sha(head_content(root,rel))==curr,'prior seal not at HEAD '+rel)
 original_r04b=load(out/'original'/'REMED_R04B_SOURCE_EVIDENCE_BINDINGS.json')
 def r04b_items(rows):
  return ['\0'.join(str(row.get(k) or '') for k in
    ('ledger','row_id','reference','alias_target','kind','path','sha256','expected_sha256')) for row in rows]
 old_r04b_root=sha(('\n'.join(sorted(r04b_items(original_r04b['bindings'])))+'\n').encode())
 chk(old_r04b_root==original_r04b['binding_root_sha256'],'historical R04B source binding root invalid')
 source_ledgers=('CORE_G3_SOURCE_DISPOSITIONS.json','ENV_G3_SOURCE_DISPOSITIONS.json',
  'ANDROID_G3_SOURCE_DISPOSITIONS.json','LIFE_G3_SOURCE_DISPOSITIONS.json',
  'DIST_G3_SOURCE_DISPOSITION_LEDGER.json','TEST_G3_SOURCE_DISPOSITION_LEDGER.json')
 source_rows={}
 for ledger in source_ledgers:
  ledger_payload=load(root/ledger)
  rr=ledger_payload.get('rows')
  if not isinstance(rr,list):rr=list(ledger_payload.get('promises',[]))+list(ledger_payload.get('regressions',[]))
  for row in rr:
   ident=str(row.get('source_id') or row.get('id') or row.get('promise_id') or row.get('obligation_id') or '')
   if ident:source_rows[ident]=row
 r04b=load(out/'CURRENT_R04B_SOURCE_BINDINGS.json');br=r04b['bindings']
 chk(r04b['current_head']==head and r04b['current_tree']==tree,'R04B source SHA/tree mismatch')
 source_cross=load(out/'CURRENT_R04B_GIT_CROSSCHECK.json')
 source_by_path={(str(r.get('row_id')),str(r.get('reference')),str(r.get('path'))):r for r in source_cross}
 checked=[]
 for row in br:
  chk(bool(row.get('resolved')),'unresolved R04B source reference '+str(row.get('reference')))
  rel=str(row.get('path') or '')
  if not rel:
   chk(row.get('kind')=='source-row','unknown pathless R04B reference')
   ref=str(row.get('reference') or '')
   chk(ref.startswith('source:') and ref.count(':')==2,'invalid source-row reference')
   _,source_id,expected_digest=ref.split(':')
   source=source_rows.get(source_id)
   chk(source and source.get('source_row_sha256')==expected_digest==row.get('sha256')==row.get('expected_sha256'),
       'source row identity not independently resolved: '+ref)
  if rel:
   key=(str(row.get('row_id')),str(row.get('reference')),rel)
   chk(key in source_by_path,'missing R04B Git crosscheck '+str(key))
   candidate=source_by_path[key];curr=file_sha(resolved(root,rel))
   chk(candidate['observed_sha256']==curr==row['sha256'],'R04B file SHA not current '+rel)
   chk(sha(head_content(root,rel))==curr and candidate['matches_git_head'],'R04B blob mismatch '+rel)
  item='\0'.join(str(row.get(k) or '') for k in ('ledger','row_id','reference','alias_target','kind','path','sha256','expected_sha256'))
  checked.append(item)
 rroot=sha(('\n'.join(sorted(checked))+'\n').encode())
 chk(rroot==r04b['binding_root_sha256'],'R04B source root mismatch')
 mat=load(out/'CURRENT_R04B_PRESENTATION_CANDIDATE.json');mxc=load(out/'CURRENT_R04B_PRESENTATION_GIT_CROSSCHECK.json')
 mroots=sha(json.dumps(mat['rows'],sort_keys=True,separators=(',',':')).encode())
 chk(mroots==mat['matrix_root_sha256'],'R04B presentation root mismatch')
 rmap={(str(x.get('surface')),str(x.get('invariant')),str(x.get('path'))):x for x in mxc}
 for row in mat['rows']:
  for rel,pin in row['evidence_sha256'].items():
   key=(str(row['surface']),str(row['invariant']),rel)
   chk(key in rmap,'missing matrix crosscheck')
   curr=file_sha(resolved(root,rel));chk(curr==pin==rmap[key]['current_sha256'],'matrix current digest mismatch '+rel)
   chk(sha(head_content(root,rel))==curr and rmap[key]['git_head_match'],'matrix ref not at HEAD '+rel)
 assert_prior=load(out/'original'/ 'REMED_R04B_PRESENTATION_SURFACE_MATRIX.json')
 old_root=sha(json.dumps(assert_prior['rows'],sort_keys=True,separators=(',',':')).encode())
 chk(old_root==assert_prior['matrix_root_sha256'],'R04B historical matrix tampered')
 chk(bool(reconciliation['repository_unchanged_by_audit']),'generator detected checkout changes')
 chk(reconciliation['formal_release_qualification']=='NOT_QUALIFIED','release qualification label invalid')
 independent={'verification':'PASS_CURRENT_SOURCE_CANDIDATE_ONLY','source_commit':head,
  'g3_bindings':len(rows),'g3_drift':sum(not r[5] for r in expected),
  'independent_ledger_structure_passed':expected_structure['passed'],
  'independent_ledger_structure_total':expected_structure['total'],
  'r04b_bindings':len(br),'r04b_root_sha256':rroot,'presentation_root_sha256':mroots,
  'historical_g3_attestations_qualified':False,'native_termux_g3_qualified':False,
  'release_qualified':False,'independent_git_blob_checks':True}
 return independent

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--evidence',required=True,type=Path)
 p.add_argument('--repo',default=Path.home()/'Code/devtool',type=Path)
 args=p.parse_args()
 try:
  result=verify(args.evidence,args.repo)
  print(json.dumps(result,indent=2,sort_keys=True));return 0
 except Exception as e:print('INDEPENDENT VERIFICATION FAILED:',type(e).__name__,str(e),file=sys.stderr);return 2
if __name__=='__main__':raise SystemExit(main())

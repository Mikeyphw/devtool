"""RM15 + D1-D5: behavior, rollback-safe policy and adversarial Android receipts."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from contextlib import nullcontext
import zipfile
import pytest

from devtool.github_actions import GitHubActionsError
from devtool.actions_android_contracts import load_consumers, select_consumer, check_packaging, check_native_library_pages
from devtool.actions_android import _gradle_modules, android_plan, inspect_android_zip
from devtool.actions_unsigned import _manifest, unsigned_local_sign, inspect_unsigned
from devtool.gradle_options_repair import gradle_options_repair
from devtool.cli import build_parser

SHA='a'*40
CERT='b'*64
PACKAGE='com.example.bkx'

def contract(variant):
 return dict(id='bkx-'+variant,repository='Mikeyphw/bkx',workflow='bkx-android.yml',
             artifact='bkx-'+variant,apk=variant+'.apk',application_id=PACKAGE,
             variant=variant,build_type='release',target='bkx',project_dir='.',module='app',
             signing_identity_id='bkx-existing',signer_sha256=CERT,
             required_files=['lib/arm64-v8a/librclone.so'] if variant=='embeddedRelease' else [],
             forbidden_files=[] if variant=='embeddedRelease' else ['lib/arm64-v8a/librclone.so'])

def registry(root):
 p=root/'config/android-consumers.json';p.parent.mkdir(parents=True,exist_ok=True)
 p.write_text(json.dumps({'schema_version':1,'consumers':[contract('embeddedRelease'),contract('liteRelease')]}))

def elf_shared(page=16384):
 import struct
 data=bytearray(128)
 data[:4]=b'\x7fELF';data[4:6]=b'\x02\x01'
 struct.pack_into('<H',data,18,183)
 struct.pack_into('<Q',data,32,64)
 struct.pack_into('<H',data,54,56)
 struct.pack_into('<H',data,56,1)
 struct.pack_into('<I',data,64,1)
 struct.pack_into('<Q',data,112,page)
 return bytes(data)

def apk(path, embedded):
 with zipfile.ZipFile(path,'w') as z:
  z.writestr('AndroidManifest.xml',b'<manifest/>')
  z.writestr('classes.dex',b'dex')
  if embedded:z.writestr('lib/arm64-v8a/librclone.so',elf_shared())
 return path

def test_registry_variants_and_package_invariants(tmp_path):
 registry(tmp_path)
 a=select_consumer(tmp_path,repository='Mikeyphw/bkx',artifact='bkx-embeddedRelease')
 b=select_consumer(tmp_path,repository='Mikeyphw/bkx',artifact='bkx-liteRelease')
 assert a['variant']=='embeddedRelease' and b['variant']=='liteRelease'
 embedded=apk(tmp_path/'embedded.apk',True);lite=apk(tmp_path/'lite.apk',False)
 assert check_packaging(embedded,a)['required_verified']
 assert check_packaging(lite,b)['forbidden_verified']
 with pytest.raises(GitHubActionsError,match='required/forbidden'):check_packaging(embedded,b)
 with pytest.raises(GitHubActionsError,match='required/forbidden'):check_packaging(lite,a)
 with pytest.raises(GitHubActionsError,match='ambiguous'):select_consumer(tmp_path,repository='Mikeyphw/bkx')
 assert select_consumer(tmp_path,consumer_id='devtool-hud')['application_id'].endswith('devtoolhud')


def test_consumer_registry_refuses_duplicate_credentials_and_paths(tmp_path):
 registry(tmp_path)
 p=tmp_path/'config/android-consumers.json'
 data=json.loads(p.read_text());data['consumers'].append(data['consumers'][0]);p.write_text(json.dumps(data))
 with pytest.raises(GitHubActionsError,match='duplicate'):load_consumers(tmp_path)
 data['consumers'].pop();data['consumers'][0]['signer_sha256']='unknown';p.write_text(json.dumps(data))
 with pytest.raises(GitHubActionsError,match='LIFE'):load_consumers(tmp_path)
 data['consumers'][0]['signer_sha256']=CERT;data['consumers'][0]['required_files']=['../escape'];p.write_text(json.dumps(data))
 with pytest.raises(GitHubActionsError,match='member'):load_consumers(tmp_path)


def test_multiline_gradle_relocation_convention_flavors(tmp_path):
 (tmp_path/'settings.gradle.kts').write_text('''include(\n    ":app",\n    ":core"\n)\nproject(":app").projectDir = file("mobile")\n''')
 mobile=tmp_path/'mobile';mobile.mkdir()
 (mobile/'build.gradle.kts').write_text('''plugins { id("project.android.application") }\nandroid {\n defaultConfig { applicationId = "com.example.bkx" }\n productFlavors { create("embedded") { } create("lite") { } }\n}\n''')
 core=tmp_path/'core';core.mkdir();(core/'build.gradle.kts').write_text('plugins { id("com.android.library") }')
 conv=tmp_path/'build-logic/src/main/kotlin';conv.mkdir(parents=True)
 (conv/'project.android.application.gradle.kts').write_text('plugins { id("com.android.application") }')
 mods=_gradle_modules(tmp_path)
 assert set(mods)=={'app'}
 assert mods['app']['project_path']=='mobile'
 assert {'embeddedRelease','liteRelease'} <= set(mods['app']['variants'])
 assert mods['app']['variants']['embeddedRelease']['build_type']=='release'
 (tmp_path/'settings.gradle.kts').write_text('include(projectsFromEnvironment)')
 with pytest.raises(GitHubActionsError,match='Dynamic'): _gradle_modules(tmp_path)


def test_custom_variant_requires_run_bound_member_manifest(tmp_path, monkeypatch):
 p=tmp_path/'artifact.zip';payload=b'SIGNED APK BYTES';name='app-embedded-release.apk'
 manifest={'schema_version':1,'variant':'embeddedRelease','format':'apk','apk_file':name,
           'head_sha':SHA,'run_attempt':3,'sha256':hashlib.sha256(payload).hexdigest()}
 with zipfile.ZipFile(p,'w') as z:
  z.writestr(name,payload);z.writestr('android-variant-manifest.json',json.dumps(manifest))
 monkeypatch.setattr('devtool.actions_android._inspect_apk',lambda p:{'package':PACKAGE,'signer_sha256':CERT,
                    'signature_verified':True,'debug_signing':False})
 opts=dict(fmt='apk',variant='embeddedRelease',expected_package=PACKAGE,
           expected_cert=CERT,expected_source_sha=SHA,expected_run_attempt=3)
 assert inspect_android_zip(p,**opts)['signing_state']=='signed-verified'
 with pytest.raises(GitHubActionsError,match='mismatch'):
  inspect_android_zip(p,**{**opts,'expected_run_attempt':4})
 with pytest.raises(GitHubActionsError,match='mismatch'):
  inspect_android_zip(p,**{**opts,'variant':'liteRelease'})
 with zipfile.ZipFile(p,'w') as z:
  z.writestr(name,b'tamper');z.writestr('android-variant-manifest.json',json.dumps(manifest))
 with pytest.raises(GitHubActionsError,match='bytes'):
  inspect_android_zip(p,**opts)


def test_unsigned_variant_manifest_source_artifact_and_attempt(tmp_path):
 from devtool.actions_unsigned import _manifest
 registry(tmp_path);consumer=select_consumer(tmp_path,consumer_id='bkx-embeddedRelease')
 source=apk(tmp_path/'input.apk',True)
 manifest={'schema_version':1,'kind':'devtool.unsigned-android-apk','repository':consumer['repository'],
   'head_sha':SHA,'run_id':120,'run_attempt':3,'ref':'refs/heads/main',
   'workflow_path':'.github/workflows/bkx-android.yml','artifact_name':consumer['artifact'],
   'apk_file':consumer['apk'],'application_id':PACKAGE,'apk_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
   'apk_size_bytes':source.stat().st_size,'version_code':42,'version_name':'0.1','variant':'embeddedRelease',
   'format':'apk','debuggable':False,'signing_state':'unsigned','qualified_release':False,'source_attested':False}
 archive=tmp_path/'candidate.zip'
 with zipfile.ZipFile(archive,'w') as z:
  z.writestr('manifest.json',json.dumps(manifest));z.writestr(consumer['apk'],source.read_bytes())
 remote=dict(id=120,run_attempt=3,head_sha=SHA,head_branch='main')
 assert _manifest(archive,remote,dict(name=consumer['artifact']),expected_sha=SHA,consumer=consumer)[0]['variant']=='embeddedRelease'
 for key,value in [('variant','liteRelease'),('run_attempt',4),('application_id','other.pkg'),('head_sha','c'*40),('artifact_name','wrong')]:
  copy=dict(manifest);copy[key]=value
  with zipfile.ZipFile(archive,'w') as z:
   z.writestr('manifest.json',json.dumps(copy));z.writestr(consumer['apk'],source.read_bytes())
  with pytest.raises(GitHubActionsError):_manifest(archive,remote,dict(name=consumer['artifact']),expected_sha=SHA,consumer=consumer)


@pytest.mark.parametrize("variant,embedded", [("embeddedRelease",True),("liteRelease",False)])
def test_signed_variant_uses_registered_identity_16k_and_preserves_rclone(tmp_path,monkeypatch,variant,embedded):
 registry(tmp_path);root=tmp_path
 files=root/'.devtool/artifacts/android-unsigned';files.mkdir(parents=True)
 unsigned=apk(files/'unsigned.apk',embedded)
 digest=hashlib.sha256(unsigned.read_bytes()).hexdigest()
 metadata={'consumer_id':'bkx-'+variant,'package_sha256':digest,'version_code':42,'remote_repository':'Mikeyphw/bkx',
           'remote_run_id':120,'remote_run_attempt':3,'remote_artifact_id':123,'remote_head_sha':SHA}
 monkeypatch.setattr('devtool.actions_unsigned._imported',lambda *a:(unsigned,metadata))
 monkeypatch.setattr('devtool.actions_unsigned.inspect_unsigned',lambda *a,**kw:{'sha256':digest,'version_code':42,'package':PACKAGE})
 profile=SimpleNamespace(mode='devtool',identity_id='bkx-existing',expected_cert_sha256=CERT,alias='release',keystore=str(root/'file.ks'))
 monkeypatch.setattr('devtool.actions_setup._signing_profile',lambda *a,**kw:profile)
 monkeypatch.setattr('devtool.actions_setup._keystore',lambda *a:root/'file.ks')
 monkeypatch.setattr('devtool.actions_setup._signer_material',lambda *a:(root/'file.ks',{'DEVTOOL_ANDROID_SIGNING_STORE_PASSWORD':'priv','DEVTOOL_ANDROID_SIGNING_KEY_PASSWORD':'priv'},CERT))
 monkeypatch.setattr('devtool.actions_local_release._local_signing_password',lambda *a:nullcontext())
 monkeypatch.setattr('devtool.actions_unsigned._tool',lambda s:s)
 monkeypatch.setattr('devtool.actions_unsigned.register_paths',lambda *a,**k:[{'id':'signed'}])
 def verify(root,**kwargs):
  file=Path(kwargs['artifact'])
  assert kwargs['registered_application_id']==PACKAGE and kwargs['target']=='bkx'
  return {'artifact_sha256':hashlib.sha256(file.read_bytes()).hexdigest(),'signer_sha256':CERT}
 monkeypatch.setattr('devtool.actions_local_release.local_verify',verify)
 called=[]
 def run(args,**kw):
  called.append(args)
  if args[0]=='zipalign' and '-c' not in args:
   Path(args[-1]).write_bytes(Path(args[-2]).read_bytes())
  elif args[0]=='apksigner':
   Path(args[args.index('--out')+1]).write_bytes(Path(args[-1]).read_bytes())
  return SimpleNamespace(returncode=0)
 monkeypatch.setattr('devtool.actions_unsigned.subprocess.run',run)
 result=unsigned_local_sign(root,registry_id='id',execute=True)
 assert result['variant']==variant and result['signing_identity_id']=='bkx-existing'
 assert '-P' in called[0] and called[0][called[0].index('-P')+1]=='16'
 assert called[2][:5]==['zipalign','-c','-P','16','4']
 assert ('lib/arm64-v8a/librclone.so' in zipfile.ZipFile(result['signed_package']).namelist()) == embedded
 with pytest.raises(GitHubActionsError,match='not authorized'):unsigned_local_sign(root,registry_id='id',identity_id='wrong')


def test_cli_registered_consumer_routes_and_jvm_repair_grammar():
 parser=build_parser()
 assert parser.parse_args(['actions','android','unsigned-plan','--ref','main','--consumer','bkx-embeddedRelease']).consumer=='bkx-embeddedRelease'
 assert parser.parse_args(['actions','android','options-repair','--policy-target','bkx']).policy_target=='bkx'


def test_workflow_custom_release_is_protected_and_16k():
 text=(Path(__file__).parents[1]/'.github/workflows/devtool-android-variants.yml').read_text()
 assert 'endsWith(inputs.variant' in text and 'TASK_BUILD_TYPE' in text
 assert 'zipalign" -P 16 -f 4' in text and 'zipalign" -c -P 16 4' in text
 assert 'android-variant-manifest.json' in text and 'GITHUB_REF_PROTECTED' in text


def test_protected_gradle_jvm_migration_previews_and_is_atomic(tmp_path,monkeypatch):
 from devtool import config
 monkeypatch.setattr(config,'load_project_config', lambda *a,**k: SimpleNamespace(runner_options={'gradle_heap_mb':2048}))
 f=tmp_path/'gradle.properties'
 f.write_text('org.gradle.jvmargs=-Xmx1024m -Dfile.encoding=UTF-8\norg.gradle.parallel=true\n')
 report=gradle_options_repair(tmp_path,project_dir='.',target='bkx')
 assert report['protected_heap_mb']==2048 and report['legacy_heap_mb']==1024
 assert '-Xmx' not in report['after'] and '-Dfile.encoding=UTF-8' in report['after']
 assert f.read_text()==report['before']
 with pytest.raises(GitHubActionsError,match='changed since preview'):
  gradle_options_repair(tmp_path,project_dir='.',target='bkx',execute=True,expected_sha256='0'*64)
 applied=gradle_options_repair(tmp_path,project_dir='.',target='bkx',execute=True,expected_sha256=report['source_sha256'])
 assert applied['applied'] and f.read_text()==report['after']
 f.write_text('org.gradle.jvmargs=-Xmx4096m\n')
 with pytest.raises(GitHubActionsError,match='downgrade'):
  gradle_options_repair(tmp_path,project_dir='.',target='bkx')
 f.write_text('org.gradle.jvmargs=-Xmx1024m -XX:+Unsafe\n')
 with pytest.raises(GitHubActionsError,match='review'):
  gradle_options_repair(tmp_path,project_dir='.',target='bkx')


def test_nested_android_plan_rejects_library_and_unregistered_variant(tmp_path,monkeypatch):
 project=tmp_path/'appgradle';project.mkdir()
 (project/'settings.gradle.kts').write_text('include(":app", ":library")')
 a=project/'app';a.mkdir();(a/'build.gradle.kts').write_text('plugins { id("com.android.application") }\nandroid { productFlavors { create("embedded") {} create("lite") {} } defaultConfig { applicationId = "com.example.bkx" }}')
 b=project/'library';b.mkdir();(b/'build.gradle.kts').write_text('plugins { id("com.android.library") }')
 (project/'gradlew').write_text('#!/bin/sh\n')
 (project/'gradle/wrapper').mkdir(parents=True);(project/'gradle/wrapper/gradle-wrapper.properties').write_text('distributionUrl=dummy')
 monkeypatch.setattr('devtool.actions_android._git',lambda root,*args:SHA if args[0]=='rev-parse' else '')
 monkeypatch.setattr('devtool.actions_android.dispatch_workflow',lambda *a,**k:{'pre_dispatch_sha':SHA})
 result=android_plan(object(),'Mikeyphw/bkx',tmp_path,module='app',variant='embeddedRelease',fmt='apk',ref='main',expected_package=PACKAGE,project_dir='appgradle')
 assert result['build_type']=='release' and result['variant']=='embeddedRelease'
 with pytest.raises(GitHubActionsError,match='statically discoverable'):
  android_plan(object(),'Mikeyphw/bkx',tmp_path,module='library',variant='release',fmt='apk',ref='main',project_dir='appgradle')
 with pytest.raises(GitHubActionsError,match='Unknown'):
  android_plan(object(),'Mikeyphw/bkx',tmp_path,module='app',variant='pirateRelease',fmt='apk',ref='main',project_dir='appgradle')


def test_two_variants_import_independently_with_real_receipts(tmp_path,monkeypatch):
 from devtool.actions_unsigned import unsigned_import, _imported
 registry(tmp_path)
 candidates={}
 for number,variant in ((201,'embeddedRelease'),(202,'liteRelease')):
  original=apk(tmp_path/(variant+'-raw.apk'),variant=='embeddedRelease')
  c=select_consumer(tmp_path,consumer_id='bkx-'+variant)
  doc={'schema_version':1,'kind':'devtool.unsigned-android-apk','repository':'Mikeyphw/bkx',
   'head_sha':SHA,'run_id':120,'run_attempt':3,'ref':'refs/heads/main',
   'workflow_path':'.github/workflows/bkx-android.yml','artifact_name':c['artifact'],
   'apk_file':c['apk'],'application_id':PACKAGE,'apk_sha256':hashlib.sha256(original.read_bytes()).hexdigest(),
   'apk_size_bytes':original.stat().st_size,'version_code':42,'version_name':'0.1',
   'variant':variant,'format':'apk','debuggable':False,'signing_state':'unsigned',
   'qualified_release':False,'source_attested':False}
  package=tmp_path/(variant+'.zip')
  with zipfile.ZipFile(package,'w') as z:
   z.writestr('manifest.json',json.dumps(doc));z.writestr(c['apk'],original.read_bytes())
  candidates[number]=(c,package)
 class Client:
  def get_run(self,slug,run_id):
   return dict(id=120,status='completed',conclusion='success',event='workflow_dispatch',
     repository={'full_name':'Mikeyphw/bkx'},head_sha=SHA,run_attempt=3,workflow_id=77,head_branch='main')
  def workflow(self,slug,name):return dict(id=77,path='.github/workflows/bkx-android.yml')
  def get_artifact(self,slug,aid):
   c,p=candidates[aid]
   return {'id':aid,'name':c['artifact'],'expired':False,'size_in_bytes':p.stat().st_size,
    'digest':'sha256:'+hashlib.sha256(p.read_bytes()).hexdigest(),
    'workflow_run':{'id':120,'head_sha':SHA}}
 monkeypatch.setattr('devtool.actions_unsigned.verify_download',lambda client,slug,run,aid,root:{'file':str(candidates[aid][1])})
 def inspect(candidate,**kw):
  with zipfile.ZipFile(candidate) as z: names=z.namelist()
  return {'sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),
          'size_bytes':candidate.stat().st_size,'version_code':42,'version_name':'0.1',
          'package':PACKAGE,'debuggable':False,'signing_state':'unsigned','signature_verified':False}
 monkeypatch.setattr('devtool.actions_unsigned.inspect_unsigned',inspect)
 client=Client()
 imported=[]
 for aid in (201,202):
  result=unsigned_import(client,'Mikeyphw/bkx',tmp_path,run_id=120,artifact_id=aid,expected_sha=SHA)
  assert result['consumer_id']==candidates[aid][0]['id'] and result['unsigned_verified']
  assert not result['qualified_release'] and not result['locally_signed']
  signed_input,meta=_imported(tmp_path,result['registry_id'])
  assert meta['variant']==candidates[aid][0]['variant'] and meta['remote_artifact_id']==aid
  imported.append(result['registry_id'])
 assert len(set(imported))==2
 # A consumer registry rewrite after import must not weaken rclone exclusions.
 p=tmp_path/'config/android-consumers.json'
 saved=p.read_text()
 revised=json.loads(saved)
 revised['consumers'][1]['forbidden_files']=[]
 p.write_text(json.dumps(revised))
 with pytest.raises(GitHubActionsError,match='receipt'):
  _imported(tmp_path, imported[1])
 p.write_text(saved)
 with pytest.raises(GitHubActionsError,match='another repository'):
  unsigned_import(client,'Other/repo',tmp_path,run_id=120,artifact_id=201,expected_sha=SHA)
 with pytest.raises(GitHubActionsError):
  unsigned_import(client,'Mikeyphw/bkx',tmp_path,run_id=120,artifact_id=202,expected_sha='c'*40)


def test_page_aligned_native_elf_is_verified_not_assumed(tmp_path):
 valid=apk(tmp_path/'valid.apk',True)
 assert check_native_library_pages(valid)['native_libraries_verified']==['lib/arm64-v8a/librclone.so']
 bad=tmp_path/'wrong-page.apk'
 with zipfile.ZipFile(bad,'w') as z:
  z.writestr('lib/arm64-v8a/librclone.so',elf_shared(4096))
 with pytest.raises(GitHubActionsError,match='16 KiB'):
  check_native_library_pages(bad)
 absent=apk(tmp_path/'no-libs.apk',False)
 assert check_native_library_pages(absent)['native_libraries_verified']==[]

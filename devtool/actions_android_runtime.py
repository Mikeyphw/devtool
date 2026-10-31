"""Explicit, trusted Gradle model evaluation for Android convention-plugin variants.

NOT a sandbox. Evaluating arbitrary Gradle plugins executes repository code, and is
only allowed on explicit user request after reviewing the pinned, clean source.
Never run this from read-only static discovery or an untrusted artifact import.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from .github_actions import GitHubActionsError

_INIT = r'''
import groovy.json.JsonOutput
import java.nio.file.Files

gradle.projectsEvaluated {
    def root = gradle.rootProject
    def result = [:]
    root.allprojects.each { p ->
        if (p.plugins.hasPlugin('com.android.application')) {
            def android = p.extensions.findByName('android')
            def variants = [:]
            android.applicationVariants.all { v ->
                def n = String.valueOf(v.name)
                def b = String.valueOf(v.buildType.name)
                def f = String.valueOf(v.flavorName ?: '')
                def parts = []
                v.productFlavors.each { flavor -> parts.add(String.valueOf(flavor.name)) }
                variants[n] = [build_type:b, flavor:f, flavors:parts]
            }
            def id = null
            try { id = android.defaultConfig.applicationId } catch (Exception ignored) { }
            result[p.path.substring(1)] = [module:p.path.substring(1), application_id:id,
                variants:variants, project_path:root.projectDir.toPath().relativize(p.projectDir.toPath()).toString().replace('\\','/')]
        }
    }
    def outfile = System.getenv('DEVTOOL_ANDROID_DISCOVERY_OUTPUT')
    if (!outfile) throw new GradleException('Devtool model output missing')
    Files.write(new File(outfile).toPath(), JsonOutput.toJson(result).getBytes('UTF-8'))
}
'''
_SAFE_NAME = re.compile(r'[a-z][A-Za-z0-9]{0,60}\Z')
_SAFE_MODULE = re.compile(r'[A-Za-z][A-Za-z0-9_-]*(?::[A-Za-z][A-Za-z0-9_-]*){0,7}\Z')
_SAFE_REL = re.compile(r'[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\Z')


def discover_runtime(project: Path, *, repository: Path, timeout: int = 120) -> dict:
    """Execute checked-in Gradle configuration only after explicit trust opt-in."""
    repository=repository.resolve();project=project.resolve()
    if not project.is_relative_to(repository):
        raise GitHubActionsError('Runtime Gradle project must be within the repository')
    from .actions_android import _gradle_bootstrap, _git
    from .android._standalone import enforce_hosted_gradle_only
    try:
        enforce_hosted_gradle_only(repository)
    except Exception as exc:
        raise GitHubActionsError(str(exc)) from exc
    _gradle_bootstrap(project)
    if _git(repository,'status','--porcelain','--untracked-files=normal'):
        raise GitHubActionsError('Runtime Gradle discovery requires clean reviewed source')
    sha=_git(repository,'rev-parse','HEAD')
    if not re.fullmatch(r'[0-9a-f]{40}',sha):
        raise GitHubActionsError('Runtime Gradle discovery requires pinned Git source')
    # This grants explicit execution to the current repository's Gradle code.
    # No GitHub credential, LIFE keystore, or signing variables are provided.
    env={k:v for k,v in os.environ.items() if k in ('PATH','HOME','TMPDIR','JAVA_HOME','ANDROID_HOME','ANDROID_SDK_ROOT','LANG','LC_ALL','TERM','GRADLE_USER_HOME')}
    for k in ('GH_TOKEN','GITHUB_TOKEN','DEVTOOL_RELEASE_STORE_PASSWORD','DEVTOOL_RELEASE_KEY_PASSWORD'):
        env.pop(k,None)
    with tempfile.TemporaryDirectory(prefix='devtool-gradle-model-') as tmp:
        out=Path(tmp)/'result.json';init=Path(tmp)/'model.init.gradle'
        init.write_text(_INIT,encoding='utf-8')
        env['DEVTOOL_ANDROID_DISCOVERY_OUTPUT']=str(out)
        try:
            run=subprocess.run([str(project/'gradlew'),'--offline','--no-daemon','--console=plain',
                                '--init-script',str(init),'help'],cwd=project,env=env,
                               capture_output=True,text=True,timeout=timeout,check=False)
        except (OSError,subprocess.TimeoutExpired):
            raise GitHubActionsError('Reviewed Gradle model execution unavailable or timed out') from None
        if run.returncode or not out.is_file() or out.is_symlink() or out.stat().st_size>256*1024:
            raise GitHubActionsError('Gradle model execution failed or returned no bounded model')
        try: raw=json.loads(out.read_text(encoding='utf-8'))
        except (OSError,UnicodeError,ValueError):
            raise GitHubActionsError('Gradle model returned invalid JSON') from None
    if _git(repository,'rev-parse','HEAD')!=sha or _git(repository,'status','--porcelain','--untracked-files=normal'):
        raise GitHubActionsError('Gradle model evaluation changed reviewed repository source')
    if not isinstance(raw,dict) or len(raw)>128:raise GitHubActionsError('Gradle model module count invalid')
    modules={}
    for module,item in raw.items():
        if not isinstance(module,str) or not _SAFE_MODULE.fullmatch(module) or not isinstance(item,dict):
            raise GitHubActionsError('Gradle model supplied invalid module identity')
        rel=item.get('project_path');variants=item.get('variants');pkg=item.get('application_id')
        if not isinstance(rel,str) or rel.startswith('.') or not _SAFE_REL.fullmatch(rel) or not isinstance(variants,dict):
            raise GitHubActionsError('Gradle model supplied unsafe project path or variants')
        location=(project/rel).resolve()
        if (project/rel).is_symlink() or not location.is_relative_to(project) or not location.is_dir():
            raise GitHubActionsError('Gradle model module escapes reviewed project')
        if pkg is not None and (not isinstance(pkg,str) or not re.fullmatch(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+',pkg)):
            raise GitHubActionsError('Gradle model returned invalid application ID')
        clean={}
        for variant,spec in variants.items():
            if not isinstance(variant,str) or not _SAFE_NAME.fullmatch(variant) or not isinstance(spec,dict):
                raise GitHubActionsError('Gradle runtime model has invalid variant')
            bt=spec.get('build_type');fl=spec.get('flavor','');parts=spec.get('flavors',[])
            if not isinstance(bt,str) or not _SAFE_NAME.fullmatch(bt) or not isinstance(fl,str) or not re.fullmatch(r'[a-zA-Z0-9]{0,60}',fl) or not isinstance(parts,list) or len(parts)>8 or any(not isinstance(x,str) or not _SAFE_NAME.fullmatch(x) for x in parts):
                raise GitHubActionsError('Gradle runtime model has invalid build-type/flavor')
            if variant != ((fl+bt.title()) if fl else bt):
                raise GitHubActionsError('Gradle runtime variant and build type disagree')
            clean[variant]={'build_type':bt,'flavor':fl,'flavors':parts,'dimensions':[]}
            if len(clean)>96:raise GitHubActionsError('Gradle runtime variant limit exceeded')
        modules[module]={'module':module,'application_id':pkg,'application_id_static':False,
                        'project_path':rel,'variants':clean,'source':'reviewed-gradle-execution',
                        'source_sha':sha}
    if not modules:raise GitHubActionsError('Gradle model returned no Android application modules')
    return modules

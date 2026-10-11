"""JVM-only Android Live HUD operation construction regression."""
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC=ROOT/'android/devtool-live-hud/app/src/main/java/io/github/mikeceead/devtoolhud'


def test_native_android_builder_compiles_and_enforces_dispatch_approval(tmp_path):
    javac=shutil.which('javac'); java=shutil.which('java')
    if not javac or not java: pytest.skip('JDK unavailable')
    sample=tmp_path/'HudPipelineCheck.java'
    sample.write_text('''package io.github.mikeceead.devtoolhud;
public final class HudPipelineCheck {
    static void check(boolean b) { if(!b) throw new AssertionError(); }
    public static void main(String[] argv) {
        DevtoolRunConfig c=new DevtoolRunConfig(); c.repo="$HOME/Code/devtool"; c.target="";
        c.autoHud=false; c.copy=false; c.copyViaTermux=false;
        c.operation="pipeline-plan";
        check(DevtoolCommandBuilder.build(c).contains("actions pipeline plan --file"));
        c.operation="pipeline-status";
        boolean invalid=false;
        try { DevtoolCommandBuilder.build(c); } catch(IllegalArgumentException e){ invalid=true; }
        check(invalid);
        c.pipelineId="a".repeat(32);
        check(DevtoolCommandBuilder.build(c).contains("actions pipeline status --pipeline-id "+c.pipelineId));
        c.operation="pipeline-resume"; c.assumeYes=false;
        invalid=false;
        try{DevtoolCommandBuilder.build(c);}catch(IllegalArgumentException e){invalid=true;}
        check(invalid);
        c.assumeYes=true;
        check(DevtoolCommandBuilder.build(c).contains("actions pipeline resume --pipeline-id "+c.pipelineId+" --yes"));
        c.operation="pipeline-run";c.pipelineFile="a/../manifest.json";
        invalid=false;
        try{DevtoolCommandBuilder.build(c);}catch(IllegalArgumentException e){invalid=true;}
        check(invalid);
        c.pipelineFile=".devtool/actions-pipeline.json";
        check(DevtoolCommandBuilder.build(c).contains("actions pipeline run --file"));
        DevtoolCommandImporter.ImportResult imported = DevtoolCommandImporter.parse(DevtoolCommandBuilder.build(c));
        check(imported.config.operation.equals("pipeline-run"));
        check(imported.config.pipelineFile.equals(c.pipelineFile));
        c.operation="pipeline-status";
        imported = DevtoolCommandImporter.parse(DevtoolCommandBuilder.build(c));
        check(imported.config.pipelineId.equals(c.pipelineId));
    }
}
''',encoding='utf-8')
    classes=tmp_path/'classes';classes.mkdir()
    proc=subprocess.run([javac,'-d',str(classes),str(SRC/'DevtoolRunConfig.java'),str(SRC/'DevtoolCommandBuilder.java'),str(SRC/'DevtoolCommandImporter.java'),str(sample)],capture_output=True,text=True,timeout=40)
    assert proc.returncode==0,proc.stderr
    proc=subprocess.run([java,'-cp',str(classes),'io.github.mikeceead.devtoolhud.HudPipelineCheck'],capture_output=True,text=True,timeout=15)
    assert proc.returncode==0,proc.stderr

"""Compile the exact Hadoop source with one explicit holder-guard mutation."""
from pathlib import Path
import os
import hashlib
import json
import subprocess
import zipfile
import difflib

BASE=Path(__file__).resolve().parent
MUTATION=BASE/'mutation'
HADOOP=Path(os.environ['HADOOP_HOME'])
JDK=Path(os.environ['JAVA_HOME'])
NAME='org/apache/hadoop/hdfs/server/namenode/FSNamesystem.java'

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    jar=HADOOP/'share/hadoop/hdfs/sources/hadoop-hdfs-3.3.6-sources.jar'
    original=zipfile.ZipFile(jar).read(NAME)
    text=original.decode('utf-8')
    before='if (holder != null && !owner.equals(holder)) {'
    after='if (false && holder != null && !owner.equals(holder)) {'
    if text.count(before)!=1:raise RuntimeError('Expected exactly one holder guard')
    derived=text.replace(before,after)
    original_path=MUTATION/'original_source'/NAME;source=MUTATION/'src'/NAME
    original_path.parent.mkdir(parents=True,exist_ok=True);source.parent.mkdir(parents=True,exist_ok=True)
    original_path.write_bytes(original);source.write_text(derived,encoding='utf-8')
    classes=MUTATION/'classes';classes.mkdir(exist_ok=True)
    cp=(BASE/'classpath.linux.txt').read_text().splitlines()
    cmd=[str(JDK/'bin/javac'),'-proc:none','-g','--release','8','-encoding','UTF-8','-cp',os.pathsep.join(cp),'-d',str(classes),str(source)]
    run=subprocess.run(cmd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=180)
    (MUTATION/'compile.log').write_text(run.stdout+run.stderr,encoding='utf-8')
    (MUTATION/'source.diff').write_text(''.join(difflib.unified_diff(text.splitlines(True),derived.splitlines(True),fromfile=NAME+' (official3.3.6)',tofile=NAME+' (derived holder-check removed)')),encoding='utf-8')
    manifest={'kind':'source mutation, not bytecode patch','hadoop_version':'3.3.6','source_jar':str(jar),'source_jar_sha256':sha(jar),
              'source_entry':NAME,'original_source_sha256':hashlib.sha256(original).hexdigest(),'mutant_source_sha256':sha(source),
              'method':'<org.apache.hadoop.hdfs.server.namenode.FSNamesystem: org.apache.hadoop.hdfs.server.namenode.INodeFile checkLease(org.apache.hadoop.hdfs.server.namenode.INodesInPath,java.lang.String,long)>',
              'mutation_before':before,'mutation_after':after,'mutation_line':next(i+1 for i,l in enumerate(text.splitlines()) if before in l),
              'change_scope':'Only the non-null holder-versus-owner rejection condition is disabled. Missing/nonfile/closed/deleted inode guards remain.',
              'compile_command':cmd,'compile_exit_code':run.returncode,'class_dir':str(classes),'classes_sha256':{str(p.relative_to(classes)):sha(p) for p in sorted(classes.rglob('*.class'))}}
    # Keep before/after disassembly as a separate concrete check, even though
    # the holder-check variant is in source and every compiled class is hashed.
    for label,classpath in [('before',os.pathsep.join(cp)),('after',os.pathsep.join([str(classes)]+cp))]:
        javap=[str(JDK/'bin/javap'),'-classpath',classpath,'-p','-c','-l','org.apache.hadoop.hdfs.server.namenode.FSNamesystem']
        disassembly=subprocess.run(javap,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        (MUTATION/('javap_'+label+'.txt')).write_text(disassembly.stdout+disassembly.stderr,encoding='utf-8')
        manifest['javap_'+label]={'command':javap,'exit_code':disassembly.returncode,'sha256':sha(MUTATION/('javap_'+label+'.txt'))}
    (MUTATION/'mutation_manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'compile_exit_code':run.returncode,'compiled_classes':len(manifest['classes_sha256']),'class_dir':str(classes)},indent=2),flush=True)
    if run.returncode:raise SystemExit(1)

if __name__=='__main__':main()

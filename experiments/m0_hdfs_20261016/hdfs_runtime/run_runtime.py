"""Run under WSL Ubuntu: real HDFS3.3.6 cluster and separate Java client JVM."""
from __future__ import annotations
from pathlib import Path
import argparse
import hashlib
import json
import os
import platform
import socket
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile

BASE=Path(__file__).resolve().parent
HADOOP=Path(os.environ['HADOOP_HOME'])
JDK=Path(os.environ['JAVA_HOME'])
JAVA=str(JDK/'bin/java');JAVAC=str(JDK/'bin/javac')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def write(path,data):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(data,indent=2)+'\n',encoding='utf-8')

def run(cmd,log,timeout=120):
    start=time.monotonic()
    try:
        p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',timeout=timeout)
        log.write_text(p.stdout,encoding='utf-8')
        return {'command':cmd,'exit_code':p.returncode,'log':str(log),'elapsed_seconds':time.monotonic()-start}
    except subprocess.TimeoutExpired as error:
        log.write_bytes(error.stdout or b'')
        return {'command':cmd,'exit_code':None,'timeout':True,'log':str(log),'elapsed_seconds':time.monotonic()-start}

def dependencies():
    deps=BASE/'deps';deps.mkdir(exist_ok=True)
    for name,url in {
        'junit-4.13.2.jar':'https://repo.maven.apache.org/maven2/junit/junit/4.13.2/junit-4.13.2.jar',
        'hamcrest-core-1.3.jar':'https://repo.maven.apache.org/maven2/org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar',
        'mockito-core-3.12.4.jar':'https://repo.maven.apache.org/maven2/org/mockito/mockito-core/3.12.4/mockito-core-3.12.4.jar',
        'byte-buddy-1.11.13.jar':'https://repo.maven.apache.org/maven2/net/bytebuddy/byte-buddy/1.11.13/byte-buddy-1.11.13.jar',
        'byte-buddy-agent-1.11.13.jar':'https://repo.maven.apache.org/maven2/net/bytebuddy/byte-buddy-agent/1.11.13/byte-buddy-agent-1.11.13.jar',
        'objenesis-3.2.jar':'https://repo.maven.apache.org/maven2/org/objenesis/objenesis/3.2/objenesis-3.2.jar',
    }.items():
        target=deps/name
        if not target.exists():
            with urllib.request.urlopen(url,timeout=60) as response:target.write_bytes(response.read())
    paths=[HADOOP/'share/hadoop/common/hadoop-common-3.3.6.jar',HADOOP/'share/hadoop/common/hadoop-common-3.3.6-tests.jar']
    paths+=sorted((HADOOP/'share/hadoop/hdfs').glob('*.jar'))
    names={p.name for p in paths}
    for directory in [HADOOP/'share/hadoop/common/lib',HADOOP/'share/hadoop/hdfs/lib',deps]:
        for p in sorted(directory.glob('*.jar')):
            if p.name not in names:paths.append(p);names.add(p.name)
    if not all(p.exists() for p in paths):raise FileNotFoundError('Missing dependency')
    return paths

def execute_case(mode,cp,mutant_dir=None):
    number=1
    runs=BASE/'runs';runs.mkdir(exist_ok=True)
    while (runs/f'run_{number:03d}_{mode}').exists():number+=1
    folder=runs/f'run_{number:03d}_{mode}';folder.mkdir()
    ready=folder/'server_ready.json';output=folder/'client_result.json'
    servercp=os.pathsep.join(([str(mutant_dir)] if mutant_dir else [])+[str(BASE/'classes')]+[str(p) for p in cp])
    clientcp=os.pathsep.join([str(BASE/'classes')]+[str(p) for p in cp])
    command=[JAVA,'-Xmx2g','-XX:ActiveProcessorCount=4','-Djava.net.preferIPv4Stack=true','-Dlog4j.configuration='+(BASE/'log4j.properties').as_uri(),'-cp',servercp,'e2e.hdfs.HdfsServer',str(folder/'state'),str(ready)]
    record={'mode':mode,'server_command':command,'folder':str(folder)}
    # Save exact input identities next to each execution.
    snapshot=json.loads((BASE/'manifest.json').read_text())
    snapshot['active_server_classpath']=servercp
    snapshot['active_client_classpath']=clientcp
    if mutant_dir:snapshot['mutation_manifest_sha256']=sha(mutant_dir.parent/'mutation_manifest.json')
    shutil.copytree(BASE/'src',folder/'input_sources')
    shutil.copytree(BASE/'classes',folder/'input_classes')
    write(folder/'inputs_manifest.json',snapshot)
    record['input_manifest']=str(folder/'inputs_manifest.json')
    record['input_manifest_sha256']=sha(folder/'inputs_manifest.json')
    with (folder/'server.log').open('w',encoding='utf-8') as log:
        server=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,text=True)
        record['server_pid']=server.pid
        try:
            deadline=time.monotonic()+120
            while not ready.exists():
                if server.poll() is not None:raise RuntimeError('Server exited before readiness')
                if time.monotonic()>deadline:raise TimeoutError('Server startup timed out')
                time.sleep(0.2)
            status=json.loads(ready.read_text());record['server_ready']=status
            record['socket_inventory']=run(['ss','-ltnp'],folder/'listen_sockets.log')
            uri=status['uri'];host_port=uri.split('://',1)[1]
            host,port=host_port.rsplit(':',1)
            with socket.create_connection((host,int(port)),timeout=5):record['tcp_ready']=True
            client_command=[JAVA,'-Xmx1g','-XX:ActiveProcessorCount=4','-Djava.net.preferIPv4Stack=true','-Dlog4j.configuration='+(BASE/'log4j.properties').as_uri(),'-cp',clientcp,
                            'e2e.hdfs.HdfsClient',uri,str(output),'true' if mode=='holder_check_removed' else 'false']
            record['client_execution']=run(client_command,folder/'client.log',timeout=90)
            record['client_result']=json.loads(output.read_text()) if output.exists() else None
            record['all_pass']=bool(record['client_result'] and record['client_result']['all_pass'] and record['client_execution']['exit_code']==0
                                    and record['client_result']['pid']!=status['pid'])
        except Exception as error:
            record['error']=f'{type(error).__name__}: {error}';record['all_pass']=False
        finally:
            if server.poll() is None:
                try:server.communicate(input='\n',timeout=35)
                except subprocess.TimeoutExpired:server.kill();server.wait(timeout=10);record['forced_shutdown']=True
            record['server_exit_code']=server.returncode
    if record.get('server_ready'):
        status=record['server_ready']
        offline=folder/'server_off_result.json'
        fixture=(record.get('client_result') or {}).get('fixture',{})
        offcmd=[JAVA,'-Xmx512m','-XX:ActiveProcessorCount=2','-Djava.net.preferIPv4Stack=true',
                '-Dlog4j.configuration='+(BASE/'log4j.properties').as_uri(),'-cp',clientcp,'e2e.hdfs.HdfsOfflineProbe',
                status['uri'],str(offline),str(fixture.get('file_id_a',16386)),str(fixture.get('owner_a','m0-holder'))]
        record['server_off_execution']=run(offcmd,folder/'server_off.log',timeout=30)
        record['server_off_result']=json.loads(offline.read_text()) if offline.exists() else None
        record['all_pass']=bool(record['all_pass'] and record['server_exit_code']==0 and record['server_off_execution']['exit_code']==0
                                and record['server_off_result'] and record['server_off_result']['pass'])
    write(folder/'run_record.json',record)
    return record

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mutant-dir',default=None);args=parser.parse_args()
    BASE.mkdir(parents=True,exist_ok=True);(BASE/'classes').mkdir(exist_ok=True)
    if sys.platform!='linux':raise RuntimeError('Run this script under WSL Ubuntu')
    cp=dependencies()
    (BASE/'classpath.linux.txt').write_text('\n'.join(map(str,cp))+'\n',encoding='utf-8')
    compilation=run([JAVAC,'-proc:none','-g','-encoding','UTF-8','-cp',os.pathsep.join(map(str,cp)),'-d',str(BASE/'classes')]
                    +[str(p) for p in sorted((BASE/'src').glob('*.java'))],BASE/'compile.log')
    if compilation['exit_code']!=0:write(BASE/'compile_failure.json',compilation);raise RuntimeError('Compilation failed')
    manifest={'environment':{'platform':platform.platform(),'python':sys.version,'java':run([JAVA,'-version'],BASE/'java_version.log')},
              'classpath':[str(p) for p in cp],'dependency_sha256':{str(p):sha(p) for p in cp},
              'source_sha256':{str(p.relative_to(BASE)):sha(p) for p in sorted((BASE/'src').glob('*.java'))},
              'class_sha256':{str(p.relative_to(BASE)):sha(p) for p in sorted((BASE/'classes').rglob('*.class'))},
              'runner_sha256':sha(__file__),'logging_config_sha256':sha(BASE/'log4j.properties'),
              'compilation':compilation,'reproduce':[sys.executable,str(Path(__file__).resolve())]}
    write(BASE/'manifest.json',manifest)
    result=execute_case('holder_check_removed' if args.mutant_dir else 'original',cp,Path(args.mutant_dir) if args.mutant_dir else None)
    write(BASE/'runtime_result_latest.json',result)
    print(json.dumps({k:v for k,v in result.items() if k not in ('server_command','client_result')},indent=2),flush=True)
    if result.get('client_result'):
        for case in result['client_result']['cases']:print(case['id'],case['outcome'],case['pass'],flush=True)
    raise SystemExit(0 if result['all_pass'] else 1)

if __name__=='__main__':main()

"""Aggregate explicitly selected actual runs; never substitute an older success."""
from pathlib import Path
import argparse
import hashlib
import json

BASE=Path(__file__).resolve().parent
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def summarize(original_path,mutant_path):
    original_path=Path(original_path).resolve();mutant_path=Path(mutant_path).resolve()
    original=json.loads(original_path.read_text());mutant=json.loads(mutant_path.read_text())
    if original['mode']!='original' or mutant['mode']!='holder_check_removed':raise ValueError('Wrong run modes')
    before={x['id']:x for x in original['client_result']['cases']}
    after={x['id']:x for x in mutant['client_result']['cases']}
    if set(before)!=set(after):raise ValueError('Paired case IDs differ')
    pairs=[{'id':key,'original':before[key]['outcome'],'mutant':after[key]['outcome'],
            'changed':before[key]['outcome']!=after[key]['outcome']} for key in before]
    changed={x['id'] for x in pairs if x['changed']}
    expected_changed={'cross_holder','cross_valid_file_id_wrong_holder'}
    runs=[]
    for path in sorted((BASE/'runs').glob('*/run_record.json')):
        record=json.loads(path.read_text())
        runs.append({'path':str(path.relative_to(BASE)),'sha256':sha(path),'mode':record['mode'],
                     'all_pass':record['all_pass'],'error':record.get('error'),
                     'client_cases':len((record.get('client_result') or {}).get('cases',[]))})
    report={'framework':'Apache Hadoop HDFS','version':'3.3.6',
            'revision':original['server_ready']['hadoop_revision'],
            'execution':'Hadoop3.3.6 MiniDFSCluster with a separate Java client and two DFSClient instances',
            'mocking':'No mock or spy in experiment fixture. Mockito jars satisfy pre-existing Hadoop test-utility class signatures.',
            'original_run_record':str(original_path.relative_to(BASE)),'original_run_record_sha256':sha(original_path),
            'mutant_run_record':str(mutant_path.relative_to(BASE)),'mutant_run_record_sha256':sha(mutant_path),
            'paired_cases':pairs,'paired_changed_cases':sorted(changed),'paired_expected_changed_cases':sorted(expected_changed),
            'original_cases':len(before),'mutant_cases':len(after),
            'original_server_off':original['server_off_result'],'mutant_server_off':mutant['server_off_result'],
            'all_pass':bool(original['all_pass'] and mutant['all_pass'] and len(before)==8 and len(after)==8 and changed==expected_changed),
            'mutation_manifest':'mutation/mutation_manifest.json','mutation_manifest_sha256':sha(BASE/'mutation/mutation_manifest.json'),
            'preserved_attempts':runs,
            'scope':'Runtime ground truth for these typed requests and constructed heap states only; not a general Java/Hadoop soundness proof or a newly discovered production vulnerability.'}
    (BASE/'runtime_summary.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'all_pass':report['all_pass'],'original_cases':len(before),'mutant_cases':len(after),
                      'changed':sorted(changed),'preserved_attempts':len(runs)},indent=2),flush=True)
    if not report['all_pass']:raise SystemExit(1)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('original_run_record');p.add_argument('mutant_run_record');a=p.parse_args()
    summarize(a.original_run_record,a.mutant_run_record)

"""Read-only alias-envelope validation; never changes the repaired 450 baseline.

For each selected serialized program, enumerate the independent per-event
address choices declared by possible_regions. Each instantiated program is
executed by the independent full concrete state-DAG oracle. Its observations
are unioned, then compared with the ALREADY SAVED baseline abstract result.
This script neither constructs abstract facts from the oracle nor tunes the
static analysis to these outputs.
"""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from copy import deepcopy
import hashlib
import itertools
import json
from pathlib import Path
import sys
import time

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
sys.path.insert(0,str(ROOT))
from artifact.core.ir import Program,EventType
from artifact.oracle.exhaustive import ExhaustiveScheduler,ConcreteSet

BASELINE=ROOT/'experiments/legacy_repair_20261016'


def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def sha_object(value):return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()
def sha_file(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def tokens(values):return {canonical(v) for v in values}
def ordered(values):return sorted(values,key=canonical)


def choices_for(data):
    choices=[]
    for event in data['events']:
        attrs=event['attributes']
        if 'possible_regions' not in attrs:continue
        addresses=list(dict.fromkeys(attrs['possible_regions']))
        if event['type'] not in ('RemoteRead','RemoteWrite') or not addresses or attrs['region'] not in addresses:
            raise ValueError('Invalid possible_regions at '+event['id'])
        choices.append((event['id'],addresses))
    return choices


def execute_envelope(data,max_variants=256,allowed_assignments=None):
    """Return ALL per-address concreteizations and their exact observation union.

    Optional allowed_assignments is only used by explicit correlation examples;
    corpus audits use the independent product precisely as specified in metadata.
    """
    choices=choices_for(data)
    cardinality=1
    for _,values in choices:cardinality*=len(values)
    if cardinality>max_variants:raise ValueError(f'Address variant count {cardinality} exceeds explicit budget {max_variants}')
    variants=[];union_values={};union_outcomes={}
    constraints=canonical(data.get('initial_state',{}).get('ordering_constraints',[]))
    by_id={e['id']:e for e in data['events']}
    for addresses in itertools.product(*(v for _,v in choices)):
        assignment=dict(zip([e for e,_ in choices],addresses))
        if allowed_assignments is not None and not allowed_assignments(assignment):continue
        instantiated=deepcopy(data)
        for event in instantiated['events']:
            if event['id'] in assignment:event['attributes']['region']=assignment[event['id']]
        # Demonstrate that the only edit is the concrete address field.
        restored=deepcopy(instantiated)
        for event in restored['events']:
            event['attributes']['region']=by_id[event['id']]['attributes']['region'] if 'region' in event['attributes'] else event['attributes'].get('region')
            if 'region' not in by_id[event['id']]['attributes']:event['attributes'].pop('region',None)
        if restored!=data:raise AssertionError('Envelope changed fields other than concrete region')
        if canonical(instantiated.get('initial_state',{}).get('ordering_constraints',[]))!=constraints:raise AssertionError('Ordering constraints changed')
        result=ExhaustiveScheduler().explore(Program.from_dict(instantiated))
        if not result.complete or result.truncated or result.deadlock_count or result.schedule_count<1:
            raise AssertionError('Incomplete or deadlocked concreteization')
        for point,values in result.observations.items():union_values.setdefault(point,ConcreteSet()).update(values)
        for point,outcomes in result.observation_outcomes.items():union_outcomes.setdefault(point,ConcreteSet()).update(outcomes)
        variants.append({'region_assignment':assignment,'concrete_program_sha256':sha_object(instantiated),
                         'oracle':result.to_dict()})
    if not variants:raise ValueError('No permitted address configurations')
    return {'independent_address_variants':cardinality,'enumerated_variants':len(variants),'complete':True,
            'event_address_choices':[{'event_id':event,'possible_regions':values,'original_region':by_id[event]['attributes']['region']} for event,values in choices],
            'ordering_constraints_unchanged':True,'all_nonaddress_event_fields_unchanged':True,
            'variants':variants,'observation_union':{k:ordered(v) for k,v in union_values.items()},
            'outcome_union':{k:[{'value':v,'tainted':t} for v,t in sorted(vals,key=lambda x:canonical([x[0],x[1]]))] for k,vals in union_outcomes.items()},
            'summed_schedule_count':sum(v['oracle']['schedule_count'] for v in variants),
            'summed_state_count':sum(v['oracle']['state_count'] for v in variants)}


def compare(envelope,abstract):
    details={};sound=True;exact=True
    if set(envelope['observation_union'])!=set(abstract):return {'sound':False,'exact':False,'error':'Observation keys differ'}
    for point,values in envelope['observation_union'].items():
        a=abstract[point]
        may_taint=any(x['tainted'] for x in envelope['outcome_union'][point])
        finite=a['kind']=='set'
        avalues=a.get('constants',[]) if finite else []
        covered=a['kind']=='top' or tokens(values)<=tokens(avalues)
        same_values=finite and tokens(values)==tokens(avalues)
        covered_taint=not may_taint or a['tainted']
        same_taint=may_taint==a['tainted']
        details[point]={'envelope_values':values,'envelope_may_tainted':may_taint,'baseline_abstract':a,
                        'values_covered':covered,'taint_covered':covered_taint,
                        'same_values':same_values,'same_may_taint':same_taint,
                        'extra_static_values':[x for x in avalues if canonical(x) not in tokens(values)],
                        'missed_concrete_values':[x for x in values if finite and canonical(x) not in tokens(avalues)]}
        sound=sound and covered and covered_taint;exact=exact and same_values and same_taint
    return {'sound':sound,'exact':exact,'definition':'Equality of finite observation value sets and may-taint projection, not relational value/taint equality','points':details}


def audit_case(task):
    record_path,baseline_result,max_variants=task
    record=json.loads(Path(record_path).read_text(encoding='utf-8'))
    data=record['program'];before=sha_file(record_path)
    if sha_object(data)!=baseline_result['program_sha256']:raise AssertionError('Baseline program hash mismatch')
    start=time.perf_counter()
    envelope=execute_envelope(data,max_variants)
    comparison=compare(envelope,baseline_result['abstract'])
    if sha_file(record_path)!=before:raise AssertionError('Baseline record modified')
    return {'id':record['id'],'family':record['family'],'generator_metadata':data.get('metadata',{}).get('generator',{}),
            'record_path':str(record_path),'record_file_sha256':before,'baseline_program_sha256':baseline_result['program_sha256'],
            'baseline_abstract_state_sha256':baseline_result['abstract_state_sha256'],
            'baseline_concrete':baseline_result['concrete'],'baseline_exact_match':baseline_result['exact_match'],
            'envelope':envelope,'comparison':comparison,'elapsed_seconds':time.perf_counter()-start}


def correlation_examples():
    # Shared-pointer equality is deliberately external: current possible_regions
    # metadata expresses no relation between choices of distinct events.
    p=Program(['a','owner'])
    p.initial_state=p.initial_state.update_memory('R','x',0,owner='owner').update_memory('S','x',1,owner='owner')
    write=p.add_remote_write('a','R','x',7,possible_regions=['R','S'])
    read=p.add_remote_read('a','R','x',destination='read',possible_regions=['R','S'])
    p.add_observe('a','read')
    independent=execute_envelope(p.to_dict())
    correlated=execute_envelope(p.to_dict(),allowed_assignments=lambda a:a[write.id]==a[read.id])
    # Explicit guard remains unchanged, including when an inactive event gets
    # a different candidate address. Its value99 must never be manufactured.
    q=Program(['a','owner'])
    q.initial_state=q.initial_state.update_memory('R','x',0,owner='owner').update_memory('S','x',1,owner='owner')
    q.add_local_assign('a','selector',0)
    q.add_branch('a',{'op':'gt','args':[{'var':'selector'},0]},decision='positive')
    inactive=q.add_remote_write('a','R','x',99,guard={'decision':'positive','value':True},possible_regions=['R','S'])
    active=q.add_remote_write('a','S','x',7,guard={'decision':'positive','value':False},possible_regions=['R','S'])
    q.add_remote_read('a','R','x',destination='read');q.add_observe('a','read')
    guard_envelope=execute_envelope(q.to_dict())
    branch_correlated=execute_envelope(q.to_dict(),allowed_assignments=lambda a:a[active.id]=='S')
    if 99 in guard_envelope['observation_union']['a::read']:raise AssertionError('Guard was violated')
    return [{'name':'shared_pointer_requires_equal_event_addresses','program':p.to_dict(),
             'mandatory_order':[write.id,read.id],'independent_envelope':independent,'correlated_envelope':correlated,
             'conclusion':'Independent per-event choices add initial values0/1 even though every shared-pointer concretization writes and reads the same location and returns7. Program order is preserved; the missing information is the address relation.'},
            {'name':'branch_correlated_address_and_unchanged_guard','program':q.to_dict(),
             'inactive_write_event':inactive.id,'active_write_event':active.id,
             'independent_envelope':guard_envelope,'branch_correlated_envelope':branch_correlated,
             'inactive_branch_value99_absent':True,
             'conclusion':'Address expansion keeps the false branch inactive and preserves all mandatory order, yet without branch-correlated address information it permits active-write7 toR. If the source language fixes active targetS, the independent envelope is broader than its concrete semantics.'}]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--scope',choices=['representative','all'],default='representative')
    parser.add_argument('--workers',type=int,default=4);parser.add_argument('--max-variants',type=int,default=256)
    args=parser.parse_args();HERE.mkdir(parents=True,exist_ok=True)
    result_path=BASELINE/'rq1/rq1_results.json';index_path=BASELINE/'corpus/rq1/index.json'
    protected={str(path):sha_file(path) for path in [result_path,index_path]}
    results=json.loads(result_path.read_text(encoding='utf-8'))
    index=json.loads(index_path.read_text(encoding='utf-8'))
    records={x['id']:x for x in index['records']}
    imprecise=[r for r in results['results'] if r['status']=='passed' and not r['exact_match']]
    selected=imprecise
    if args.scope=='representative':
        selected=[]
        for family,sync in [('rma','nosync'),('rma','sync'),('mixed','nosync'),('mixed','sync')]:
            for r in imprecise:
                item=records[r['program']]
                if item['family']!=family:continue
                data=json.loads((BASELINE/item['path']).read_text())['program']
                if data['metadata']['generator']['synchronization']==sync:
                    selected.append(r);break
    tasks=[(str(BASELINE/records[r['program']]['path']),r,args.max_variants) for r in selected]
    start=time.perf_counter();cases=[]
    if args.workers==1:
        for task in tasks:cases.append(audit_case(task));print(cases[-1]['id'],cases[-1]['comparison']['exact'],flush=True)
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures={pool.submit(audit_case,task):task[1]['program'] for task in tasks}
            for future in as_completed(futures):
                cases.append(future.result())
                if len(cases)%20==0 or len(cases)==len(tasks):print('Completed',len(cases),'/',len(tasks),flush=True)
    cases.sort(key=lambda r:r['id'])
    report={'scope':args.scope,'baseline':'Unchanged repaired450 programs and saved static results',
            'meaning':'Address-envelope validation considers a FAMILY of programs; it does not replace the concrete semantics of the original fixed-address program.',
            'address_choice_model':'Independent per-event possible_regions choices; no shared-pointer or branch-address relational constraints are represented in corpus metadata.',
            'summary':{'baseline_programs':len(results['results']),'baseline_imprecise':len(imprecise),'audited_programs':len(cases),
                       'all_concreteizations_complete':all(c['envelope']['complete'] for c in cases),
                       'enumerated_concreteizations':sum(c['envelope']['enumerated_variants'] for c in cases),
                       'maximum_variants_per_program':max((c['envelope']['enumerated_variants'] for c in cases),default=0),
                       'envelope_sound':sum(c['comparison']['sound'] for c in cases),
                       'envelope_exact':sum(c['comparison']['exact'] for c in cases),
                       'remaining_gaps':sum(not c['comparison']['exact'] for c in cases),
                       'summed_legal_schedules':sum(c['envelope']['summed_schedule_count'] for c in cases),
                       'summed_reachable_states':sum(c['envelope']['summed_state_count'] for c in cases),
                       'elapsed_seconds':time.perf_counter()-start},
            'cases':cases,'correlation_examples':correlation_examples(),
            'baseline_file_sha256_before':protected,
            'baseline_file_sha256_after':{p:sha_file(p) for p in protected},
            'audit_source_sha256':sha_file(__file__),
            'oracle_source_sha256':sha_file(ROOT/'artifact/oracle/exhaustive.py'),
            'concrete_semantics_source_sha256':sha_file(ROOT/'artifact/core/semantics.py')}
    report['baseline_unchanged']=report['baseline_file_sha256_before']==report['baseline_file_sha256_after']
    filename='ALIAS_ORACLE_AUDIT.json' if args.scope=='all' else 'ALIAS_ORACLE_REPRESENTATIVE.json'
    (HERE/filename).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    summary=report['summary']
    lines=['Alias-envelope precision audit (2026-10-16)',
           'Scope: '+args.scope,
           f"Audited {summary['audited_programs']} of {summary['baseline_imprecise']} baseline imprecise programs; enumerated {summary['enumerated_concreteizations']} complete address concretizations.",
           f"Saved static result matches full alias-envelope value/may-taint observations in {summary['envelope_exact']}/{summary['audited_programs']}; remaining gaps {summary['remaining_gaps']}.",
           f"All address concretizations were exhaustively scheduled: {summary['summed_legal_schedules']} legal schedules and {summary['summed_reachable_states']} reachable states summed across concretizations.",
           'Only event region fields change. Program order, mandatory HB constraints, branch guards, payloads and all expressions remain identical.',
           'The baseline450 corpus and saved results remain unchanged; this audit does not revise their fixed-address precision counts.',
           'Independent address choices define a family envelope. Shared-pointer equality or branch-correlated address facts require relational constraints; the counterexamples demonstrate why an unconstrained envelope can include infeasible source-program addresses without violating the encoded guard/HB.',
           'Interpretation: disappearing gaps identify address information deliberately discarded by possible_regions; they do not establish arbitrary alias-analysis precision or justify using oracle outputs in the static algorithm.']
    if args.scope=='all':(HERE/'ALIAS_ORACLE_AUDIT.txt').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2));print('baseline_unchanged',report['baseline_unchanged'])


if __name__=='__main__':main()

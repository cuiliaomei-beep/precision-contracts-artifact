"""Independent M1 fixture integration, held-out heaps, and fail-closed checks.

Input construction never reads outcome/expected/pass fields. A separate checker
reads concrete outcomes only after source-derived execution. The extra finite
heap oracle is a specification control, not the implementation of the analysis.
"""
from pathlib import Path
import argparse
import copy
import hashlib
import itertools
import json
import sys

HERE = Path(__file__).resolve().parent
AUDIT = HERE / 'novelty_audit'
sys.path.insert(0, str(AUDIT))
import finite_jimple as frontend


def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def successful_runs():
    result = {}
    directory = HERE / 'hdfs_runtime'
    summary = json.loads((directory / 'runtime_summary.json').read_text(encoding='utf-8'))
    for key in ('original', 'mutant'):
        file = directory / summary[key + '_run_record']
        assert hashlib.sha256(file.read_bytes()).hexdigest() == summary[key + '_run_record_sha256']
        record = json.loads(file.read_text(encoding='utf-8'))
        assert record.get('all_pass') and len(record['client_result']['cases']) == 8
        result[record['mode']] = record
    return result


def request_arguments(case):
    return {'id': case['id'], 'path': case['path'], 'file_id': case['file_id'],
            'holder': case['holder'], 'last_block_length': 0}


def actual_input(record):
    # Only fixture identities and request arguments are used, never answers.
    client = record['client_result']
    source_facts = json.loads((HERE / 'hdfs_runtime' / 'HEAP_INPUT_FACTS.json').read_text(encoding='utf-8'))
    facts = next(r for r in source_facts['runs'] if Path(r['run']).name == Path(record['folder']).name)
    assert hashlib.sha256(Path(facts['construction_source']).read_bytes()).hexdigest() == facts['construction_source_sha256']
    facts_by_id = {r['case_id']: r for r in facts['cases']}
    assert set(facts_by_id) == {r['id'] for r in client['cases']}
    heap = frontend.make_heap(client['fixture'])
    heap['root']['children'].pop('audit-parent', None)
    del heap['parent']
    root_id = next(item['id'] for fact in facts['cases'] for item in fact['inode_map'] if item['path'] == '/')
    heap['root'].update(id=root_id, path='/', name='', features={}, snapshot=False)
    heap['inodeMap']['table'][str(root_id)] = frontend.ref('root')
    requests = []
    for case in client['cases']:
        request = request_arguments(case)
        request_heap = copy.deepcopy(heap)
        fact = facts_by_id[case['id']]
        assert fact['request'] == {'src': request['path'], 'file_id': request['file_id'],
                                   'holder': request['holder'], 'last_block_length': 0}
        for item in fact['inode_map']:
            if item['path'] == '/':
                assert item['id'] == root_id and not item['is_file'] and not item['under_construction']
                continue
            identifier = 'inode_a' if item['id'] == client['fixture']['file_id_a'] else 'inode_b'
            obj = request_heap[identifier]
            assert obj['id'] == item['id'] and obj['path'] == item['path'] and item['is_file'] and not item['deleted']
            if not item['under_construction']:
                obj['features'] = {}
            else:
                feature = obj['features'][frontend.NN + 'FileUnderConstructionFeature']
                field = frontend.fq('FileUnderConstructionFeature', 'java.lang.String', 'clientName')
                assert request_heap[feature['ref']][field] == item['lease_owner']
        if fact['phase'] != 'both_output_streams_open':
            assert not request_heap['inode_a']['features'] and not request_heap['inode_b']['features']
        requests.append({'id': 'runtime_' + request['id'], 'heap': request_heap, 'requests': [request]})
    return {'provenance': 'Heap fixture contracts from executed fixture source; runtime identity/request arguments only; no outcome labels',
            'families': requests}


def held_out_input(fixture):
    families = []
    variants = ('live', 'closed_a', 'directory_a', 'detached_a', 'replaced_child_a',
                'missing_ancestor', 'extra_parent')
    for owners in ((fixture['owner_a'], fixture['owner_b']),
                   (fixture['owner_b'], fixture['owner_a'])):
        for variant in variants:
            heap = frontend.make_heap(fixture, owners=list(owners))
            inode = heap['inode_a']
            if variant == 'closed_a':
                inode['features'] = {}
            elif variant == 'directory_a':
                inode['type'] = frontend.NN + 'INodeDirectory'
                inode['features'] = {}
                inode['children'] = {}
            elif variant == 'detached_a':
                inode['parent'] = None
            elif variant == 'replaced_child_a':
                heap[inode['parent']['ref']]['children'][inode['name']] = frontend.ref('inode_b')
            elif variant == 'missing_ancestor':
                for suffix in ('a', 'b'):
                    child = heap['inode_' + suffix]
                    child['parent'] = frontend.ref('parent')
                    child['path'] = '/audit-parent/' + child['name']
                    heap['parent']['children'][child['name']] = frontend.ref('inode_' + suffix)
                heap['root']['children'].pop('audit-parent', None)
            elif variant == 'extra_parent':
                heap['extra_parent'] = {'type': frontend.NN + 'INodeDirectory', 'id': 3,
                    'name': 'extra', 'is_root': False, 'parent': frontend.ref('root'),
                    'children': {'audit-parent': frontend.ref('parent')}}
                heap['root']['children'] = {'extra': frontend.ref('extra_parent')}
                heap['parent']['parent'] = frontend.ref('extra_parent')
                for suffix in ('a', 'b'):
                    child = heap['inode_' + suffix]
                    child['parent'] = frontend.ref('parent')
                    child['path'] = '/extra/audit-parent/' + child['name']
                    heap['parent']['children'][child['name']] = frontend.ref('inode_' + suffix)
            requests = []
            for index, (fileid, holder, path) in enumerate(itertools.product(
                    (fixture['file_id_a'], fixture['file_id_b'], 9223372036854775790),
                    (fixture['owner_a'], fixture['owner_b'], 'unrelated-holder'),
                    (fixture['file_a'], fixture['file_b']))):
                requests.append({'id': f'ctx_{index}', 'path': path, 'file_id': fileid,
                                 'holder': holder, 'last_block_length': 0})
            families.append({'id': f'{len(families)}_{variant}', 'heap': heap, 'requests': requests})
    return {'provenance': 'Predeclared finite heap variants crossed with all listed request choices; no runtime answers used',
            'families': families}


def specification(heap, request, mutant):
    """Small independent fixture specification, not source interpreter code."""
    lease = 'org.apache.hadoop.hdfs.server.namenode.LeaseExpiredException'
    missing = 'java.io.FileNotFoundException'
    node_ref = heap['inodeMap']['table'].get(str(request['file_id']))
    if node_ref is None:
        return missing
    node = heap[node_ref['ref']]
    if node['type'] != frontend.NN + 'INodeFile':
        return lease
    feature = node['features'].get(frontend.NN + 'FileUnderConstructionFeature')
    if feature is None:
        return lease
    child = node
    ancestor_ref = node['parent']
    seen = set()
    while ancestor_ref is not None:
        key = ancestor_ref['ref']
        assert key not in seen, 'Specification excludes cyclic directory ancestry'
        seen.add(key)
        ancestor = heap[key]
        entry = ancestor['children'].get(child['name'])
        if entry is None or heap[entry['ref']]['id'] != child['id']:
            return missing
        if ancestor['is_root']:
            break
        child, ancestor_ref = ancestor, ancestor['parent']
    else:
        return missing
    owner = heap[feature['ref']][frontend.fq('FileUnderConstructionFeature', 'java.lang.String', 'clientName')]
    return None if mutant or owner == request['holder'] else lease


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--original-only', action='store_true')
    args = parser.parse_args()
    runs = successful_runs()
    modes = ['original'] if args.original_only else ['original', 'holder_check_removed']
    reports = []
    for mode in modes:
        assert mode in runs
        ir_path = AUDIT / ('extracted_original.json' if mode == 'original' else 'extracted_mutant.json')
        ir = json.loads(ir_path.read_text(encoding='utf-8'))
        actual = actual_input(runs[mode])
        write(HERE / f'M1_RUNTIME_INPUT_{mode}.json', actual)
        predictions = frontend.predict(ir, actual)
        assert {(r['family'], r['request_ctx']) for r in predictions['results']} == {(f['id'], r['id']) for f in actual['families'] for r in f['requests']}
        assert len(predictions['results']) == len(actual['families']) == 8
        assert predictions['unknown_count'] == 0, predictions['results']
        cases = {c['id']: c for c in runs[mode]['client_result']['cases']}
        for row in predictions['results']:
            actual_outcome = cases[row['request_ctx']]['outcome']
            predicted = row['exception_class'] or 'normal'
            assert predicted == actual_outcome, (mode, row, actual_outcome)
            assert row['reached_persistBlocks'] == (predicted == 'normal')
        write(HERE / f'M1_RUNTIME_PREDICTIONS_{mode}.json', predictions)
        held = held_out_input(runs[mode]['client_result']['fixture'])
        write(HERE / f'M1_HELD_OUT_INPUT_{mode}.json', held)
        predicted_held = frontend.predict(ir, held)
        expected_keys = {(f['id'], r['id']) for f in held['families'] for r in f['requests']}
        assert {(r['family'], r['request_ctx']) for r in predicted_held['results']} == expected_keys
        assert len(predicted_held['results']) == len(expected_keys)
        assert predicted_held['unknown_count'] == 0, [r for r in predicted_held['results'] if r['unsupported']]
        expected = {(f['id'], r['id']): specification(f['heap'], r, mode != 'original')
                    for f in held['families'] for r in f['requests']}
        for row in predicted_held['results']:
            assert row['exception_class'] == expected[row['family'], row['request_ctx']], (mode, row)
            assert row['reached_persistBlocks'] == (row['exception_class'] is None)
        backedges = sum(t['backedge'] for r in predicted_held['results'] for t in r['trace'])
        assert backedges > 0, 'The extra-parent family must actually exercise extracted CFG backedges'
        write(HERE / f'M1_HELD_OUT_PREDICTIONS_{mode}.json', predicted_held)
        negatives = []
        for kind in ('null_holder', 'positive_last_block', 'snapshot', 'missing_field', 'uncontracted_call'):
            data = copy.deepcopy(actual)
            data['families'] = data['families'][:1]
            request = data['families'][0]['requests'][0]
            bad_ir = copy.deepcopy(ir)
            if kind == 'null_holder':
                request['holder'] = None
            elif kind == 'positive_last_block':
                request['last_block_length'] = 1
            elif kind == 'snapshot':
                data['families'][0]['heap']['inode_a']['snapshot'] = True
            elif kind == 'missing_field':
                del data['families'][0]['heap']['uc_a'][frontend.fq('FileUnderConstructionFeature', 'java.lang.String', 'clientName')]
            else:
                method = next(m for m in bad_ir['methods'] if m['signature'] == frontend.ENTRY)
                invocation = next(u for u in method['units'] if u['kind'] == 'invoke')
                invocation['value']['signature'] = '<untrusted.Library: void checkNNStartup()>'
            negative = frontend.predict(bad_ir, data)
            assert negative['unknown_count'] == 1, (kind, negative)
            negatives.append({'kind': kind, 'status': 'rejected_as_unknown',
                              'reason': negative['results'][0]['unsupported']})
        reports.append({'mode': mode, 'runtime_rows_matched': len(predictions['results']),
                        'held_out_families': len(held['families']),
                        'held_out_rows_checked': len(predicted_held['results']),
                        'actual_cfg_backedges_traversed': backedges,
                        'ir_sha256': hashlib.sha256(ir_path.read_bytes()).hexdigest(),
                        'negative_controls': negatives})
    result = {'status': 'passed', 'modes': reports,
              'scope': 'Restricted finite heap/library/protocol contract, actual extracted CFG; not whole-HDFS analysis',
              'm1_status': 'restricted_source_derived_reference_completed; general frontend obligations remain'}
    write(HERE / 'M1_INDEPENDENT_VERIFICATION.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()

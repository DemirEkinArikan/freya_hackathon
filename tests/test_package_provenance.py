"""Fabricated package consistency regressions; never evidence of live acceptance."""
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from builder.safety import digest
from builder.source_provenance import bind_run_provenance
from tools import package_submission as package
import test_package_submission as fixtures


class PackageProvenanceTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.PackageTests()
        self.helper.setUp()
        self.addCleanup(self.helper.tearDown)
        self.root = self.helper.root
        self.manifest = self.helper.evidence()
        self.git = patch.object(package.subprocess, 'run', return_value=SimpleNamespace(stdout='a' * 40))
        self.git.start()
        self.addCleanup(self.git.stop)

    def records(self):
        directory = self.root / 'hidden-1'
        return (json.loads((directory / 'builder-source.json').read_text()),
                json.loads((directory / 'manifest.json').read_text()),
                [json.loads(line) for line in (self.root / 'hidden-1.jsonl').read_text().splitlines()])

    def rewrite(self, source, run, events, *, refresh=True):
        directory = self.root / 'hidden-1'
        if refresh:
            source['builder_source_sha256'] = digest({key: source[key] for key in ('version', 'files', 'resolved_config')})
            run.update(builder_source_sha256=source['builder_source_sha256'], builder_source_record_sha256=digest(source))
            for event in events:
                if event['event'] == 'builder_source_snapshot':
                    event.update(builder_source_sha256=source['builder_source_sha256'],
                                 builder_source_record_sha256=digest(source), resolved_config=source['resolved_config'])
        (directory / 'builder-source.json').write_text(json.dumps(source))
        (directory / 'manifest.json').write_text(json.dumps(run))
        (self.root / 'hidden-1.jsonl').write_text('\n'.join(json.dumps(event) for event in events))
        if refresh:
            (directory / 'build-provenance.json').write_text(json.dumps(bind_run_provenance(source, run, events)))

    def test_original_provenance_and_isolation_trace_are_retained(self):
        retained = {name for _, name in package.check_manifest(self.manifest)}
        for name in ('hidden-1', 'hidden-2'):
            for filename in ('builder-source.json', 'build-provenance.json', 'policy.json', 'policy.contract.json',
                             'attempts/plan-001/plan.json', 'attempts/plan-001/proposal.json',
                             'attempts/plan-001/action-bindings.json', 'attempts/plan-001/execution-policy.json',
                             'attempts/plan-001/guard-context.json', 'attempts/plan-001/guard-manifest.json',
                             'attempts/plan-001/workflow.json'):
                self.assertIn(f'runs/{name}/{filename}', retained)
            self.assertIn(f'traces/{name}.jsonl', retained)

    def test_selected_generated_artifacts_cannot_drift_from_recorded_fingerprints(self):
        for filename in ('policy.json', 'policy.contract.json', 'attempts/plan-001/plan.json',
                         'attempts/plan-001/guard-manifest.json', 'attempts/plan-001/workflow.json',
                         'attempts/plan-001/proposal.json', 'attempts/plan-001/action-bindings.json',
                         'attempts/plan-001/execution-policy.json'):
            self.manifest = self.helper.evidence()
            path = self.root / 'hidden-1' / filename
            record = json.loads(path.read_text())
            record['changed'] = 'Synthetic artifact drift'
            path.write_text(json.dumps(record))
            # A proposal's opaque extra field is immaterial to plan binding;
            # mutate the actual proposal plan instead.
            if filename.endswith('proposal.json'):
                record['plan'] = {'changed': True}
                path.write_text(json.dumps(record))
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'artifacts|fingerprints|workflow'):
                package.check_manifest(self.manifest)

    def test_missing_original_source_or_binding_fails_closed(self):
        for filename in ('builder-source.json', 'build-provenance.json'):
            path = self.root / 'hidden-1' / filename
            contents = path.read_bytes()
            path.unlink()
            with self.subTest(filename=filename), self.assertRaisesRegex(ValueError, 'does not exist'):
                package.check_manifest(self.manifest)
            path.write_bytes(contents)

    def test_runtime_inventory_must_match_each_invocation(self):
        source, run, events = self.records()
        (self.root / 'builder/build.py').write_text('# Later modified code\n')
        with self.assertRaisesRegex(ValueError, 'runtime source inventory'):
            package.check_manifest(self.manifest)
        (self.root / 'builder/build.py').write_text('# Synthetic runtime fixture\n')
        source['files']['builder/new.py'] = '0' * 64
        self.rewrite(source, run, events)
        with self.assertRaisesRegex(ValueError, 'runtime source inventory'):
            package.check_manifest(self.manifest)

    def test_bound_original_records_reject_later_trace_or_manifest_edits(self):
        for which in ('source', 'run', 'trace', 'binding', 'boolean_version'):
            self.manifest = self.helper.evidence()
            source, run, events = self.records()
            if which == 'source': source['limitations'] = 'later rewrite'
            if which == 'run': run['model'] = 'changed-model'
            if which == 'trace': events[6]['purpose'] = 'later rewrite'
            self.rewrite(source, run, events, refresh=False)
            if which == 'binding':
                path = self.root / 'hidden-1/build-provenance.json'
                record = json.loads(path.read_text()); record['source_snapshot']['captured_at'] = 'changed'
                path.write_text(json.dumps(record))
            if which == 'boolean_version':
                path = self.root / 'hidden-1/build-provenance.json'
                record = json.loads(path.read_text()); record['version'] = True
                path.write_text(json.dumps(record))
            with self.subTest(which=which), self.assertRaises(ValueError):
                package.check_manifest(self.manifest)

    def test_ineligible_resolved_config_cannot_be_rebound_into_final_package(self):
        for key, value in [('mode', 'generate-only'), ('controller_dependency_injection', True),
                           ('controller_dependency_injection', 0), ('python_version', '3.12.0'),
                           ('deadline_seconds', 1801), ('export_reserve_seconds', 299),
                           ('max_repairs', True), ('codex_executable', ''), ('reasoning_effort', None)]:
            self.manifest = self.helper.evidence()
            source, run, events = self.records()
            source['resolved_config'][key] = value
            self.rewrite(source, run, events)
            with self.subTest(key=key, value=value), self.assertRaisesRegex(ValueError, 'resolved configuration'):
                package.check_manifest(self.manifest)

    def test_source_event_and_audit_must_precede_generation(self):
        for kind in ('source', 'audit', 'missing_audit'):
            self.manifest = self.helper.evidence()
            source, run, events = self.records()
            if kind == 'source': events[2], events[3] = events[3], events[2]
            if kind == 'audit': events[4], events[6] = events[6], events[4]; events[6]['time'] = events[4]['time']
            if kind == 'missing_audit': events.pop(4)
            self.rewrite(source, run, events)
            with self.subTest(kind=kind), self.assertRaises(ValueError):
                package.check_manifest(self.manifest)

    def test_audit_pass_label_alone_does_not_suffice(self):
        for key, value in [('status', 'fail'), ('failures', ['tool_event']), ('response_valid', False),
                           ('model_reported_tool_count', 1), ('model_reported_tools', ['exec_command']),
                           ('omitted_skill_count', 1), ('completed_turn_count', True),
                           ('canary_exposure', ['forbidden_file']), ('observed_item_types', ['tool_call'])]:
            self.manifest = self.helper.evidence()
            source, run, events = self.records()
            events[4][key] = value
            self.rewrite(source, run, events)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'tool-free evidence'):
                package.check_manifest(self.manifest)

    def test_all_generation_calls_use_invocation_model_and_effort(self):
        for key, value in [('model', 'changed-model'), ('reasoning_effort', 'low')]:
            self.manifest = self.helper.evidence()
            source, run, events = self.records()
            events[6][key] = value
            self.rewrite(source, run, events)
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, 'model calls differ'):
                package.check_manifest(self.manifest)

    def test_export_phone_model_is_always_the_fixed_event_model(self):
        source, run, events = self.records()
        path = self.root / 'hidden-1.json'
        workflow = json.loads(path.read_text())
        workflow['workflow']['agent_config']['llmConfig']['model'] = 'other-phone-model'
        path.write_text(json.dumps(workflow))
        run['export_sha256'] = digest(workflow)
        events[-1]['export_sha256'] = digest(workflow)
        self.rewrite(source, run, events)
        with self.assertRaisesRegex(ValueError, 'experimental-5 phone model'):
            package.check_manifest(self.manifest)

    def test_original_capture_time_cannot_be_naive_after_generation_or_before_start(self):
        for timestamp in ('2026-10-10T12:00:00', '2026-10-10T12:00:01Z', '2026-10-10T11:59:59Z'):
            self.manifest = self.helper.evidence()
            source, run, events = self.records()
            source['captured_at'] = timestamp
            self.rewrite(source, run, events)
            with self.subTest(timestamp=timestamp), self.assertRaisesRegex(ValueError, 'capture timestamp'):
                package.check_manifest(self.manifest)

    def test_structured_test_data_and_jsonl_cannot_evade_credential_scanning(self):
        path = self.root / 'tests/synthetic.json'
        path.parent.mkdir()
        for text in ('{"password":"private-opaque-value"}', '{"password":"private-value","password":""}', '{"number":NaN}'):
            path.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'requires review'):
                package.check_content(path)
        path = path.with_suffix('.jsonl')
        for text in ('{"password":"private-value","password":""}', '{"number":Infinity}'):
            path.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, 'requires review'):
                package.check_content(path, evidence=True)


if __name__ == '__main__':
    unittest.main()

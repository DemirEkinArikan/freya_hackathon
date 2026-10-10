"""Local CLI tool-registration audit. No real model, credentials or upstream forwarding."""
import argparse
import datetime as dt
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import secrets
import shlex
import sys
import tempfile
import threading
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))
from builder.runtime import CodexGenerator, Trace, Deadline, generation_environment, run_bounded
from builder.preflight import PROBE_SCHEMA


def names(tools, prefix=''):
    found=[]
    for tool in tools or []:
        name=tool.get('name', tool.get('type','unknown'))
        if tool.get('type')=='namespace':
            found.extend(names(tool.get('tools',[]),prefix+name+'.'))
        else: found.append(prefix+name)
    return found


def inspect(mode, catalog_mode):
    observed=[]; outputs=[]; injected=[]
    scratch = PROJECT / 'artifacts' / 'runtime-wire-probes'
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wire-', dir=scratch) as tmp:
        root=Path(tmp).resolve(); home=root/'codex-home';home.mkdir();directory=root/'generation';directory.mkdir()
        sentinel=root/'forbidden.txt';token='WIRE_CANARY_'+secrets.token_hex(20);sentinel.write_text(token)
        forbidden_write=directory/'should-not-exist.txt'
        env=generation_environment();env['CODEX_HOME']=str(home)
        def local_run(argv, **kwargs):
            kwargs['env'] = env
            return run_bounded(argv, **kwargs)
        gen=CodexGenerator(root,Trace(root/'ignored-trace.jsonl'),Deadline(),run=local_run)
        catalog_evidence=gen.prepare_catalog(directory)
        if catalog_mode=='baseline':
            result=run_bounded(['codex','debug','models','--bundled'],text=True,capture_output=True,check=True,timeout=20,env=env)
            (directory/'generation.model-catalog.json').write_text(result.stdout)
        schema=directory/'response.schema.json';schema.write_text(json.dumps(PROBE_SCHEMA))
        tool_call=None
        if mode=='forced_patch':
            tool_call={'id':'ctc_audit','call_id':'call_audit','type':'custom_tool_call','name':'apply_patch',
                       'input':'*** Begin Patch\n*** Add File: '+str(forbidden_write)+'\n+synthetic\n*** End Patch'}
        if mode=='forced_shell':
            tool_call={'id':'fc_audit','call_id':'call_audit','type':'function_call','name':'exec_command',
                       'arguments':json.dumps({'cmd':'cat '+shlex.quote(str(sentinel))})}
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                additional=[i for i in body.get('input',[]) if isinstance(i,dict) and i.get('type')=='additional_tools']
                tool_defs=list(body.get('tools') or [])
                for i in additional:
                    tool_defs.extend(i.get('tools') or [])
                actual_names=names(tool_defs)
                observed.append({'path':self.path,'model':body.get('model'),'tools_field_present':'tools' in body,'additional_tools_items':len(additional),
                                 'request_tool_names':actual_names,'request_tool_count':len(actual_names)})
                for item in body.get('input',[]):
                    if isinstance(item,dict) and item.get('type') in {'function_call_output','custom_tool_call_output'}:
                        output=item.get('output')
                        output_text=json.dumps(output)
                        outputs.append({'call_id':item.get('call_id'),'type':item['type'],
                                        'rejected_as_unsupported':isinstance(output,str) and 'unsupported' in output.lower(),
                                        'sentinel_exposed':token in output_text,
                                        'diagnostic':output if isinstance(output,str) and 'unsupported' in output.lower() and len(output)<250 else '[not retained]'})
                if tool_call is not None and not injected:
                    injected.append(tool_call['name']); output_items=[tool_call]
                    events=[{'type':'response.output_item.added','output_index':0,'item':tool_call},
                            {'type':'response.output_item.done','output_index':0,'item':tool_call}]
                else:
                    value=json.dumps({'available_tools':[],'loaded_markers':[],'forbidden_content':'UNAVAILABLE','environment_content':'UNAVAILABLE'})
                    message={'id':'msg_synthetic','type':'message','role':'assistant','status':'completed',
                             'content':[{'type':'output_text','text':value,'annotations':[]}]}
                    output_items=[message]
                    events=[{'type':'response.output_item.added','output_index':0,'item':{**message,'status':'in_progress','content':[]}},
                            {'type':'response.output_text.delta','item_id':'msg_synthetic','output_index':0,'content_index':0,'delta':value},
                            {'type':'response.output_item.done','output_index':0,'item':message}]
                response={'id':'resp_'+str(len(observed)),'object':'response','status':'completed','model':'gpt-6.1-sol',
                          'output':output_items,'usage':{'input_tokens':1,'output_tokens':1,'total_tokens':2}}
                events.insert(0,{'type':'response.created','response':{'id':response['id'],'status':'in_progress','output':[]}})
                events.append({'type':'response.completed','response':response})
                encoded=''.join('event: '+e['type']+'\ndata: '+json.dumps(e)+'\n\n' for e in events).encode()
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
        provider='model_providers.t02_local={name="T02 local inspection",base_url="http://127.0.0.1:'+str(server.server_port)+'/v1",wire_api="responses",requires_openai_auth=false,request_max_retries=0,stream_max_retries=0}'
        argv=gen.argv(directory,schema)+['-c','model_provider="t02_local"','-c',provider]
        try:
            result=run_bounded(argv,input='Synthetic transport inspection. Return the required JSON.',text=True,capture_output=True,timeout=40,cwd=directory,env=env)
        finally:server.shutdown();server.server_close()
        summary={'case':mode,'catalog_mode':catalog_mode,'returncode':result.returncode,'requests':observed,
                 'injected_tool_calls':injected,'tool_outputs':outputs,'sentinel_exposed':token in (result.stdout+result.stderr),
                 'patch_file_created':forbidden_write.exists()}
        if catalog_mode=='restricted':summary['generation_catalog_sha256']=catalog_evidence['generation_catalog_sha256']
        return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=PROJECT / 'artifacts' / 't02-tool-registry.json')
    args = parser.parse_args()
    cursor = args.output.absolute()
    while cursor != cursor.parent:
        if cursor.is_symlink():
            parser.error('Output must not traverse a symlink')
        cursor = cursor.parent
    output = args.output.resolve()
    if not output.is_relative_to(PROJECT / 'artifacts'):
        parser.error('Output must stay within repository artifacts')
    version = run_bounded(['codex', '--version'], capture_output=True, text=True, timeout=20,
                          env=generation_environment(), check=True).stdout.strip()
    reports = [inspect('baseline', 'baseline'), inspect('restricted', 'restricted'),
               inspect('forced_patch', 'restricted'), inspect('forced_shell', 'restricted')]
    failures = []
    if (reports[0]['returncode'] != 0 or not reports[0]['requests']
            or reports[0]['requests'][0]['request_tool_count'] == 0):
        failures.append('positive_control_did_not_advertise_tools')
    for result in reports[1:]:
        if result['returncode'] != 0 or not result['requests']:
            failures.append(result['case'] + ':incomplete_invocation')
        if any(request['request_tool_count'] != 0 for request in result['requests']):
            failures.append(result['case'] + ':registered_tools')
        if result['sentinel_exposed'] or result['patch_file_created']:
            failures.append(result['case'] + ':tool_side_effect')
    for result in reports[2:]:
        if (not result['tool_outputs'] or not all(item['rejected_as_unsupported'] and not item['sentinel_exposed']
                                                 for item in result['tool_outputs'])):
            failures.append(result['case'] + ':rejection_not_observed')
    report = {
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'status': 'fail' if failures else 'pass', 'failures': failures,
        'codex_version': version, 'provider': 'loopback_stub_no_upstream',
        'codex_home': 'empty_temporary_directory', 'cases': reports,
        'limits': [
            'Uses production model catalog and generation argv; provider and CODEX_HOME differ from the live ChatGPT invocation.',
            'Inspects actual outgoing tool definitions and forced apply_patch/exec_command calls; no real model response is used.',
            'Does not independently prove OS path denial or exhaustively audit all possible tool names.'
        ]}
    from builder.safety import assert_no_secrets, sanitize
    report = sanitize(report)
    assert_no_secrets(report)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())

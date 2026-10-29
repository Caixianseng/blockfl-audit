"""Read-only Streamlit AppTest and live commitment checks for the new cohort."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

from streamlit.testing.v1 import AppTest
from dashboard.data import list_runs,query_commitment,load_run,network_snapshot
from fl.artifacts import load_parameters,hash_parameters


def element(elements,label):
    return next(item for item in elements if item.label==label)


def assert_clean(app,page):
    if app.exception:
        raise AssertionError(f'{page} raised: {[x.value for x in app.exception]}')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prefix',default='audit-optimization-20261029-final')
    args=parser.parse_args()
    name=f'{args.prefix}-pipelined-s44'
    path=ROOT/'results'/name
    run=load_run(path)
    if run['status'].get('state')!='completed':raise ValueError('The final pipelined run must be complete')
    app=AppTest.from_file(str(ROOT/'dashboard/app.py'),default_timeout=30)
    app.session_state['selected_run']=name
    app.run();assert_clean(app,'training_page')
    strategy=element(app.selectbox,'审计提交方式')
    strategy.set_value('pipelined').run();assert_clean(app,'strategy_selection')
    if element(app.selectbox,'审计提交方式').value!='pipelined':raise AssertionError('Strategy selector did not retain pipelined')
    checks={'training_page':True,'pipelined_strategy_selection':True}
    for page,key in [('运行记录','history_page'),('链上审计与校验','audit_page'),('对照实验','comparison_page')]:
        app.sidebar.radio[0].set_value(page).run();assert_clean(app,key);checks[key]=True
    if element(app.selectbox,'实验批次').value!=args.prefix:raise AssertionError('The latest completed optimization study was not selected by default')
    checks['latest_completed_study_default']=True
    element(app.selectbox,'实验批次').set_value(args.prefix).run();assert_clean(app,'study_filter')
    if element(app.selectbox,'实验批次').value!=args.prefix:raise AssertionError('Wrong optimization cohort selected')
    # The table only includes the selected six-run study. Dataframes are actual
    # UI values, rather than a separate synthetic comparison fixture.
    frame=app.dataframe[0].value
    if len(frame)!=6 or not all(str(value).startswith(args.prefix+'-') for value in frame['run']):
        raise AssertionError('Comparison page mixed historical or unrelated runs')
    if set(frame['condition'])!={'blockchain_sync','blockchain_pipelined'}:raise AssertionError('Audit strategies not separated')
    checks['explicit_six_run_study_filter']=True
    checks['sync_and_pipelined_separate_conditions']=True
    app.sidebar.radio[0].set_value('链上审计与校验').run();assert_clean(app,'audit_page_return')
    # Select the actual complete tenth-round aggregate; no training or chain
    # mutation button is pressed in this script.
    selected=element(app.selectbox,'选择运行记录')
    runs=list_runs()
    index=next(i for i,item in enumerate(runs) if item['name']==name)
    selected.set_value(index).run();assert_clean(app,'audit_run_selection')
    record_index=next(i for i,record in enumerate(run['manifest']) if record['kind']=='global_model' and record['round']==10)
    element(app.selectbox,'模型记录').set_value(record_index).run();assert_clean(app,'model_selection')
    element(app.checkbox,'同时从当前网络读取该轮次的链上承诺').set_value(True).run()
    element(app.button,'计算并比对哈希').click().run();assert_clean(app,'live_hash_button')
    if not any('哈希一致' in message.value for message in app.success):raise AssertionError('Live UI hash comparison did not succeed')
    if not any('实时 eth_call' in message.value for message in app.caption):raise AssertionError('Live query provenance was not displayed')
    checks['live_model_hash_button']=True
    record=run['manifest'][record_index]
    proof=query_commitment(run,record)
    actual=hash_parameters(load_parameters(path/record['path']))
    if actual!=proof['hash']:raise AssertionError('Independent live hash check failed')
    result={'at':datetime.now(timezone.utc).isoformat(),'source_run':name,'artifact':record['path'],
            'actual_model_hash':actual,'chain_readback':proof,'network':network_snapshot(),'checks':checks,
            'scope':'Read-only AppTest with genuine completed optimization runs and live eth_call; no training launch or signed transaction.',
            'app_source_sha256':hashlib.sha256((ROOT/'dashboard/app.py').read_bytes()).hexdigest(),
            'data_source_sha256':hashlib.sha256((ROOT/'dashboard/data.py').read_bytes()).hexdigest()}
    output=ROOT/'report/optimization_ui_validation.json'
    output.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=='__main__':main()

"""Launch with: python -m streamlit run dashboard/app.py"""
from __future__ import annotations

import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd
import streamlit as st
from dashboard.data import RESULTS, comparison_key, is_live, launch_training, list_runs, load_run, network_snapshot, query_commitment, run_scope, tail_log, valid_pair, within_results
from common.job_lock import job_status

st.set_page_config(page_title="BlockFL · 联邦训练审计", page_icon="◈", layout="wide", initial_sidebar_state="expanded")
st.markdown("""<style>
.stApp {background:#f5f7fa;color:#162e3d;}
[data-testid="stSidebar"] {background:#102b3a;}
[data-testid="stSidebar"] * {color:#e5f0f5;}
[data-testid="stSidebar"] hr {border-color:#294857;}
.block-container {max-width:1440px;padding-top:2rem;padding-bottom:3rem;}
h1,h2,h3 {letter-spacing:-.035em;}
[data-testid="stMetric"] {background:white;padding:1rem 1.1rem;border:1px solid #e1e9ee;border-radius:12px;}
[data-testid="stMetricLabel"] {color:#597280;}
[data-testid="stMetricValue"] {font-weight:650;}
.hero {padding:1.7rem 2rem;background:linear-gradient(110deg,#103547,#145e64);border-radius:16px;color:white;margin-bottom:1.4rem;}
.hero h1 {font-size:2rem;color:white;margin:0;padding:0;}
.hero p {margin:.55rem 0 0;color:#c8e5e8;line-height:1.6;}
.eyebrow {font-size:.75rem;letter-spacing:.14em;color:#8ad8d0;margin-bottom:.5rem;}
.subtle {color:#607d8b;font-size:.88rem;}
.stButton button[kind="primary"], .stFormSubmitButton button[kind="primary"] {background:#087f81;border-color:#087f81;}
[data-testid="stDataFrame"] {border-radius:10px;}
</style>""", unsafe_allow_html=True)

MODE_NAMES = {"baseline": "普通 FedAvg", "blockchain": "联盟链审计", "unknown": "模式未记录"}
STATE_NAMES = {"preparing": "准备中", "running": "训练中", "completed": "已完成", "failed": "失败"}
STRATEGY_NAMES = {"sync":"逐笔确认", "pipelined":"并发提交后集中确认"}
CONDITION_NAMES = {"baseline":"普通 FedAvg", "blockchain_sync":"联盟链 · 逐笔确认", "blockchain_pipelined":"联盟链 · 并发提交"}
CLIENT_STATE_NAMES = {'waiting':'等待','training':'本地训练','committing':'发送审计交易','verifying':'校验模型',
                      'pending_confirmation':'等待交易回执','confirmed':'交易已确认','verified':'哈希核验通过'}

def audit_condition(run):
    if run['mode']!='blockchain':return run['mode']
    strategy=run['summary'].get('audit_strategy',run['config'].get('audit_strategy','sync'))
    return 'blockchain_'+strategy


@st.cache_data(ttl=5, show_spinner=False)
def cached_network_snapshot():
    return network_snapshot()


def show_network():
    nodes = cached_network_snapshot()
    count = sum(node["responsive"] for node in nodes)
    st.caption(f"本地验证节点 RPC：{count} / 4 可响应 · 这是节点快照，交易确认需另行验证")
    with st.expander("查看节点区块高度"):
        st.dataframe(pd.DataFrame(nodes), use_container_width=True, hide_index=True)


def number(value, unit="", decimals=2):
    try:
        return f"{float(value):,.{decimals}f}{unit}"
    except (TypeError, ValueError):
        return "—"


def accuracy(value):
    try:
        return f"{float(value) * 100:.2f}%"
    except (TypeError, ValueError):
        return "—"


def scope_label(run):
    return {"subset": "小样本演示", "full": "全量数据", "unknown": "规模未记录"}[run_scope(run)]


def run_label(run):
    condition=audit_condition(run)
    return f"{run['name']} · {CONDITION_NAMES.get(condition,condition)} · {scope_label(run)}"


def choose_run(runs, key):
    if not runs:
        st.info("尚无实验记录。先到训练控制台启动一次任务。")
        return None
    names = [run["name"] for run in runs]
    preferred = st.session_state.get("selected_run")
    index = names.index(preferred) if preferred in names else 0
    selected = st.selectbox("选择运行记录", range(len(runs)), index=index, format_func=lambda i: run_label(runs[i]), key=key)
    return runs[selected]


def errors(run):
    for error in run["errors"][:5]:
        st.warning(f"文件读取提示：{error}")


@st.fragment(run_every="4s")
def show_run(path: str):
    run = load_run(Path(path))
    state, summary = run["status"], run["summary"]
    live = is_live(run)
    if live:
        st.success("● 实时任务 · 正在读取当前进程产生的结果")
    else:
        st.caption("历史记录 · 展示本次运行保存的结果")
    errors(run)
    cols = st.columns(4)
    last = run["rounds"][-1] if run["rounds"] else {}
    cols[0].metric("当前状态", STATE_NAMES.get(state.get("state"), "尚未报告"))
    cols[1].metric("测试准确率", accuracy(last.get("accuracy", summary.get("final_accuracy"))))
    cols[2].metric("已完成轮次", f"{int(last.get('round', 0))} / {state.get('total_rounds', run['config'].get('rounds', '—'))}")
    cols[3].metric("平均轮次耗时", number(summary.get("mean_round_seconds"), " s"))
    total = state.get("total_rounds", run["config"].get("rounds", 0))
    if total:
        st.progress(min(1.0, max(0, float(last.get("round", 0)) / int(total))))
    st.caption(f"{MODE_NAMES.get(run['mode'], run['mode'])} · {scope_label(run)} · 最近更新：{state.get('updated_at', '等待首条状态')} ")
    if state.get("error"):
        st.error(str(state["error"]))
    clients = state.get("clients", [])
    if clients:
        clients_frame=pd.DataFrame(clients)
        if 'state' in clients_frame:clients_frame['state']=clients_frame['state'].map(lambda x:CLIENT_STATE_NAMES.get(x,x))
        st.dataframe(clients_frame.rename(columns={"client_id": "客户端", "state": "状态"}), hide_index=True, use_container_width=True)
    frame = pd.DataFrame(run["rounds"])
    if not frame.empty and "round" in frame:
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**学习曲线**")
            if "accuracy" in frame:
                st.line_chart(frame.set_index("round")[["accuracy"]], height=250)
            st.caption(f"最近测试损失：{number(last.get('loss'), decimals=4)}")
        with c2:
            st.markdown("**每轮时间 / 秒**")
            fields = [f for f in ("round_seconds", "train_seconds", "audit_seconds") if f in frame]
            if fields:
                st.line_chart(frame.set_index("round")[fields], height=250)
    with st.expander("运行日志", expanded=state.get("state") == "failed"):
        st.code(tail_log(run["path"]), language="text")
    with st.expander("本次配置与结果数据"):
        st.json(run["config"])
        if summary:
            st.json(summary)
        if not frame.empty:
            st.dataframe(frame, hide_index=True, use_container_width=True)
            st.download_button("下载轮次 CSV", frame.to_csv(index=False), file_name=f"{run['name']}-rounds.csv", mime="text/csv", key=f"csv-{run['name']}")


def train_page(runs):
    st.markdown("### 创建训练任务")
    st.caption("四个客户端持有各自的数据分区，FedAvg 聚合模型；审计模式同步记录每轮模型承诺。")
    show_network()
    active = [run for run in runs if is_live(run)]
    busy=job_status()
    if busy:
        st.info(f"项目任务占用中：{busy.get('operation','未知任务')} · PID {busy.get('pid',busy.get('owner_pid','—'))}")
    with st.form("new-training"):
        c1, c2, c3 = st.columns([1.5, 1, 1])
        mode = c1.selectbox("训练模式", ["blockchain", "baseline"], format_func=lambda value: MODE_NAMES[value])
        strategy=st.selectbox("审计提交方式",['sync','pipelined'],format_func=lambda value:STRATEGY_NAMES[value],help="审计模式生效；并发方式仍等待四笔交易全部成功并核验后再聚合。")
        rounds = c2.number_input("全局轮数", min_value=1, max_value=100, value=10, step=1)
        seed = c3.number_input("随机种子", min_value=0, max_value=2147483647, value=42, step=1)
        scale = st.radio("数据规模", ["全量 CIFAR-10", "小样本演示"], horizontal=True)
        c1, c2 = st.columns(2)
        train_limit = c1.number_input("演示训练图片数", min_value=4, max_value=50000, value=2000, step=4, help="只在小样本演示模式生效。")
        test_limit = c2.number_input("演示测试图片数", min_value=4, max_value=10000, value=1000, step=4, help="只在小样本演示模式生效。")
        submitted = st.form_submit_button("启动真实训练", type="primary", disabled=bool(active) or bool(busy), use_container_width=True)
    if active:
        st.info("已有训练任务运行中，本机一次执行一个任务。")
    if submitted:
        try:
            output = launch_training(mode, int(rounds), int(seed), int(train_limit) if scale == "小样本演示" else 0, int(test_limit) if scale == "小样本演示" else 0,audit_strategy=strategy if mode=='blockchain' else 'sync')
            st.session_state["selected_run"] = output.name
            st.success(f"已创建任务：{output.name}")
            st.rerun()
        except Exception as exc:
            st.error(f"任务启动失败：{exc}")
    st.divider()
    run = choose_run(runs, "train-run")
    if run:
        show_run(str(run["path"]))


def history_page(runs):
    st.markdown("### 运行记录")
    if runs:
        rows = [{"运行": r["name"], "模式": MODE_NAMES.get(r["mode"], r["mode"]), "规模": scope_label(r), "状态": STATE_NAMES.get(r["status"].get("state"), "未报告"), "准确率": accuracy(r["summary"].get("final_accuracy")), "总耗时 / s": r["summary"].get("total_seconds"), "实时进程": is_live(r)} for r in runs]
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    run = choose_run(runs, "history-run")
    if run:
        show_run(str(run["path"]))


def audit_page(runs):
    st.markdown("### 链上审计与模型校验")
    show_network()
    run = choose_run(runs, "audit-run")
    if not run:
        return
    errors(run)
    if run["mode"] != "blockchain":
        st.info("这次运行采用普通 FedAvg；可查看模型清单，链上交易仅在审计模式产生。")
    txs = pd.DataFrame(run["transactions"])
    c1, c2, c3 = st.columns(3)
    c1.metric("已保存交易凭证", str(len(txs)))
    if not txs.empty and "status" in txs:
        c2.metric("成功回执", str(int(pd.to_numeric(txs["status"], errors="coerce").eq(1).sum())))
    else:
        c2.metric("成功回执", "—")
    if not txs.empty and "latency_seconds" in txs:
        c3.metric("平均确认延迟", number(pd.to_numeric(txs["latency_seconds"], errors="coerce").mean(), " s"))
    else:
        c3.metric("平均确认延迟", "—")
    if txs.empty:
        st.caption("尚无已保存的交易回执。")
    else:
        st.caption("下表读取本次运行保存的交易凭证；检查最新链状态需运行网络验证命令。")
        st.dataframe(txs, use_container_width=True, hide_index=True)
        st.download_button("下载交易凭证 JSONL", "\n".join(json.dumps(t, ensure_ascii=False) for t in run["transactions"]), file_name=f"{run['name']}-transactions.jsonl", mime="application/x-ndjson")
    if run["verifications"]:
        with st.expander("训练期间的独立合约读回记录"):
            st.dataframe(pd.DataFrame(run["verifications"]), use_container_width=True, hide_index=True)
    manifest = run["manifest"]
    if not manifest:
        st.info("模型清单尚未生成，完成第一个本地更新后可校验。")
        return
    st.markdown("**选择承诺并校验模型**")
    choice = st.selectbox("模型记录", range(len(manifest)), format_func=lambda i: f"第 {manifest[i].get('round', '—')} 轮 · {manifest[i].get('kind', 'model')} · 客户端 {manifest[i].get('client_id', '聚合')} · {manifest[i].get('path', '')}")
    record = manifest[choice]
    expected = str(record.get("hash", "")).removeprefix("0x")
    # An independently saved chain digest is preferred to the local manifest.
    candidates = []
    for tx in run["transactions"] + run["events"]:
        digest = tx.get("hash", tx.get("digest", tx.get("update_hash", tx.get("aggregate_hash"))))
        same_round = "round" not in tx or str(tx["round"]) == str(record.get("round"))
        same_client = "client_id" not in record or "client_id" not in tx or str(tx["client_id"]) == str(record["client_id"])
        if digest and same_round and same_client and str(digest).removeprefix("0x").lower() == expected.lower() and str(tx.get("status", 1)) == "1":
            candidates.append(tx)
    st.code(expected, language="text")
    if candidates:
        st.caption("期望哈希与本次保存的链上凭证一致。")
    else:
        st.caption("当前期望哈希来自模型清单；未找到可独立对应的链上哈希凭证。")
    upload = st.file_uploader("可上传一个 .npz 模型，或直接校验所选记录的原文件", type=["npz"])
    can_query = bool(run["audit_context"]) and int(record.get("round", 0)) > 0
    live_query = st.checkbox("同时从当前网络读取该轮次的链上承诺", value=can_query, disabled=not can_query)
    if st.button("计算并比对哈希", type="primary"):
        try:
            from fl.artifacts import hash_parameters, load_parameters
            if upload is not None:
                if upload.size > 32 * 1024 * 1024:
                    raise ValueError("模型文件超过 32 MiB 限制")
                with np.load(io.BytesIO(upload.getvalue()), allow_pickle=False) as archive:
                    parameters = {name: archive[name] for name in archive.files}
                actual = hash_parameters(parameters)
            else:
                relative = Path(record["path"])
                model_path = within_results(run["path"] / relative)
                if not model_path.is_relative_to(run["path"].resolve()):
                    raise ValueError("模型文件路径必须位于所选运行目录内")
                actual = hash_parameters(load_parameters(model_path))
            if live_query:
                proof = query_commitment(run, record)
                expected = proof["hash"]
                st.caption(f"实时 eth_call · 区块 {proof['block_number']} · chain ID {proof['chain_id']} · 合约 {proof['contract_address']}")
                st.code(expected, language="text")
            else:
                st.caption("本次为历史证据 / 模型清单比对，未请求当前链状态。")
            st.code(actual, language="text")
            if expected and actual.removeprefix("0x").lower() == expected.lower():
                st.success("哈希一致：文件参数与所选承诺相符。")
            else:
                st.error("哈希不一致：文件参数已变化，或选择了不同的模型记录。")
        except Exception as exc:
            st.error(f"无法校验：{exc}")
    st.caption("哈希证明内容一致性；训练诚实性、聚合正确性和隐私仍需要各自的验证机制。")


def comparison_page(runs):
    st.markdown("### 有链 / 无链对照")
    st.caption("独立重复单位为完整运行；同一次运行的多个轮次不作为独立样本。")
    completed = [r for r in runs if r["status"].get("state") == "completed" and r["summary"]]
    if not completed:
        st.info("完整运行完成后，这里会展示实测结果。")
        return
    scope = st.radio("比较数据范围", ["full", "subset"], format_func=lambda x: "全量数据" if x == "full" else "小样本演示", horizontal=True)
    selected = [r for r in completed if run_scope(r) == scope]
    if not selected:
        st.info("该范围内尚无已完成运行。")
        return
    studies=sorted({r['config'].get('study_id') or '历史实验（未标注批次）' for r in selected})
    preferred_study=selected[0]['config'].get('study_id') or '历史实验（未标注批次）'
    study=st.selectbox('实验批次',studies,index=studies.index(preferred_study),help='不同开发阶段的实验分开汇总，避免重复种子混入。')
    selected=[r for r in selected if (r['config'].get('study_id') or '历史实验（未标注批次）')==study]
    groups = sorted(set(comparison_key(r) for r in selected))
    group = st.selectbox("相同训练配置组", groups, help="按轮数、数据规模、模型、学习率及部署方式等已记录字段分组。")
    selected = [r for r in selected if comparison_key(r) == group]
    rows = []
    for run in selected:
        summary = run["summary"]
        rows.append({"run": run["name"], "condition": audit_condition(run), "seed": summary.get("seed", run["config"].get("seed")), "accuracy": summary.get("final_accuracy"), "total_seconds": summary.get("total_seconds"), "mean_round_seconds": summary.get("mean_round_seconds")})
    frame = pd.DataFrame(rows)
    st.dataframe(frame, use_container_width=True, hide_index=True)
    for column in ("accuracy", "total_seconds", "mean_round_seconds"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    grouped = frame.groupby("condition").agg(runs=("run", "count"), accuracy_mean=("accuracy", "mean"), accuracy_sd=("accuracy", "std"), seconds_mean=("total_seconds", "mean"), seconds_sd=("total_seconds", "std"))
    grouped.index=grouped.index.map(lambda x:CONDITION_NAMES.get(x,x))
    st.markdown("**完整运行的描述性汇总**")
    st.dataframe(grouped, use_container_width=True)
    seed_counts = frame.groupby(["seed", "condition"]).size()
    if (seed_counts > 1).any():
        st.warning("同一种子和模式存在多次运行；请在正式分析中指定重复运行选择规则。此处保留全部记录，不任意挑选。")
    else:
        times = frame.pivot(index="seed", columns="condition", values="total_seconds")
        for first,second in [('baseline','blockchain_sync'),('blockchain_sync','blockchain_pipelined'),('baseline','blockchain_pipelined')]:
            if {first,second}.issubset(times.columns):
                paired=times[[first,second]].dropna()
                invalid_seeds = []
                for seed in paired.index:
                    members = [r for r in selected if r["summary"].get("seed", r["config"].get("seed")) == seed and audit_condition(r) in (first,second)]
                    ok, reason = valid_pair(members[0], members[1])
                    if not ok:
                        invalid_seeds.append(seed)
                        st.warning(f"种子 {seed} 不计入配对差值：{reason}")
                paired = paired.drop(index=invalid_seeds)
                paired["差值 / 秒"] = paired[second] - paired[first]
                paired=paired.rename(columns=CONDITION_NAMES)
                st.markdown(f"**同种子配对：{CONDITION_NAMES[second]} − {CONDITION_NAMES[first]}**")
                st.dataframe(paired, use_container_width=True)
    chart = frame.set_index("run")[["total_seconds"]]
    st.bar_chart(chart, height=280)
    st.caption("少量重复用于描述变异；不会预设区块链提升准确率或产生统计显著性。")


with st.sidebar:
    st.markdown("## ◈ BlockFL")
    st.caption("联邦训练 · 联盟链审计")
    st.divider()
    page = st.radio("工作区", ["训练控制台", "运行记录", "链上审计与校验", "对照实验"], label_visibility="collapsed")
    st.divider()
    st.markdown("**实验拓扑**")
    st.caption("4 个训练客户端\n\n1 个 FedAvg 聚合服务\n\n4 个 QBFT 验证节点")
    st.caption("数据与模型保留在链下，身份、轮次与模型承诺写入共享账本。")
    if st.button("刷新运行列表", use_container_width=True):
        st.rerun()

st.markdown("""<div class="hero"><div class="eyebrow">BLOCKCHAIN-ENABLED FEDERATED LEARNING</div><h1>让协作训练留下可核查的记录</h1><p>从本地模型更新到全局聚合，连接真实训练、共享审计与可复现实验。</p></div>""", unsafe_allow_html=True)
runs = list_runs()
{"训练控制台": train_page, "运行记录": history_page, "链上审计与校验": audit_page, "对照实验": comparison_page}[page](runs)

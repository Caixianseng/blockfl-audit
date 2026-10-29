# BlockFL Audit

基于 Besu QBFT 的联邦图像分类训练与审计演示。四个逻辑客户端用 PyTorch 本地训练，Flower FedAvg 按样本数聚合；Solidity 合约记录每轮参与者、更新承诺和全局模型承诺。

公开仓库包含源码、测试、报告、图表和可离线核验的模型/交易实验凭证。Python/Node依赖、CIFAR-10、JDK/Besu、签名密钥及本机账本由复现步骤在本地生成或下载，版本依据 `requirements.lock`、`contracts/package-lock.json` 和 `tools/runtime.lock.json`。已保存结果可以离线分析；实时链上查询需要对应原账本，重新部署后应运行新的实验生成新凭证。

当前实现是单机课程研究原型。四个逻辑客户端顺序执行并共享 GPU，由真实 Flower `NumPyClient` 和 `FedAvg` 聚合，未启动 gRPC 网络；模型传输量是确定性序列化参数负载。四个 Besu 验证者是独立的真实进程，执行 QBFT 共识。报告必须保留这一部署边界。

## 本轮优化

已修正网络就绪判定并统一任务互斥，新增 `pipelined` 审计策略。原始实验报告在 `report/report.md`，对应的 v1 源码已另存为工作区中的 `blockfl-audit-source-v1.zip`；优化版实测和证据见 `report/optimization.md`。

- 就绪检查验证四个受管进程、chain ID、共同 genesis hash、预期验证者集合，并观察四节点新区块推进；RPC 暂不可用在截止时间内等待，错误身份直接拒绝。
- 训练、批次、部署、故障实验和网络变更共用 OS 文件锁。进程退出后由 OS 释放锁；批次给真实后代进程委托，避免父子互相阻塞。界面显示占用任务并在启动入口复核。
- `sync` 逐笔发送并等待；`pipelined` 保持 GPU 顺序训练，将更新提交与后续训练重叠，最后集中确认四个更新，再重新读取模型文件和链上承诺，全部通过才聚合。
- 交易先保存确定性签名哈希和提交票据；等待失败保留待对账证据，不盲目重发。完整的跨进程断点续训仍是后续工作。

```powershell
# 选择优化策略（同步模式仍为默认）
.venv/Scripts/python.exe -m experiments.run --mode blockchain --audit-strategy pipelined --rounds 10 --output results/my-pipelined-run --manage-network

# 使用相同代码与完整数据，按3个种子比较同步/并发两种审计策略
.venv/Scripts/python.exe -m experiments.compare_audit --rounds 10 --seeds 42,43,44 --name my-audit-comparison

# 验证冲突命令会被拒绝；执行此验证时不要同时运行其他任务
.venv/Scripts/python.exe -m experiments.check_mutual_exclusion
```

并发策略的 `confirmation_seconds` 表示发送到首次观察到回执的时间，可能包含后续训练和调度等待，不能直接当作物理共识最终确认时刻。性能比较以完整轮次/训练循环时间为主，并检查每轮模型哈希和审计记录一致。不同批次在界面中分开汇总。

## 本机运行

在本目录打开 PowerShell，所有命令均以本目录为工作目录：

```powershell
# 启动四个真实验证节点
.venv/Scripts/python.exe -m network.manage start
.venv/Scripts/python.exe -m network.manage status

# 仅首次部署（重置链后须重新部署）
.venv/Scripts/python.exe -m audit.chain deploy

# 启动中文实验界面
.venv/Scripts/python.exe -m streamlit run dashboard/app.py --server.address 127.0.0.1 --server.port 8501 --browser.gatherUsageStats false
```

访问 http://127.0.0.1:8501。启动后在界面选择训练模式、轮数和样本规模。子集演示与全量实验在结果中分别标记。不要在运行训练时同时部署合约或运行故障实验。

## 复现环境

Python 3.12、Node.js 20；GPU 可用时采用 CUDA，否则明确选择 CPU。本机实现使用项目内便携 JDK 25 与 Besu 26.10.2，未替换系统 Java，也没有安装 Docker/WSL。

新机器先创建环境并安装依赖，已测试的完整依赖记录在 `requirements.lock`：

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements.in
# 或使用发布包内的 requirements.lock，CUDA wheels 需要上述 extra index
.venv/Scripts/python.exe tools/bootstrap_runtime.py
# 若 GitHub 直连不可达：使用公开镜像传输，仍校验独立官方 SHA-256
.venv/Scripts/python.exe tools/bootstrap_runtime.py --mirror

cd contracts
npm ci --cache .npm-cache
npm run compile
cd ..

.venv/Scripts/python.exe -m network.manage start
.venv/Scripts/python.exe -m audit.chain deploy
```

`tools/bootstrap_runtime.py` 会下载到项目目录并校验摘要。CIFAR-10 默认从 Toronto 原始源获取，检查上游归档 MD5 与批次完整性。本机使用 Paddle 官方代码列出的同一归档镜像加速：`python -m fl.download_data --source paddle`，源及校验值记录在 `data/cifar10-source.json` 和每次实验配置。该工具使用有界 Range 下载。下载完成后训练仅使用本地数据。

首次生成的 `network/accounts.json` 包含本地演示账户签名密钥，`network/nodes/` 包含验证节点密钥与账本。它们与运行时、数据集、依赖目录均不纳入源码包；部署与训练凭证不写私钥。

## 命令行实验

```powershell
# 短演示：512训练样本、128测试样本；不是正式精度结果
.venv/Scripts/python.exe -m experiments.run --mode baseline --rounds 2 --train-limit 512 --test-limit 128 --output results/my-smoke-baseline --manage-network
.venv/Scripts/python.exe -m experiments.run --mode blockchain --rounds 2 --train-limit 512 --test-limit 128 --output results/my-smoke-chain --manage-network

# 主实验：完整CIFAR-10，10轮，每客户端每轮1 epoch；3个种子配对
.venv/Scripts/python.exe -m experiments.batch --rounds 10 --seeds 42,43,44 --name my-full-study

# 在链上实时读取承诺，校验原始模型与修改后的副本
.venv/Scripts/python.exe -m experiments.integrity results/my-smoke-chain

# 运行权限/轮次/防覆盖合约测试（需真实链）
$env:BLOCKFL_LIVE_TESTS='1'
.venv/Scripts/python.exe -m pytest tests/test_contract_live.py -v
Remove-Item Env:BLOCKFL_LIVE_TESTS

# 独立故障实验：停止一个、再停止第二个验证者，然后恢复
.venv/Scripts/python.exe -m experiments.fault_test

# 汇总真实结果生成PNG/PDF、配对统计及来源清单
.venv/Scripts/python.exe -m experiments.plot_results --scope all --output report/figures

# 停止本项目四个验证节点
.venv/Scripts/python.exe -m network.manage stop
```

每次输出目录必须新建；已有训练结果会拒绝覆盖。`experiments.run` 具有进程锁，界面也会检测活跃训练任务。`--manage-network` 在基线训练时停止链，在有链模式时启动并等待四节点就绪。

## 审计流程与证据

创建独立 run → 开轮交易确认 → 四客户端顺序本地训练 → 各自签名提交更新哈希并确认 → 读取合约并校验实际模型文件 → Flower FedAvg → 聚合哈希确认 → 测试集评估 → 下一轮。

`results/<run>/` 包括：

- `config.json`、`data_indices.json`：实际训练配置、依赖版本与固定数据分区。
- `global_000.npz`、`global_*.npz`、`updates/*.npz`、`manifest.jsonl`：初始模型、各轮输入输出和确定性哈希。
- `rounds.csv`、`clients.csv`、`resources.csv`、`summary.json`：精度、损失、耗时、序列化参数负载、CPU和RSS。
- `audit_context.json`、`transactions.jsonl`、`submitted_transactions.jsonl`、`verifications.jsonl`：运行标识、签名交易回执、事件和独立链上读回校验。
- `status.json`、`training.log`、`run.log`：实时状态与可追溯日志。

哈希规范在 `fl/artifacts.py`。它包含参数名、形状、dtype、固定端序与参数字节，不依赖 ZIP 时间戳。原始数据与模型参数均不上链。

`round_seconds` 包含本轮开轮、承诺、核验、聚合、保存和评估。`train_seconds` 只计本地 SGD。`audit_setup_seconds` 单列创建运行的链上开销；`workflow_wall_seconds` 还包含启动准备和数据读取。GPU计时边界同步设备。

`application_audit_tps` 以首笔提交到最后一笔回执的窗口计算，包含创建运行及准备间隙，是应用实际确认交易率，不能当成链的饱和吞吐量。账本增长为四节点文件合计（排除日志），包含空块与复制。CPU 百分比采用 psutil 进程口径，100%代表占用一个逻辑处理器；RSS不是显存，系统空闲资源会随其他应用变化。

## 能力边界

哈希支持承诺后模型改动检测；不能证明诚实训练、正确聚合或隐私安全。聚合器仍是信任和可用性边界。停节点实验验证停止故障下的共识门槛，不代表完整的拜占庭攻击或网络分区测试。单机逻辑客户端不提供机构间操作系统隔离。

差分隐私、安全聚合、模型投毒防御和零知识证明在报告中作为后续工作讨论。运行指标均由实验产生，不预设区块链会提高准确率。

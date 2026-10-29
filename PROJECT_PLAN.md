**联盟链联邦学习项目：开源复用、Skills 与一天开发计划**

编制日期：2026-10-29（Asia/Shanghai）。执行者：Codex，按需并行处理网络/合约、训练、界面与验证。工期目标：24 小时内完成课程研究演示版；下表是估算与验收计划，不是已经完成的实现或实验结果。

本计划依据用户提供的《Blockchain Enabled Federated Learning》proposal。研究问题是：联盟链能为联邦训练增加哪些可核查的审计证据，为此增加多少延迟、存储和计算开销。

**推荐路线：官方 Flower/PyTorch 示例 + Besu QBFT + 自写审计合约 + Streamlit 演示界面。**

目前没有在本次有界检索中找到已核实完全符合“四个 Flower/PyTorch 客户端、四个 Besu QBFT 验证节点、指定审计约束”的即用整套项目。以下复用建议基于仓库源码、README、许可证与官方文档检查；尚未安装或运行候选仓库。

**开源项目与采用方式**

| 项目 | 已核实内容 | 本项目采用方式 |
|---|---|---|
| [Flower / quickstart-pytorch](https://github.com/flwrlabs/flower/tree/main/examples/quickstart-pytorch) | 官方 PyTorch/CIFAR-10/FedAvg 示例；Flower 主仓库 Apache-2.0、未归档 | 复用模型、数据加载与训练流程；改为四客户端，新增审计接入、日志与实验配置 |
| [Besu](https://github.com/besu-eth/besu) 与 [Besu Dev Quickstart](https://github.com/Consensys-Incorporated/besu-dev-quickstart) | 均为 Apache-2.0、未归档；官方 Quickstart 的私有网络包含四个 QBFT 验证节点、一个 RPC 节点和监控服务 | 采用官方 QBFT 配置结构；优先精简到四验证节点，监控用轻量采样，避免一天任务的额外服务负担 |
| [OpenZeppelin Contracts](https://github.com/OpenZeppelin/openzeppelin-contracts) | MIT；提供 Ownable、AccessControl 等访问控制组件 | 使用角色控制管理员、聚合者和训练客户端；轮次与防重复逻辑自己实现 |
| [R3T](https://github.com/linhnt31/R3T) | 有 Flower、模型哈希记录与指标采集，但实际使用 TensorFlow、Ganache/Truffle、Pinata；合约缺少本计划需要的权限及防覆盖约束 | 仅参考结构；不作为整套工程起点。README、LICENSE 与个别文件许可标记存在差异，复制代码前需逐文件核对 |

旧的 [quorum-dev-quickstart](https://github.com/Consensys/quorum-dev-quickstart) 已归档，官方引导使用新的 Besu Dev Quickstart。应更新原 proposal 的实现引用。

发现一个版本细节：Besu 文档当前命令中的 npm scope 为 `@consensys-software`，仓库 README/package.json 为 `@consensys`。实施时从已核对仓库的固定 commit 获取模板，并核实发布包身份；不直接执行未核实的同名 npm 包。不要把 main 分支当成版本锁。

Flower 当前示例的 pyproject.toml 使用 `torch==2.10.0`、`torchvision==0.25.0` 和较新的 Flower API；本机已有 PyTorch 2.7.1。实施时选择一个通过验证的完整依赖组合，固定版本，避免混用旧版 NumPyClient 教程和新版 ClientApp/ServerApp 接口。

**可使用的 Skills**

已对本地技能目录和插件缓存中的 SKILL.md 路径、frontmatter 做过检查，未发现 Besu、QBFT、Flower、Solidity 或区块链联邦学习专用 skill。

| Skill | 来源/状态 | 在一天计划中的用途 |
|---|---|---|
| [get-available-resources](C:/Users/CAI/.codex/skills/get-available-resources/SKILL.md) | 本机已安装，本次已执行只读探测 | 资源预算、GPU 候选和可用内存检查 |
| [experimental-design](C:/Users/CAI/.codex/skills/experimental-design/SKILL.md) | 本机已安装，本次用于设计 | 同种子配对实验、运行顺序、重复层级、控制混杂因素 |
| [matplotlib](C:/Users/CAI/.codex/skills/scientific-toolkit-skill/references/scientific-skills/matplotlib/SKILL.md) | 本机已安装 | 从实测 CSV 生成准确率、耗时、交易延迟和资源图 |
| [scientific-writing](C:/Users/CAI/.codex/skills/scientific-writing/SKILL.md) | 本机已安装 | 基于真实结果编写方法、结果和局限；正式报告阶段调用 |
| [setup-solidity-contracts](https://github.com/OpenZeppelin/openzeppelin-skills/blob/main/skills/setup-solidity-contracts/SKILL.md) | OpenZeppelin 官方外部 skill，已核查，未安装 | 合约项目初始化、依赖和编译配置 |
| [develop-secure-contracts](https://github.com/OpenZeppelin/openzeppelin-skills/blob/main/skills/develop-secure-contracts/SKILL.md) | OpenZeppelin 官方外部 skill，已核查，未安装 | 角色权限和安全开发检查；不能替代运行测试 |

OpenZeppelin 两个 skill 的许可标记为 AGPL-3.0-only，Contracts 库的许可为 MIT，二者应分别记录。外部 skill 安装不是开工前置条件；现有技能加官方文档足以支持开发。PUA 按用户全局配置用于诊断与验收纪律。

**本机环境证据与开工门槛**

| 检查项 | 2026-10-29 的只读检查结果 | 排期影响 |
|---|---|---|
| CPU | 8 个物理核、16 个逻辑处理器；当前进程亲和性覆盖 16 个逻辑处理器 | 给训练进程限制线程，留资源给验证节点 |
| 内存 | 总量约 31.7 GiB，检查时可用约 6.7 GiB | 使用小模型、受控训练并发和精简监控；运行前重新读取可用量 |
| GPU | RTX 3070 Laptop，8 GiB 显存；检查时空闲约 5.9 GiB | 避免四个客户端同时占满 GPU；逻辑客户端可按固定顺序训练 |
| Python | 默认环境 Python 3.12.7；默认环境无 torch/flwr/web3 | 使用项目隔离环境 |
| 已有 PyTorch 环境 | Python 3.12.7、torch 2.7.1+cu128；torch.cuda.is_available() 为 True；无 flwr/web3 | 可复用兼容的包版本或缓存；还需真实张量与小模型验证 |
| Java | PATH 中为 Java 14.0.2 | 不能据此启动新版 Besu；需匹配版本的项目内 JDK |
| Docker/WSL | PATH 和常规 Docker 安装位置未发现 Docker；wsl --status 退出码 1、无输出 | 尚未确认可用，须列为第一阶段关键风险，不能声称容器环境已就绪 |
| 磁盘 | 工作盘空闲约 88.4 GiB | 规划模型、数据、环境和账本目录，实际占用随运行记录 |

首选路径仍是文档中的 Docker Compose。若 Docker/WSL 的准备明显影响工期，验证“官方 Besu 二进制 + 匹配 JDK + 四个独立进程”的本机路径。该路径仍保留四节点 QBFT，但需要报告部署方式与 proposal 的差异。官方 QBFT 教程列出 Windows 启动命令；当前安装说明的 JDK 要求为 25+。原生路径也必须先通过出块与交易确认测试，不能把文档存在视为已经运行可用。

H+2 的环境门槛：四个真实验证节点能够出块并确认一笔交易；训练环境完成一次真实小批次训练；CIFAR-10 能获取并校验。若未达到，应立即调整环境路径并给出已确认的阻塞证据，更新剩余工期。不能用单个 Hardhat 节点冒充 QBFT，也不能用模拟实验数字填补缺失结果。

**一天内的功能范围**

- 四个逻辑 FL 客户端，各持有固定 CIFAR-10 数据分区。
- 一个 Flower 中心聚合服务，使用按样本数加权的 FedAvg。
- 四个 Besu QBFT 验证节点；训练数据和模型张量在链下，链上保存身份、轮次和哈希。
- 审计合约与 Python 链接入模块；等待成功交易回执后推进相应阶段。
- 一个 Streamlit 控制台：训练任务、客户端状态、训练曲线、审计记录、验证与实验结果。
- 两种训练模式、重复实验脚本、资源记录、合约测试和故障实验。
- README、锁定依赖、运行命令、原始结果、图表与英文报告初稿。

non-IID、客户端掉线、投毒、隐私泄漏、CAP 与聚合器信任边界按文档要求在报告讨论；其中 IID/non-IID 的额外对照可在核心验收后增加。安全聚合、差分隐私、零知识证明和完全去中心化聚合保留为未来工作。

**实现约定**

| 模块 | 设计与验证重点 |
|---|---|
| 训练 | 小型 CNN；固定训练/测试划分；四客户端；保留训练样本数、损失、准确率、耗时与模型文件 |
| 聚合 | 同一套 FedAvg 代码通过审计开关形成 A/B 两模式；模型输入、权重和客户端顺序保持一致 |
| 合约 | `runId + roundId + participant` 定位提交；管理员授权、聚合者开轮/结束、客户端提交；禁止越权、错轮、重复覆盖及提前结束 |
| 哈希 | 明确确定性序列化规则，包括参数键顺序、形状、dtype、字节序与原始字节；模型文件与哈希算法版本一起记录 |
| 链接入 | 各客户端用各自身份签名；记录链 ID、合约地址、nonce、交易哈希、回执状态、区块号和确认时延；失败后先查状态再决定是否重试 |
| 同步流程 | 开轮确认 → 本地训练 → 客户端提交承诺并确认 → 链下模型核验 → FedAvg → 聚合哈希确认 → 下一轮 |
| 文件保留 | 每次运行独立目录，配置与数据索引可追踪，保留参与聚合的原始更新；链本身不提供链下文件可用性 |
| 界面 | 训练在独立任务进程执行，界面读取日志与结果；当前实时运行与历史结果明确标注 |
| 部署 | 本地回环 RPC、私有 P2P 网络与明确许可配置；四验证者名单和合约白名单分开管理；单机演示不代表跨机构治理已实现 |

预计每个正常轮次有 4 次客户端提交和 1 次聚合提交；若开轮为独立交易则额外计入。部署、授权、开轮与失败交易按类别记录，不能忽略其开销。

Hash 核验只验证收到的模型更新是否与链上承诺相符。链上最终模型哈希不能证明聚合计算正确；保存输入与确定性聚合脚本可以供链下复算，但聚合器仍保留信任与可用性风险。

**排期：以开工时刻 T0 计算**

| 时间段 | 工作 | 交付与验收 |
|---|---|---|
| H+0～2 | 环境、依赖候选、网络最小验证、数据准备 | 四验证节点确认交易；小批次真实训练；记录最终版本候选 |
| H+2～5 | 并行完成四客户端 FedAvg、审计合约及网络脚本 | 无链模式完整跑 2 轮；合约权限/轮次/重复测试通过 |
| H+5～8 | 接入哈希、签名、回执、轮次状态和链下校验 | 有链模式真实跑 2 轮，逐条对应模型文件、合约事件和回执 |
| H+8～10 | 接入 Streamlit 展示与实验启动入口 | 页面能启动任务、查看状态和交易、校验文件；结果来自当前工程 |
| H+10～14 | 跑配对重复实验、资源采样和故障实验 | 原始 CSV/JSON、模型、回执、测试日志完整；新增问题修复后重跑受影响项 |
| H+14～16 | 汇总图表、报告初稿、README、独立运行目录复现 | 按 README 完成一次短演示，证据文件相互对应 |
| H+16～24 | 依赖下载、性能和联调缓冲；完成尚未通过的核心验收 | 输出实测交付清单，清晰列出任何未通过项 |

并行安排：主执行者负责接口和最终集成；协作任务 A 负责网络/合约，B 负责训练/指标，C 在接口确定后负责界面/图表。训练实验期间同时整理报告。界面美化、完整链浏览器及扩展攻击实验不占用核心验收的缓冲时间。

**实验方案**

主要实验单元是“一次完整训练运行”，同一运行中的多个轮次不是独立重复。

| 项目 | 初始计划 |
|---|---|
| 数据 | CIFAR-10 完整训练集固定分成四份；固定独立测试集。短 smoke 测试可用明确标注的子集，正式主对照统一数据规模 |
| 模型与训练 | 小型 CNN；每客户端每轮 1 个本地 epoch；目标 10 轮；四客户端全部参与 |
| 规模确定 | 先测小批次与 1 轮耗时，再冻结正式实验配置；若需调整轮数/样本数，两模式统一调整，记录原因，不能按结果好坏挑选 |
| A 模式 | 普通 FedAvg，审计关闭 |
| B 模式 | 同配置 FedAvg，同步审计开启 |
| 重复 | 3 个固定种子，例如 42、43、44，每种子各跑 A/B；配对共享初始化、划分、批次顺序和训练配置 |
| 顺序 | A/B 顺序按记录的调度随机种子随机化，避免所有基线先跑造成温度/系统负载偏差 |
| 共享 GPU | 固定客户端执行顺序或并发调度，两模式一致；控制 CPU 原生线程，记录设备与调度 |
| 数据加载 | 下载和首次缓存预热单独计时；主实验的起止点一致 |
| 运行隔离 | 每次模型重置；每次链运行有独立 runId，记录起始区块与账本大小，必要时使用独立测试链数据目录 |
| 输出统计 | 展示每次完整运行、配对差值、均值和标准差；3 次重复主要用于描述性结果，不预设显著性结论 |

所需指标：每轮准确率与损失、完整轮次时间、总运行时间、交易提交到成功回执的延迟、成功确认的审计交易率、上下行模型张量传输字节、账本目录增量、训练与验证节点各自 CPU/RAM。GPU 训练耗时需在计时边界同步设备。

模型字节数需明确是张量/应用负载还是完整协议流量；账本增长注明每节点或全网合计，包含交易/回执/状态/复制及空块等因素。应用实际交易率不能称为饱和 TPS。对比总开销时计入 B 模式网络运行资源，同时分别展示训练开销和验证节点开销。

**必须通过的验收**

| 编号 | 验收项 | 证据 |
|---|---|---|
| A1 | 四节点真实 QBFT 连续出块并确认交易 | 验证者列表、区块/交易回执、各节点日志 |
| A2 | 四客户端完成真实 FedAvg 训练 | 客户端日志、参数更新、测试集指标、全局模型 |
| A3 | 每个正常轮次四条更新承诺和一个聚合承诺可追溯 | 文件、确定性哈希、参与者、轮次、事件和回执一致 |
| A4 | 未授权、错轮、重复提交被拒绝；合法记录不被覆盖 | 合约测试输出与链上失败/成功证据 |
| A5 | 修改已承诺模型文件后检测到不一致 | 原始/篡改文件哈希与校验输出 |
| A6 | 停一个验证者仍可确认新交易 | 对仍运行节点查询到的新区块和回执 |
| A7 | 停两个验证者后在预定观察窗内无法确认新交易，恢复后验证继续出块 | 区块高度、待确认交易、超时与恢复日志；避免只查询被停掉的 RPC 节点 |
| A8 | A/B 各 3 次运行，配置配对，指标完整 | 配置、原始 CSV/JSON、图表、统计脚本 |
| A9 | 新运行目录按 README 完成短演示 | 完整命令与验证记录 |

节点故障实验使用独立实验运行，不能混入正常性能主对照。停节点展示的是可用性门槛，不等同于验证所有拜占庭攻击；2-2 与 3-1 网络分区可以先以 CAP 和 QBFT 假设分析，若另做网络分区实验应单独报告。

**预期目录与交付物**

```text
blockfl-audit/
  README.md
  THIRD_PARTY_NOTICES.md
  requirements.lock / package-lock.json
  configs/                 # 训练、实验、网络与版本配置
  network/                 # Besu genesis、节点配置、启动停止脚本
  contracts/               # 审计合约、编译部署与测试
  fl/                      # 客户端、服务器、CNN、数据划分
  audit/                   # 序列化、哈希、交易与回执校验
  dashboard/               # Streamlit 演示界面
  experiments/             # A/B、故障实验、指标采集、画图
  tests/                   # 合约和关键接入验证
  results/<run-id>/        # 配置、模型、CSV、事件、回执、日志
  report/                  # 英文报告初稿与图片
```

结果与仓库应保留上游版本/commit、许可证和修改说明。模型性能不预设提升：上链主要增加审计能力；准确率变化必须由实验解释。所有图表应能追溯到原始结果文件。

**关键官方依据**

- [Flower PyTorch Quickstart](https://flower.ai/docs/framework/tutorial-quickstart-pytorch.html)：CIFAR-10、CNN、FedAvg 与 ClientApp/ServerApp。
- [Flower Deployment Runtime](https://flower.ai/docs/framework/how-to-run-flower-with-deployment-engine.html)：多个客户端进程的部署路径。
- [Besu Developer Quickstart](https://docs.besu-eth.org/private-networks/tutorials/quickstart/)：四验证节点的 Docker Compose 开发网络。
- [Besu QBFT tutorial](https://docs.besu-eth.org/private-networks/tutorials/qbft)：四验证节点配置与启动。
- [Besu binary installation](https://docs.besu-eth.org/private-networks/get-started/install/binary-distribution)：当前 JDK 要求及二进制安装。
- [OpenZeppelin Access Control](https://docs.openzeppelin.com/contracts/5.x/access-control)：角色权限组件。
- [Streamlit 文档](https://docs.streamlit.io/)：Python 数据展示应用。
- [R3T 依赖](https://github.com/linhnt31/R3T/blob/master/requirements.txt)、[客户端](https://github.com/linhnt31/R3T/blob/master/Client/client.py)、[合约](https://github.com/linhnt31/R3T/blob/master/Blockchain/contracts/Federation.sol)：集成参考及与本任务的差异依据。

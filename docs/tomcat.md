# Tomcat 的完整关系比较

这部分对应论文 RQ3。输入是公开的 Tomcat DOOP 事实，求解采用 Soufflé 2.5 的同一组规则，分别使用 1 个和 4 个 OpenMP 线程。两次运行都导出 71 个关系，包含 11,618,755 条元组，每份输出为 2,076,206,212 字节。比较以每个关系的完整内容为准。

## 文件

- `experiments/tomcat/inputs/tomcat.zip` 是公开输入归档，共 36,750,964 字节。
- `experiments/tomcat/inputs/source.json` 记录输入和规则的来源、版本及 SHA-256。
- `experiments/tomcat/rules/doop_original.dl` 是上游规则，`doop_all_relations.dl` 为同一组规则补上全部输出声明。
- `experiments/tomcat/rules/souffle_phase_driver.cpp` 使用 Soufflé 的 `loadAll`、`run`、`printAll` 接口，分别记录加载、计算和输出时间。
- `experiments/tomcat/results/` 保存两次运行的阶段数据、执行记录、逐关系数量与哈希，以及完整内容的比较结果。
- 两份完整结果放在 GitHub Release 中，每份压缩后约 137 MiB。下载清单为 `experiments/tomcat/assets.json`。

## 检查已有结果

在 artifact 根目录运行下面的命令。

```sh
python scripts/tomcat.py summary
python scripts/tomcat.py fetch
python scripts/tomcat.py verify --output runs/tomcat/archive_verification.json
```

`fetch` 通过公开 HTTPS 链接下载 [artifact-20261113 Release](https://github.com/cuiliaomei-beep/precision-contracts-artifact/releases/tag/artifact-20261113)，不需要 GitHub 账号、token 或 GitHub CLI。下载完成后，脚本核对归档的长度和 SHA-256。已有归档也会重新核对。

`verify` 直接读取两个压缩归档中的全部元组，不需要解压到磁盘。它检查 71 个关系的文件集合、元组个数、列数、排序及重复行，逐关系核对 SHA-256，并比较两份结果的实际内容。

## 重新运行分析

需要 Soufflé 2.5、支持 C++17 和 OpenMP 的 g++、GNU sort 及 timeout。编译阶段还需要 Soufflé 开发头文件，以及 zlib、SQLite 和 ncurses 开发库。在 Linux 或 WSL 中运行即可。Windows 上的脚本会调用默认 WSL 环境。

```sh
python scripts/tomcat.py build
python scripts/tomcat.py run --threads 1 --name tomcat_w1 --binary build/tomcat/doop_phase_driver
python scripts/tomcat.py run --threads 4 --name tomcat_w4 --binary build/tomcat/doop_phase_driver
python scripts/tomcat.py compare runs/tomcat/tomcat_w1/canonical runs/tomcat/tomcat_w4/canonical
```

`build` 先让 Soufflé 生成 C++，再编译计时入口。`run` 检查并解压输入，运行分析，按字节序排序每个完整关系，然后与已有结果逐项比较。每次运行使用新名称，输出写入 `runs/tomcat/`。阶段时间保存在对应运行目录的 `phases.json`。若只需要直接运行解释器，可省略 `--binary` 参数。

两次运行的原始输出和排序结果约占 8.3 GB，另需输入和排序空间。已有论文测量中的加载、计算、输出时长受同机其他任务影响。这里的核心复现标准是同一事实与规则下的完整关系相等。输入来自公开事实归档，原始 Tomcat 应用到事实的生成步骤没有在此重建。

## 来源与许可

输入来自 Hugging Face 的 `NemoYuu/flowlog_benchmark`，版本为 `da9e91b3ff75d94604f57ba2b21ef3aa97e241ec`，数据集卡标注 Apache-2.0。许可文本保存在 `experiments/tomcat/licenses/DATASET-APACHE-2.0.txt`。

规则来自 `flowlog-rs/flowlog-bench` 的 `ccf29c3ff828b1286be25481e0547b896be8c733`，原路径为 `programs/oracle/souffle/doop.dl`。所取得的上游目录中没有许可文件，规则文件也没有许可声明，因此这里仅记录其来源，不替上游指定许可。Soufflé 的许可与版本信息应以安装来源为准。

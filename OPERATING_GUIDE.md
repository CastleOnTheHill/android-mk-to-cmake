# 操作指导

本文说明如何运行 `android-mk-to-cmake`、查看监控结果、验证 libcurl 转换，以及确认 opencode 异常时系统仍能继续工作。

## 1. 工作模式

当前仓库有两条可用路径：

- 主流程：`scripts/run_all.py`，优先使用 LangGraph；如果 LangGraph 不可用，会回退到顺序执行同一批 stage 函数。
- 轻量流程：`lite_dag/run.py`，只使用 Python 标准库，实现 DAG Executor、`state/graph_run.json` 和静态 `state/dashboard.html`。

主流程适合 Android.mk / package.mk 项目迁移。轻量流程适合验证 Makefile.am / Makefile.inc 到 CMake 的确定性转换，当前已用 curl/libcurl 做回归验证。

## 2. 环境准备

基础脚本和轻量 DAG 只要求 Python 3.10+：

```sh
python3 --version
```

LangGraph Studio 需要 Python 3.11+ 和可安装依赖：

```sh
cd android-mk-to-cmake
python3.11 -m venv .venv
. .venv/bin/activate
pip install -e .
```

如果使用 `uv`：

```sh
cd android-mk-to-cmake
uv sync
```

## 3. 运行主转换流程

在要转换的工程根目录运行：

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config
```

如果工程还没有可配置的根 `CMakeLists.txt`，先跳过 CMake 检查：

```sh
python3 android-mk-to-cmake/scripts/run_all.py --root . --config-dir config --skip-check
```

常用参数：

- `--root`：被转换工程根目录。
- `--config-dir`：Kconfig `.config` 产品配置目录。
- `--scan-dir`：扫描 mk 文件的子目录，默认是 `.`。
- `--state-dir`：输出状态目录，默认是 `state`。
- `--force`：忽略 manifest 复用，强制重新执行。
- `--ai-provider skipped`：禁用 AI fallback，但仍生成显式 skipped 结果。
- `--opencode-command` / `--opencode-model` / `--opencode-agent`：配置 opencode 调用。

主要输出：

- `state/files/*.ir.json`：解析后的中间表示。
- `state/unknown/*.unknown.json`：脚本无法确定转换的小片段。
- `state/model_results/*.model.json`：AI fallback 的结构化结果。
- `state/generated/**/CMakeLists.txt`：生成的 CMake。
- `state/report.md`：迁移报告。
- `state/graph_run.json`：阶段执行结果。

## 4. 启动 LangGraph 监控

```sh
cd android-mk-to-cmake
. .venv/bin/activate
langgraph dev
```

打开命令输出中的 Studio URL。图中的节点含义：

- `parse_kconfig`：读取产品 `.config`，生成配置符号矩阵。
- `scan_mk`：扫描 Android.mk / package.mk / Makefile。
- `parse_include_graph`：解析 include 关系。
- `parse_mk`：把 mk 文件转换成 IR，并提取 unknown 片段。
- `ai_unknown`：只处理 unknown 片段的 AI 节点。
- `convert_ir`：用确定性脚本生成 CMake，并只合并通过校验的 AI 片段。
- `record_graph_run`：记录图执行状态。
- `render_report`：生成 Markdown 报告。
- `check_cmake`：可选 CMake 配置检查。

其中 `ai_unknown` 是 AI 节点，其余节点都是 Python 脚本节点。

## 5. 运行轻量 DAG

轻量 DAG 不依赖 LangGraph 或 LangChain：

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /path/to/curl \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

输出：

- `/tmp/curl-lite-state/graph_run.json`：DAG 每个节点的状态、耗时和结果。
- `/tmp/curl-lite-state/dashboard.html`：静态监控页面。
- `/tmp/curl-lite-state/generated/`：转换出的 CMake。
- `/tmp/curl-lite-state/comparison.json`：和已有 CMake 的 target source 对比。
- `/tmp/curl-lite-state/config_switches.json`：配置开关覆盖检查。
- `/tmp/curl-lite-state/cmake-check.log`：生成 CMake 的 configure 日志。

打开监控页面：

```sh
xdg-open /tmp/curl-lite-state/dashboard.html
```

如果没有桌面环境，可以直接用浏览器打开这个文件路径。

## 6. libcurl 验证方法

先准备 curl 源码：

```sh
git clone --depth 1 https://github.com/curl/curl.git /tmp/curl-src
```

执行轻量 DAG：

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /tmp/curl-src \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

重点看 dashboard 的两张表：

- `curl Makefile vs Existing CMake Comparison`：确认 `libcurl`、`libcurlu`、`curl`、`curlinfo`、`libcurltool` 的 source 列表是否 match。
- `Configuration Switch Coverage`：确认 Automake 条件开关是否能匹配到已有 curl CMake 符号，以及生成 CMake 是否保留兼容开关。

当前 curl 验证期望：

- target source 对比：5/5 match。
- `lib/src` Makefile 实际使用开关：19 个。
- 生成 CMake 覆盖：19/19。
- curl 现有 CMake 可匹配：25/26。
- 唯一未匹配项：`CURL_LT_SHLIB_USE_MIMPURE_TEXT`，它是 libtool 的 `mimpure-text` 链接标志类开关，curl 现有 CMake 没有明显等价建模。

## 7. opencode 异常行为

AI fallback 不是主调度器，只负责 unknown 小片段。系统会对 opencode 输出做 schema 校验和规范化：

- opencode 报错：记录为 `failed`，继续后续转换。
- opencode 超时：记录为 `failed`，继续后续转换。
- opencode 输出非法结果：记录为 `failed`，清空 `cmake_fragment`。

`convert_ir` 只合并满足以下条件的 AI 结果：

- `status == "converted"`
- `cmake_fragment` 非空
- 通过 schema 校验
- 不包含 Markdown fence

因此坏的 AI 输出不会进入最终 CMake。

## 8. 回归测试

提交前建议跑：

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
python3 -m py_compile android-mk-to-cmake/scripts/*.py android-mk-to-cmake/tests/*.py android-mk-to-cmake/lite_dag/run.py
git -C android-mk-to-cmake diff --check
```

opencode 故障模拟由 `tests/test_pipeline.py` 覆盖，包括报错、超时和非法输出三种情况。

## 9. 常见问题

如果 `langgraph dev` 报 checkpointer 或 API 兼容错误，优先确认当前代码使用的是 `graph.compile()`，不要额外传入内存 checkpointer。

如果轻量 DAG 的 CMake 检查失败，先看：

```sh
cat /tmp/curl-lite-state/cmake-check.log
```

如果开关覆盖显示 `out_of_scope`，表示该 `AM_CONDITIONAL` 没在当前 `--focus` 的 Makefile 范围内使用，不代表主工程没有对应 CMake 表达。

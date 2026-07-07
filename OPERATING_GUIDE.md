# 操作指导

本文只描述当前保留的简易实现：`lite_dag/run.py`。旧的 LangGraph / LangChain / opencode 调度路径已经移除。

## 1. 当前架构

`lite_dag/run.py` 使用 Python 标准库实现一个小型 DAG Executor。它把确定性任务拆成固定节点并按依赖顺序执行：

- `discover`：发现指定 `--focus` 目录中的 `Makefile.am` 和 `CMakeLists.txt`。
- `parse_configs`：解析指定的 `.config`、`.euap_config` 或其他 config 文件。
- `parse_project_variables`：从指定的公共 mk 文件中预加载变量，例如 `TOPDIR`。
- `parse_makefiles`：解析 Automake 赋值、include、条件块和 target，用 config/预加载变量解析 mk include 依赖，并识别 `target_definition`、`judgment_package`、`variable_fragment`。
- `convert_makefiles`：生成中间 CMake，并生成 `MakefileSwitches.cmake` 兼容开关层；target 定义型 mk 中的条件追加会按 target 归属转换成 `target_sources()`、`target_include_directories()`、`target_compile_definitions()`、`target_compile_options()` 等命令，脚本不能转换的 unknown 片段会按原解析顺序写成 TODO 注释。
- `extract_existing_cmake`：读取已有 CMake target 信息。
- `compare_with_existing`：对比生成 target source 和既有 CMake baseline。
- `analyze_config_switches`：检查 Automake 条件开关是否映射到已有 CMake 和生成 CMake。
- `check_generated_cmake`：运行 `cmake -S generated -B cmake-check`。
- `render_dashboard`：生成静态 HTML 监控页面。

所有节点结果写入 `state/graph_run.json`，不依赖任何 AI 或外部调度框架。

## 2. 环境要求

只需要 Python 3.10+。如果要验证生成的 CMake，还需要本机安装 `cmake`。

```sh
python3 --version
cmake --version
```

## 3. 基本运行

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /path/to/project \
  --state-dir /tmp/mk2cmake-state \
  --focus lib --focus src \
  --config-file .config \
  --config-file .euap_config \
  --var-file build/top.mk \
  --force
```

参数说明：

- `--root`：被分析的源码根目录。
- `--state-dir`：输出目录。相对路径会放到 `--root` 下；绝对路径会直接使用。
- `--focus`：要转换的子目录，可重复传入；默认是 `lib` 和 `src`。
- `--config-file` / `--config`：要读取的 config 文件，可重复传入。相对路径按 `--root` 解析；不传时会自动读取根目录下存在的 `.config` 和 `.euap_config`。
- `--var-file` / `--preload-mk`：要预加载变量的公共 mk 文件，可重复传入。例如包含 `TOPDIR := ...` 的文件。
- `--makefile-name`：在每个 `--focus` 目录下要发现的 makefile 文件名，可重复传入；默认是 `Makefile.am`。
- `--makefile`：额外指定要解析的 makefile 路径，可重复传入。
- `--force`：当前保留参数，便于后续扩展强制重跑语义。

## 4. 输出文件

运行完成后重点查看：

- `graph_run.json`：每个 DAG 节点的状态、耗时、依赖和摘要。
- `dashboard.html`：静态监控页面。
- `dot_config.json`：多个 config 文件的解析结果。后传入的 config 对同名变量覆盖前面的值。
- `project_variables.json`：默认变量、config 变量和 `--var-file` 中读取到的公共 mk 变量。
- `make_ir.json`：解析后的 Makefile IR。每个入口包含 `package_kind`，每个被 include 的文件包含 `file_roles`，每个 target 包含按 mk 顺序记录的 `operations`。
- `mk_dependencies.json`：解析出的 mk include 依赖边，包含 `from`、`to`、行号和原始 include 表达式。
- `generated/`：脚本转换生成的中间 CMake 工程。每个子目录的 `CMakeLists.txt` 会保留可转换 target，并把动态 include、自定义 rule 等 unknown 片段按原顺序写成注释，供后续 AI 或人工继续处理。
- `generated/MakefileSwitches.cmake`：Automake 条件开关到 CMake 兼容选项的声明。
- `generated_manifest.json`：生成 target 和 source 列表。
- `comparison.json`：生成 target source 与既有 CMake baseline 的对比。
- `config_switches.json`：配置开关覆盖报告。
- `cmake-check.log`：生成 CMake 的 configure 日志。

打开监控页面：

```sh
xdg-open /tmp/mk2cmake-state/dashboard.html
```

没有桌面环境时，可以把 `dashboard.html` 路径复制到浏览器打开。

## 5. libcurl 验证

准备 curl 源码：

```sh
git clone --depth 1 https://github.com/curl/curl.git /tmp/curl-src
```

运行转换和对比：

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /tmp/curl-src \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

查看：

```text
/tmp/curl-lite-state/dashboard.html
```

当前验证目标：

- `lib:libcurl` source list match
- `lib:libcurlu` source list match
- `src:curl` source list match
- `src:curlinfo` source list match
- `src:libcurltool` source list match
- `lib/src` Makefile 实际使用的 19 个条件开关都在生成 CMake 中有兼容声明
- curl 既有 CMake 对 26 个 Autotools 条件中的 25 个有可识别映射

已知差异：

- `CURL_LT_SHLIB_USE_MIMPURE_TEXT` 是 libtool 的 `mimpure-text` 链接标志类开关，curl 既有 CMake 没有明显等价建模。

## 6. 回归测试

```sh
python3 -B -m unittest discover -s android-mk-to-cmake/tests -v
python3 -m py_compile android-mk-to-cmake/lite_dag/run.py android-mk-to-cmake/tests/*.py
git -C android-mk-to-cmake diff --check
```

如果本地已有 `/tmp/curl-src`，建议提交前也跑一次 libcurl 验证：

```sh
python3 android-mk-to-cmake/lite_dag/run.py \
  --root /tmp/curl-src \
  --state-dir /tmp/curl-lite-state \
  --focus lib --focus src \
  --force
```

## 7. 不再保留的内容

以下旧路径已经删除：

- LangGraph 图入口和 `langgraph.json`
- LangChain / LangGraph 依赖和锁文件
- opencode AI fallback 调度
- Android.mk 专用旧脚本管线

后续开发应围绕 `lite_dag/run.py` 增量扩展，不再恢复重型调度框架。

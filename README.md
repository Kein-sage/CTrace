# CTrace

CTrace 是一个面向 C 语言初学者的诊断与可视化学习项目。目前通过 Python 调用 Clang 检查单个 C 文件，将诊断整理为 JSON，并为部分常见问题提供中文解释。

## 当前状态

[v0.1.0 — 首个命令行版本](https://github.com/Kein-sage/CTrace/releases/tag/v0.1.0) 已发布。该版本提供以下功能：

- 调用 Clang 进行语法和语义检查，不运行被检查的 C 程序。
- 提取诊断的文件、行号、列号、严重程度和原始信息。
- 为首批五类问题提供中文解释：缺少分号、未声明标识符、缺少右花括号、字符串引号未闭合、函数调用参数不足。
- 保留 Clang 原始诊断输出，未匹配到解释时返回 null。
- 使用统一 JSON 报告参数错误、文件不存在、Clang 不可用、启动失败和超时。
- 编译前检查主源码文件大小，上限为 64 KiB；限制编译器标准输出和标准错误输出的合计大小为 1 MiB。
- 当前已有 21 个自动化测试，并在开发者的 WSL 环境中通过。

当前开发分支扩展了变量声明和普通表达式缺少分号的中文解释，并新增函数调用参数过多、头文件不存在和调用未声明函数时的中文解释。这些改动尚未发布，v0.1.0 的已发布内容保持不变。

这里的“五类”表示首批样例的覆盖范围，不代表支持这些类别下的所有诊断形式。v0.1.0 的参数数量规则只处理“参数太少”；当前开发分支新增了本轮样例所涉及的“参数太多”诊断解释，仍不代表覆盖所有参数数量错误形式。

## 环境要求

- Python 3.12（当前验证版本）。
- Clang，且终端能够通过 clang 命令找到它。
- Python 部分仅使用标准库，无第三方 Python 依赖。

当前开发环境为 WSL Ubuntu 24.04，历史工具记录为 Python 3.12.3、Clang 18.1.3。其他系统或版本尚未完整验证，Clang 诊断措辞的变化可能影响解释匹配和测试。

可通过以下只读命令查看环境：

~~~bash
python3 --version
clang --version
~~~

## 快速开始

第一次下载项目：

~~~bash
git clone https://github.com/Kein-sage/CTrace.git
cd CTrace
~~~

如果本机已经有项目，请直接进入已有目录，不必再次克隆。例如：

~~~bash
cd ~/projects/CTrace
~~~

检查正确样例：

~~~bash
python3 backend/diagnose.py examples/correct.c
~~~

检查缺少分号的样例：

~~~bash
python3 backend/diagnose.py examples/missing_semicolon.c
~~~

命令格式：

~~~bash
python3 backend/diagnose.py <C文件路径>
~~~

请将 <C文件路径> 替换成真实路径，不要原样输入尖括号。路径包含空格时，应使用英文引号包住整个路径。

## 教学样例

除 correct.c 外，以下文件故意保留编译错误，用来演示诊断并进行回归测试。

| 文件 | 内容 |
|---|---|
| examples/correct.c | 正确代码 |
| examples/missing_semicolon.c | return 语句缺少分号 |
| examples/undeclared_variable.c | 使用未声明的标识符 |
| examples/missing_brace.c | 缺少右花括号，并产生配对位置的 note |
| examples/unterminated_string.c | 字符串缺少结束引号，并产生相关警告和错误 |
| examples/wrong_argument_count.c | 函数需要两个参数，调用时只提供一个 |
| examples/missing_declaration_semicolon.c | 变量声明末尾缺少分号 |
| examples/missing_expression_semicolon.c | 表达式语句末尾缺少分号 |
| examples/too_many_arguments.c | 函数需要两个参数，调用时提供三个 |
| examples/missing_header.c | 引用了不存在的头文件 |
| examples/undeclared_function.c | 调用尚未声明或定义的函数 |

一个源码问题可能产生多条诊断。note 是补充说明，不应把每条诊断都当作独立错误。

## JSON 输出

每次受支持的检查或已处理的工具异常都会输出一个 JSON 对象。

| 字段 | 含义 |
|---|---|
| file | 输入文件的绝对路径；未提供有效命令参数时为 null |
| success | Clang 返回 0 时为 true，否则为 false；工具异常时也为 false |
| diagnostics | 已解析的源码诊断列表；没有诊断或发生工具异常时为空列表 |
| compiler_output | 正常完成编译器调用时保留的标准错误输出；当前工具异常报告中为空字符串 |
| error | 工具异常的 code 和 message；检查流程正常完成时为 null |

每条 diagnostics 记录包含：

| 字段 | 含义 |
|---|---|
| file | 诊断指向的文件 |
| line / column | Clang 给出的行号和列号，均从 1 开始 |
| severity | fatal error、error、warning 或 note |
| message | 原始诊断文字，保留警告选项后缀 |
| explanation | 匹配到的中文解释；没有对应规则时为 null |

success 为 false、error 为 null 可以是预期的源码编译失败。此时应查看 diagnostics 和 compiler_output。

查找中文解释时会忽略末尾的 Clang 警告选项，例如 [-Winvalid-pp-token]；输出的 message 和 compiler_output 仍保留原始信息。未被解析的诊断文字也仍可从 compiler_output 查看。

输出按 UTF-8 解码；遇到非法字节时使用替换字符，避免因解码失败而中断 JSON 报告。

## 输入与输出限制

| 项目 | 当前限制 |
|---|---|
| 主源码文件 | 最大 65,536 字节（64 KiB），超过时不启动编译器 |
| 编译器输出 | 标准输出和标准错误输出合计最大 1,048,576 字节（1 MiB） |
| 编译器调用 | 5 秒超时；终止和清理进程可能需要额外时间 |

输出收集过程中会持续检查字节数，超限时停止编译器并报告 COMPILER_OUTPUT_TOO_LARGE。不会先无限收集，再截短结果。刚好等于上限时允许继续。

1 MiB 是编译器两路输出的合计字节上限，不是整个 Python 进程的内存上限，也不是最终 JSON 文本大小上限。主源码文件大小检查不包含被引用头文件的总大小。

## 退出码与工具错误

| 退出码 | 含义 |
|---|---|
| 0 | Clang 检查通过；仍可能包含警告 |
| 1 | Clang 检查未通过，需查看诊断和原始输出 |
| 2 | 命令使用错误或已处理的工具运行异常 |

在 Bash 中，紧接着运行下面的命令可以查看上一条命令的退出码：

~~~bash
echo $?
~~~

当前工具错误码：

| 错误码 | 含义 |
|---|---|
| INVALID_ARGUMENTS | 输入参数数量不符合要求 |
| FILE_NOT_FOUND | 输入文件不存在或不是普通文件 |
| FILE_ACCESS_FAILED | 无法读取输入文件信息 |
| SOURCE_TOO_LARGE | 主源码文件超过 64 KiB |
| COMPILER_NOT_FOUND | 找不到 Clang |
| COMPILER_TIMEOUT | 编译检查超过 5 秒 |
| COMPILER_START_FAILED | 编译器启动或输出读取的其他系统异常，例如缺少执行权限 |
| COMPILER_OUTPUT_TOO_LARGE | 编译器两路输出的合计大小超过 1 MiB |

这些是目前显式处理的情况，并不代表所有系统异常都已覆盖。

## 运行测试

在项目根目录执行：

~~~bash
python3 -m unittest discover -s backend -p 'test_*.py' -v
~~~

当前开发分支共 25 个测试，包含声明和表达式缺少分号的两个子用例，以及参数过多、头文件缺失和调用未声明函数测试。已发布的 v0.1.0 包含 21 个测试。当前开发分支的测试覆盖：

- 正确代码与五类故意出错的样例。
- 缺少命令参数、输入文件不存在。
- 模拟 Clang 找不到、启动失败和超时。
- 主源码文件刚好达到和超过大小上限。
- 输出边界、两路输出的合计大小、输出分离、非法 UTF-8、真实子进程超时及输出超限的 JSON 报告。

样例测试使用真实 Clang。三个编译器异常测试使用 unittest.mock 模拟；输出收集测试会启动短小的 Python 子进程，并用较小的测试阈值验证边界及终止行为。增加测试后，总数量可能变化；以实际测试结果为准。

仓库已配置 GitHub Actions：推送到 main 或向 main 提交拉取请求时，会在 Ubuntu 24.04、Python 3.12、Clang 18 环境运行测试。每次上传后，可在仓库的 Actions 页面查看结果。

## 当前限制与安全边界

- 当前提供本地命令行原型，尚无 Web API、网页界面或执行过程可视化。
- 主要面向单个小型 C 教学文件，尚未提供大型多文件工程、用户自定义编译选项或完整编译数据库支持。
- 中文解释通过有限规则匹配，不能保证覆盖所有 Clang 诊断。
- 当前使用 Clang 的 -fsyntax-only 检查，不生成或运行被检查程序。
- 编译器本身仍需要解析源码并可能读取头文件。当前没有针对恶意源码的编译隔离、编译器内存限制或文件系统访问隔离。
- 目前应仅用于本地可信教学样例。源码大小检查、输出收集限制、超时和“不运行 C 程序”不能替代安全沙箱，不应直接作为面向公网的不可信代码检查服务。

## 后续路线

- v0.1：完善本地诊断工具、错误处理、测试、输入与资源限制、使用文档。
- v0.2：在完成隔离设计与验证后，检测部分运行时错误。
- v0.3：为明确支持的程序展示执行事件和时间线。

## 许可证

本项目采用 [MIT 许可证](LICENSE)。

Copyright (c) 2026 Kein-sage

## 仓库

https://github.com/Kein-sage/CTrace

# 固定可信资源探针

此开发工具用于验证 systemd 用户临时任务的资源控制和回收，不是运行 C 程序的沙箱。
依赖非 root Linux、正在运行的 systemd 用户管理器，以及已委派的 memory / pids 控制器。
不自动安装组件，不创建持久服务配置，不修改网络或代理。

## 使用方式与范围

在项目根目录查看执行计划：

~~~sh
python3 backend/runtime_resource_probe.py
python3 backend/runtime_resource_probe.py --plan memory
~~~

默认只输出 JSON 计划，不启动外部程序。case 只允许 snapshot、pids、memory、timeout、descendants；不接受源码、任意命令、任意 unit 名或自定义资源参数。

实际运行示例：

~~~sh
python3 backend/runtime_resource_probe.py --run snapshot
~~~

--run 会创建临时 systemd 用户任务及其 cgroup，运行项目内固定可信 Python 探针，随后停止、reset-failed 并核对回收。
这会改变项目目录外的临时运行状态，只有在明确允许此范围时才能执行；不应把它当作只读检查。
服务管理器也可能产生正常的状态日志。无需 root，不安装系统软件或写入持久配置。

每次只运行一个探针，固定上限为 64 MiB 内存、零交换空间、16 个进程/线程、5 秒运行时间。
停止阶段最多等待 2 秒后发送 SIGKILL，控制端另外设置超时，因此 5 秒不是整个命令返回时间的上限。
所有压力操作前，worker 先验证非 root 身份、所在 unit 和 cgroup 读回限额；任何不一致均拒绝压力操作。

| case | 固定负载 | 通过所需的证据 |
|---|---|---|
| snapshot | 读取自身限额后退出 | 限额与预设一致，临时 unit 和 cgroup 已回收 |
| pids | 最多尝试创建 20 个短时线程 | 创建触限、pids.events 的 max 计数增加，当前任务数不超过 16 |
| memory | 单次尝试分配 80 MiB | 先确认 64 MiB 限额，再观察 systemd 的 Result=oom-kill；非零退出或信号本身不足为据 |
| timeout | 固定等待 6 秒 | 5 秒运行预算触发 Result=timeout；控制端超时不算通过 |
| descendants | 父进程返回，留下一个忽略 SIGTERM、最多等待 6 秒的可信子进程 | 核对 PID 与启动时间，确认该进程身份消失，并完成整个 unit / cgroup 回收 |

所有情况均先停止本次生成的 unit，再清理其失败状态；不按模糊前缀停止其他任务。
失败任务在获取 Result 前保留状态，以区分 OOM、超时和普通退出；获取证据后再回收。
未取得 worker 的 cgroup 信息、无法读取管理器状态或未确认清理时均不报告通过。

## 输出和退出码

计划输出 kind=trusted_resource_probe_plan、executes=false，退出码为 0。
实际探针报告 kind=trusted_resource_probe，包含 case、passed、runtime_ready、unit、client_exit_code、frames、unit_result、cleanup_confirmed、errors 及原始 stdout/stderr。
探针验收通过退出 0；用法错误、不支持的环境或验收失败退出 2。

memory、timeout 的 client_exit_code 非零是预期情况；是否通过必须结合资源管理器证据和回收结果。
descendants 的主进程返回 0，但停止阶段可能因忽略 SIGTERM 的子进程而得到 Result=timeout、client_exit_code=1；只有子进程身份消失且清理完成才通过。

runtime_ready 始终为 false。该字段不能被一次探针通过改变，也不能作为启用受检代码执行的依据。
当前输出采集只面向上述固定、输出有限的可信 worker；不能复用于任意程序，未来运行时入口仍须单独实现有界输出采集。

## 已验收范围

2026-11-10，在 WSL Ubuntu 24.04 / Python 3.12.3 / systemd 255.4 环境实测：

- systemd 用户管理器处于 active 状态，已委派 cpu、memory、pids；本次没有创建或修改该委派配置。
- 五项固定探针全部通过。pids 创建了 15 个额外线程，当前任务数为 16，max 事件增加 1。
- memory 的管理器结果为 oom-kill；timeout 为 timeout。
- descendants 留下的忽略 SIGTERM 子进程已被回收；五项任务的 unit 与 cgroup 均已消失。
- 完整 unittest 回归 85 项全部通过，无跳过。自动测试使用模拟，不要求 CI 存在 systemd 用户管理器，也不自动运行压力探针。

原环境清单检查的是调用进程本身。在普通 WSL 终端的 /non-systemd 下显示 LIMITS_UNAVAILABLE，
与用户管理器能创建其他受限任务并不矛盾；清单仍保持只读，不自动创建服务来寻找可用能力。

## 尚未完成

该原型未提供文件系统隔离、沙箱网络隔离、环境和文件描述符清理、工具链挂载、8 MiB 总工作区和 inode 配额，未编译或运行受检 C 代码。
此次 64 MiB 探针也不代表设计中的 512 MiB 编译和 128 MiB 运行预算已经验收。
不支持任意代码在沙箱外运行，不以这些资源探针替代 bubblewrap 或完整隔离验收。
下一步应在项目内评估可信 bubblewrap 组件来源和私有工作区配额，再组合隔离与资源管理。

参见 [v0.2 最小设计](runtime-v0.2-plan.md)、[环境核查](runtime-v0.2-environment.md)。
属性依据：[systemd 255 资源控制](https://www.freedesktop.org/software/systemd/man/255/systemd.resource-control.html)、[systemd 服务超时与清理](https://www.freedesktop.org/software/systemd/man/255/systemd.service.html)。

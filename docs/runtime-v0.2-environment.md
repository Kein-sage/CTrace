# v0.2 环境核查与隔离方案

核查日期：2026-11-10。对应 [最小运行时检测设计](runtime-v0.2-plan.md) 的第一阶段。
当前状态：环境清单已实现，隔离后端未实现，运行时检测不可用。

## 可复现的只读检查

在项目根目录运行：

```sh
python3 backend/runtime_preflight.py
```

命令不接收源码，不启动外部命令、不编译或运行 C 程序、不写文件，输出 JSON。
它仅观察系统、执行身份、工具路径和 Linux cgroup 元数据，不启动工具或验证实际约束。
通过 /proc/self/cgroup 和 /proc/self/mountinfo 定位当前子树，新增 observations.current_cgroup：

- path / mount_point / mount_read_only：当前 cgroup 目录及挂载观察；路径含义不明确时报告 error，不猜测。
- controllers / subtree_control：当前子树可见和已启用的控制器，保留空值与读取错误。
- directory_writable / control_writable：目录及 cgroup.procs、cgroup.threads、cgroup.subtree_control 的有效身份权限观察，不做写入探针。
- limits：memory.max、memory.swap.max、pids.max 的原文；max 不等于 CTrace 已设限，读取失败也不等于无限制。

根目录控制器继续展示，阻塞判断改用当前子树，避免根目录可见但当前不可用时漏报。
无法定位当前 cgroup、控制器不足、只读挂载或缺少候选管理权限时附加 LIMITS_UNAVAILABLE。
重复挂载等歧义视图当前保守地报告未知；没有扫描其他子树或查找宿主服务。
即使所有权限观察均为 true，仍未验证创建子组、进程迁移、无内部进程约束、整任务清理或工作区配额。
其独立清单字段为 kind=runtime_environment_inventory、schema_version=1、observations、blockers 和 ready。
这不是运行时检测报告，不能用它替代设计文档中的源码检查结果。

当前始终返回 ready=false、退出码 2，表示后端尚未通过验收。
即使所有工具均存在，仍保留 ISOLATION_NOT_VERIFIED 和 LIMITS_NOT_VERIFIED；不提供强制启用或绕过检查选项。
缺少工具和控制器时附加具体阻塞项。元数据不可读时记录错误类型，不能将其解释为控制器一定不存在。
工具路径只用于展示，绝不作为已验证或可信可执行文件的证明。

## 验证记录

当前资源核查在相同环境运行完整回归：72 项通过，无跳过。新增挂载映射、当前子树权限、读取失败、路径越界和保守阻塞的回归。
实际 CLI 定位到 /sys/fs/cgroup/non-systemd，报告目录与管理接口不可写；当前 memory.max、memory.swap.max、pids.max 均为 max，附加 LIMITS_UNAVAILABLE。

在 Ubuntu 24.04 / Python 3.12.3 / Clang 18.1.3 下运行完整 unittest 回归：59 项通过，无跳过（原有 50 项、新增 9 项）。
实际清单命令输出有效 JSON，ready=false、退出码 2；git diff --check 通过。
测试临时文件限定项目 tmp。未修改现有诊断源码或 CI 配置。

## 本机实测

正式仓库为 WSL Ubuntu-24.04 的 /home/user8260/projects/CTrace；Windows 副本不作为源码覆盖来源。
PR #10 已合并，合并提交 646b4854d103a1c68342273513b8359c7b8b5bbf，原头提交 e60a75d 的 CI 通过。
核查开始时本地 docs/runtime-v0.2-plan 位于 1466e0e，工作区干净；本地 main 和 origin/main 尚在 f12d06c。
未通过修改代理解决远端同步，也未执行网络 fetch。

| 项目 | 观察结果 | 能说明什么 |
|---|---|---|
| 执行身份 | uid=1000，非 root | 满足执行身份前提 |
| Clang | /usr/bin/clang，18.1.3 | 工具存在；未验证隔离编译和 UBSan 链接 |
| unshare / prlimit / timeout | /usr/bin 下存在 | 不能单独构成完整隔离 |
| bwrap / Docker / Podman | PATH 中未找到 | 当前候选隔离组件缺失 |
| systemd-run | /usr/bin/systemd-run 存在 | 未启动服务或验证用户管理器及委派 |
| 用户命名空间 | unshare --user --map-root-user -- /usr/bin/true 返回 0 | 只验证临时用户命名空间创建，不验证其他隔离或限额 |
| cgroup v2 根控制器 | 包含 memory、pids | 控制器可见，不代表可使用 |
| 当前进程 cgroup | /non-systemd | 当前路径下可见 cpu、memory、pids；subtree_control 为空 |
| cgroup 写权限观察 | 根目录与 /non-systemd 的 os.access(W_OK) 均为 false | 当前路径缺少写权限；没有尝试创建 cgroup 或设置限额 |

表中版本、命名空间与权限结果来自本轮专用探针，不是清单命令的额外保证。
没有编译或运行受检 C 可执行文件；没有用真实私密数据验证访问边界。

## 方案选择与当前边界

继续以无特权 bubblewrap 和整任务资源管理作为候选，不手写通用容器运行时。
若后续提供项目内组件，必须固定来源、版本和校验值，记录许可及更新方式，拒绝需要 setuid 或系统安装的分发方式。
不在本阶段下载或构建 bubblewrap：仅补齐该二进制仍无法解决资源控制验收。

Linux cgroup v2 的委派需要相应子树的权限；把文件放进项目不会自动取得这类权限。
在“只修改项目目录”的当前范围内，不修改 /sys/fs/cgroup、systemd 单元、内核参数或宿主网络/代理。
当前没有找到并验证可用的委派子树；这不等于断言整台宿主不存在其他可用机制。
若后续选择 cgroup 路线，需要环境提供已有的委派能力，并另行明确允许的管理范围；仅安装 bwrap 不足以启用运行时检测。

固定只读工具链、私有挂载与 PID 命名空间、清理环境变量和文件描述符、禁止提权及沙箱网络隔离仍须共同设计与验收。
项目内磁盘目录本身不提供总空间或 inode 配额；8 MiB 总预算还需要可验证的机制。
不得用 RLIMIT_AS、RLIMIT_NPROC、单文件大小限制或 timeout 替代整任务资源预算及后代清理保证。

## 下一阶段准入条件

在编写会处理受检源码的启动入口前，逐项完成：

- 明确组件来源和维护方式，以及可用的资源控制和工作区配额机制。
- 使用项目内专用标记数据验证文件读写边界，避免暴露宿主项目、用户目录和服务套接字。
- 验证隔离内没有外部通信路径，同时保持宿主网络与代理配置不变。
- 验证编译和运行阶段的内存、进程数、空间、文件数、时间及合计输出限制。
- 验证父进程退出或异常后，整个任务的后代均能终止、回收，且隔离失败时不会启动编译。

完成后才进入隔离内 UBSan 整数除零与正确程序的对照实验。
现有 diagnose.py 保持兼容；其语法检查回归不构成 v0.2 的隔离验收。

依据：[bubblewrap 安全边界](https://github.com/containers/bubblewrap#sandbox-security)、[Linux cgroup v2 委派](https://docs.kernel.org/admin-guide/cgroup-v2.html#delegation)。

挂载字段依据：[Linux proc_pid_mountinfo(5)](https://man7.org/linux/man-pages/man5/proc_pid_mountinfo.5.html)。

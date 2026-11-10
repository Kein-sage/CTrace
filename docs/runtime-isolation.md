# 固定隔离策略与验收

本轮交付：在 WSL Ubuntu 24.04 amd64 / systemd 255 / Clang 18 布局下，完成固定隔离策略及 11 项可信探针验收。
这是一层经本机验证的隔离原型，不是已经开放的 C 代码执行服务；runtime_ready 仍为 false。

## 组成与组件来源

- bubblewrap 0.9.0-1ubuntu0.3：从 Ubuntu 官方包地址下载，按本机 apt 元数据中的固定 SHA-256 验证，再解包到项目 .runtime/bubblewrap-0.9.0-1ubuntu0.3。
- tools/bubblewrap-lock.json：记录包版本、架构、大小、来源和包/二进制摘要。每次实际启动前重新核对二进制摘要、执行权限、非 setuid/setgid 和无文件 capabilities。
- backend/runtime_component.py：只接受固定包摘要，仅向项目内解包；不安装系统软件、不创建 AppArmor 配置、不调整内核或代理。
- 包内版权与许可保留在解包目录的 usr/share/doc/bubblewrap/copyright。bubblewrap 主体为 LGPL-2.0-or-later；依赖宿主现有运行库，不修改这些运行库。
- .runtime/ 被 Git 忽略，不把本机二进制作为源码提交。更换版本、内核或工具链布局后须重新校验和验收，不自动从 PATH 换用其他 bwrap。

如需在另一台符合条件的机器复现，先把锁定 URL 的包下载到项目 tmp，核对锁文件来源；再运行：

~~~sh
python3 backend/runtime_component.py tmp/bubblewrap_0.9.0-1ubuntu0.3_amd64.deb
~~~

命令仅在项目内解包，既不调用 apt install，也不修改系统软件。下载或依赖失败即报告失败，不自动改变网络或代理。

## 固定边界

每个 probe 使用一个随机命名的临时 systemd 用户 unit，保留 64 MiB 内存、零交换空间、16 个任务、5 秒运行预算及额外停止清理时间。
PrivateUsers=yes 提供临时用户/挂载边界；TemporaryFileSystem 只在任务自身命名空间内覆盖项目私有工作区，设为 8 MiB、128 inode、nosuid、nodev。
配额读回不一致时，受检探针启动前即拒绝。

bubblewrap 创建独立 mount、user、PID、network、IPC、UTS、cgroup 命名空间；显式要求用户命名空间，禁用继续创建嵌套用户命名空间。
禁止提权，清空有效/许可/环境 capabilities，使用新会话和父进程死亡清理策略。
--disable-userns 的限制作用在新建的用户命名空间内，不修改宿主的全局 sysctl。

只读挂载限定为固定 Python/Clang/ld 可执行文件、Python 3.12、LLVM 18、GCC 13、系统运行库与标准头文件，以及固定探针和专用测试配置。
不挂载整个项目、宿主用户目录、Windows 盘、.git、/run、/sys 或宿主服务套接字。
根文件系统与 /proc 只读；/proc 属于沙箱 PID 命名空间。常规存储写入仅允许 /work；/tmp 指向同一个 /work，不能绕过配额。
只提供 /dev/null 和必要的标准 I/O，不提供额外可写 /dev/shm。

环境先清空，再固定 PATH、LANG、HOME、TMPDIR、PWD；不传递控制端总线地址、凭据或代理变量。
控制端只给自身保留连接现有 systemd 用户管理器所需的固定运行时路径，这些路径不进入沙箱。
所有非标准文件描述符在启动时关闭，stdin 为 EOF。探针还主动设置可继承的专用标记 FD 和宿主抽象 Unix 套接字，确认沙箱无法取得它们。

网络探针先确认网络命名空间不同、只有 loopback 且没有 IPv4 路由，再检查文档专用 IPv4/IPv6 地址不可达、宿主抽象 Unix 套接字不可连接。
整个过程不改变宿主网卡、路由、DNS、代理或网络开关。

## 使用与报告

只查看计划，不创建任务：

~~~sh
python3 backend/runtime_isolation_probe.py
python3 backend/runtime_isolation_probe.py --plan network
~~~

实际运行固定可信探针，会创建临时用户任务、命名空间和私有挂载：

~~~sh
python3 backend/runtime_isolation_probe.py --run files
~~~

只接受下面 11 个固定 case，不接收源码、任意命令、unit 名、挂载路径或自定义预算。
该工具使用项目内固定 worker，不接受待检查 C 程序；不要把探针报告或 stdout 当作任意代码无法伪造的证明。

| case | 已验证边界 |
|---|---|
| files | 宿主标记、目录和进程不可见；根及固定工具链只读；工作区可写，宿主标记保持不变 |
| network | 独立网络命名空间、无外部接口/路由、IPv4/IPv6 不可达、宿主抽象和文件系统服务套接字不可访问 |
| environment | 精确环境白名单，仅 FD 0/1/2，stdin EOF |
| namespaces | NoNewPrivs、能力清空、命名空间独立，拒绝新的用户和挂载命名空间 |
| space | 写满 8 MiB 后得到 ENOSPC |
| inodes | 128 inode 限额触发 ENOSPC，不能仅靠空文件耗尽无限宿主 inode |
| pids | 沙箱内线程创建受限，控制端读到 cgroup pids.events 增量 |
| output | 超过 64 KiB 合计输出时，有界采集器结束任务；超限字节不再转发 |
| timeout | 运行预算触发 systemd Result=timeout，控制端超时不能替代此证据 |
| memory | 隔离内固定分配触发 systemd Result=oom-kill，非零退出或信号不能单独算通过 |
| descendants | 父进程退出后，隔离 PID 命名空间中的忽略 SIGTERM 子进程随任务被清理 |

报告 kind=trusted_isolation_probe，含 case、passed、runtime_ready、unit、client_exit_code、unit_result、cleanup_confirmed、errors、events。
通过退出 0；用法、组件、启动、证据或清理失败退出 2。默认计划退出 0，executes=false。
实际 probe 不仅检查返回码：必须先取得资源/配额读回和沙箱内基线证据，再结合全部必需检查、管理器结果与回收结果。

沙箱两路合计输出上限为 64 KiB，控制端再设 128 KiB 的内部事件采集上限。超过上限立即进入终止与清理，不先无限收集再截断。
采集器新增可选 stdin/env/cwd 和字节流回调；原 diagnose.py 调用默认参数不变。回调只在字节预算检查之后触发，回调异常也进入原有终止路径。

停止只针对本次生成的 unit；即使 stop 异常，仍尝试 reset-failed 和回收核实。
unit 必须变为 not-found/inactive，cgroup 必须消失；宿主标记不变、宿主工作区仍为空后才删除本次项目内临时目录。
组件缺失、摘要不一致、权限/命名空间/挂载/配额不可用时，不退化为直接运行。

## 本次证据与尚未开放的内容

2026-11-10 的 11 项真实固定组合探针全部通过，所有临时 unit、cgroup、私有挂载和任务目录均清理。原始证据保存在项目 tmp/isolation-acceptance.json。
完整自动回归 101 项通过，无跳过；自动测试不下载组件、不创建 systemd 任务、不运行真实压力探针。

已经验证的是上述固定隔离策略及 64 MiB 可信探针预算。Clang 在策略中以只读文件存在，但尚未编译、链接或运行任何受检 C 源码。
512 MiB 编译、128 MiB 运行及其阶段切换、UBSan 整数除零对照、独立 C 运行入口和最终报告契约仍属于后续工作，不由本次验收推定完成。
现有 diagnose.py 仍是面向可信本地样例的原命令；未自动切换为新的隔离后端。

参见 [v0.2 设计](runtime-v0.2-plan.md)、[资源探针](runtime-resource-probes.md)。
技术依据：[bubblewrap 0.9.0 参数](https://raw.githubusercontent.com/containers/bubblewrap/v0.9.0/bwrap.xml)、[systemd 255 临时文件系统与用户命名空间](https://raw.githubusercontent.com/systemd/systemd/v255/man/systemd.exec.xml)。

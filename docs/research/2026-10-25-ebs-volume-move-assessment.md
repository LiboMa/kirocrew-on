# 评估：KiroCrew ON MVP 0.2.0 集群版：每人一块 EBS 卷，在主机之间 detach / attach

- 日期：2026-10-25
- 用途：负责人据此确定缩容策略（主机空闲多久回收、回收前怎样迁走卷）
- 方法：全部是只读操作。
  - 用 curl 抓取 AWS 官方文档原文
  - AWS Pricing API：`aws pricing get-products`，us-east-1，2026-10-25 查询
  - `aws ec2 describe-instance-types` / `describe-instance-type-offerings` / `describe-availability-zones`
  - `aws service-quotas list-service-quotas` / `get-service-quota`
  - EBS CSI 驱动源码（只读）

  **没有创建或修改任何 AWS 资源。**
- 来源标注：
  - 【文档】AWS 官方文档
  - 【API】Pricing API 或 EC2 / Service Quotas 的只读查询结果
  - 【社区】AWS re:Post 或开源 EBS CSI 驱动的源码与 issue
  - 【项目】本仓库文档
  - 【本机】本机 `du` 实测
  - 【推断】作者的推断，未经实测，需按第 5 节验证

---

## 0. 结论摘要

1. **可行。** 在同一可用区内移动卷不复制数据，耗时与卷的大小无关。gp3 不支持 Multi-Attach，一块卷同一时刻只能挂在一台主机上，所以不会出现两台主机同时写同一个用户数据的情况【文档】。
2. **有三条硬约束：**
   - **(a) 卷必须和主机在同一可用区。** 共享池和专用 Worker 都要放在同一个 AZ。跨 AZ 只能快照再恢复，耗时以分钟计。
   - **(b) 每台主机能挂的卷有上限。** m8g / c8g / r8g（xlarge–12xlarge）是独立上限 32 块，扣掉根卷还能放 **31 个用户**。m7g / c7g / r7g 是共享上限，查询值 27 已扣掉 1 块网卡，再扣根卷剩 **26 个用户**，每多一块网卡再少 1 个。
   - **(c) 主机数有下限。** 如果每个用户的卷始终挂在某台主机上，池子缩不到「用户数 ÷ 31」台以下。1000 人时至少要 33 台。
3. **一次同 AZ 迁移，用户可见约 20–60 s，长尾 1–3 min【推断】。** 时间主要花在停止和启动 Gateway / Worker 上，EBS 本身只占小头。卷卡在 busy / detaching 时，不能在仍在运行的主机上强制 detach，要先隔离主机。
4. **每人存储成本：** gp3 每 GB 每月 $0.08【API】。30 GiB 为 $2.40，50 GiB 为 $4.00，100 GiB 为 $8.00；增量快照约 $0.4–1.0【推断】；attach / detach 不收费。1000 人、每人 30 GiB 约 $2.8–3.4k/月（含快照）。
5. **缩容建议：**
   - 不新增任何等待时，采用**选项 A**：装箱集中、只在用户静默时迁移、空主机 10 分钟后回收。
   - 规模超过约 100 人时，主机数下限的费用会超过存储费用。此时推荐**选项 C（需负责人批准）**：夜间停放空闲用户的卷，早上自动预热。在 1000 人的假设下，主机费用约从 $8.6k/月降到 $3.0k/月【推断】。

---

## 1. 可行性

### 1.1 可用区约束与跨 AZ 迁移

- 卷和实例必须在同一个可用区，只能挂到同 AZ 的实例上。【文档】
  - [EBS volumes](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volumes.html)：「The volume and instance must be in the same Availability Zone」
  - [Attach a volume](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html)
- 由此得出的设计约束【推断】：
  - 共享池的 Auto Scaling 组只放在一个 AZ 的子网里（或者每个 AZ 一个池，用户固定属于某个 AZ）。
  - 专用 Worker 也必须开在用户卷所在的 AZ。
  - 平台控制面可以在别的 AZ。控制面和 Worker 之间的流量按 AZ 间流量计费，每 GB $0.01【API，AWSDataTransfer `DataTransfer-Regional-Bytes`】，量很小。
- 目标机型在 us-east-1 的可用 AZ【API，`describe-instance-type-offerings`】：
  - m8g.2xlarge、m8g.4xlarge、c8g.2xlarge、r8g.2xlarge、m7g.2xlarge 都在 us-east-1a/b/c/d/f 提供，us-east-1e 没有。
- **跨 AZ 只能走「快照 → 在目标 AZ 用快照建卷」。**
  - 快照是异步的：快照立即创建，但会停在 pending，直到数据全部传到 S3，「可能需要数小时，取决于修改过的块数」【文档：[Create snapshots](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-creating-snapshot.html)】。
  - 从快照建的卷要先初始化，块要从 S3 下载。在此之前 I/O 延迟升高，而且默认的初始化速率会波动【文档：[Create a volume](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-creating-volume.html)、[Initialize volumes](https://docs.aws.amazon.com/ebs/latest/userguide/initalize-volume.html)】。
  - **耗时量级【推断】：**
    - 已有定期快照时，停进程后补一次增量快照约 1–5 min，建卷到 available 约 10–60 s，attach、mount、启动约 30 s，合计约 **3–8 min**。
    - 没有任何快照、要先做首次全量时，约 5–20 min。
    - node_modules 是大量小文件，懒加载期间首次 `npm ci` / 构建会明显变慢。
  - **Provisioned Rate for Volume Initialization（推荐用于跨 AZ）：**
    - 建卷时指定 100–300 MiB/s，块按这个速率写入，在可预期的时间内完成初始化。
    - 文档示例：10 GiB 数据、300 MiB/s，约 34.1 s。
    - 按快照里的实际数据量计费，不按卷大小。
    - 一个区域内并发请求的速率合计上限 5,000 MiB/s【文档】。
    - 价格：100–200 MiB/s 档每 GB $0.0024，201–300 MiB/s 档每 GB $0.0036【API，`ProvisionedRateVolumeInitialization`】。5 GB 数据约 **$0.012–0.018/次**。
  - **Fast Snapshot Restore（FSR）：作用与价格**
    - 作用：从启用了 FSR 的快照建出的卷在创建时就已完全初始化，首次访问没有延迟【文档：[FSR](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-fast-snapshot-restore.html)】。
    - 价格：每个快照、每个 AZ 每小时 $0.75，按分钟计，**最少计 1 小时**【API：`USE1-EBS:FastSnapshotRestore` $0.75 per DSU-Hour；文档示例：1 个快照 × 1 个 AZ × 30 天 = $540】。
    - 限制：
      - 每个区域最多 5 个启用 FSR 的快照（可提额）【文档；本账号 Service Quotas 当前值也是 5】。
      - 必须对每个**新**快照单独启用。
      - 启用后先进入 optimizing，每 TiB 需 60 分钟【文档：[FSR state](https://docs.aws.amazon.com/ebs/latest/userguide/view-fsr-enabled-snapshots.html)】。
      - 建卷消耗信用，信用桶从 0 开始，补充速率为每小时 MIN(10, 1024 ÷ 快照 GiB) 个【文档：[credits](https://docs.aws.amazon.com/ebs/latest/userguide/volume-creation-credits.html)】。30–50 GiB 的快照每小时补 10 个，即每 6 分钟才有 1 个信用。
    - **结论【推断】：** FSR 适合「同一个模板快照批量建大量新用户卷」，不适合「每人一次」的迁移。每次至少 $0.75，启用后还要等几分钟，而且受 5 个快照的配额限制。跨 AZ 迁移用初始化速率，便宜两个数量级，时间也可预期。
- 同 AZ 卷复制（EBS Volume Copy / Clones）【文档：[Copy a volume](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-copying-volume.html)】：
  - 只能在**同一 AZ** 内复制，帮不了跨 AZ。
  - 每个区域最多 5 个进行中的复制。
  - 适合「迁移前做一份即时备份」或「复制一个用户环境」。

### 1.2 单台实例可挂的 EBS 卷数，以及每台共享主机能放几个用户

**规则【文档：[EBS volume limits for EC2 instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/volume_limits.html)】**

- **独立上限（Dedicated EBS volume limit）：** 上限不与其他设备共享，网卡、NVMe 实例存储都不占名额。适用机型包括 M8g、C8g、R8g（以及 M7i、C7i、R7i 等）。
- **共享上限（Shared EBS volume limit）：** 其余 Nitro 机型共用一组名额，EBS 卷、网卡、NVMe 实例存储都算在内。「Most Nitro instances support a maximum of 28 attachments」。文档示例：m5.xlarge 只有主网卡时可挂 28 − 1 = 27 块。M7g、C7g、R7g 属于这一类。
- **根卷也占名额。**
  - EC2 API 把 `MaximumEbsAttachments` 定义为「能挂的 EBS 卷的最大数量」【文档：[EbsInfo](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_EbsInfo.html)】，根卷本身就是一块 EBS 卷。
  - AWS 的 EBS CSI 驱动计算可用名额时，两类上限都先减 1（注释写明「plus 1 to account for the root volume」）；共享上限再减「网卡数 − 1」【社区：[aws-ebs-csi-driver `pkg/driver/node.go`](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/pkg/driver/node.go) `getVolumesLimit`】。
  - 这一点在第 5 节 T2 实测确认。
- 超过上限时，AttachVolume 返回 `AttachmentLimitExceeded`【文档】。

**查询结果【API：`describe-instance-types`；与文档 [gp.html](https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html)、[co.html](https://docs.aws.amazon.com/ec2/latest/instancetypes/co.html)、[mo.html](https://docs.aws.amazon.com/ec2/latest/instancetypes/mo.html) 一致；价格为 Pricing API 中 Linux 按需价】**

| 机型 | vCPU / 内存 | 上限类型 | 最大 EBS 卷 | 可放用户卷（扣根卷，1 块网卡） | 最大网卡 | EBS 基线 / 突发 IOPS | 按需价（美元/小时） |
|---|---|---|---|---|---|---|---|
| m7g.xlarge | 4 / 16 GiB | 共享 | 27 | 26 | 4 | 6,000 / 40,000 | 0.1632 |
| m7g.2xlarge | 8 / 32 GiB | 共享 | 27 | 26 | 4 | 12,000 / 40,000 | 0.3264 |
| m7g.4xlarge | 16 / 64 GiB | 共享 | 27 | 26 | 8 | 20,000 / 40,000 | 0.6528 |
| m8g.xlarge | 4 / 16 GiB | 独立 | 32 | 31 | 4 | 6,000 / 40,000 | 0.1795 |
| m8g.2xlarge | 8 / 32 GiB | 独立 | 32 | 31 | 4 | 12,000 / 40,000 | 0.3590 |
| m8g.4xlarge | 16 / 64 GiB | 独立 | 32 | 31 | 8 | 20,000 / 40,000 | 0.7181 |
| c7g.xlarge | 4 / 8 GiB | 共享 | 27 | 26 | 4 | 6,000 / 40,000 | 0.1450 |
| c7g.2xlarge | 8 / 16 GiB | 共享 | 27 | 26 | 4 | 12,000 / 40,000 | 0.2900 |
| c7g.4xlarge | 16 / 32 GiB | 共享 | 27 | 26 | 8 | 20,000 / 40,000 | 0.5800 |
| c8g.xlarge | 4 / 8 GiB | 独立 | 32 | 31 | 4 | 6,000 / 40,000 | 0.1595 |
| c8g.2xlarge | 8 / 16 GiB | 独立 | 32 | 31 | 4 | 12,000 / 40,000 | 0.3190 |
| c8g.4xlarge | 16 / 32 GiB | 独立 | 32 | 31 | 8 | 20,000 / 40,000 | 0.6381 |
| r7g.xlarge | 4 / 32 GiB | 共享 | 27 | 26 | 4 | 6,000 / 40,000 | 0.2142 |
| r7g.2xlarge | 8 / 64 GiB | 共享 | 27 | 26 | 4 | 12,000 / 40,000 | 0.4284 |
| r7g.4xlarge | 16 / 128 GiB | 共享 | 27 | 26 | 8 | 20,000 / 40,000 | 0.8568 |
| r8g.xlarge | 4 / 32 GiB | 独立 | 32 | 31 | 4 | 6,000 / 40,000 | 0.2356 |
| r8g.2xlarge | 8 / 64 GiB | 独立 | 32 | 31 | 4 | 12,000 / 40,000 | 0.4713 |
| r8g.4xlarge | 16 / 128 GiB | 独立 | 32 | 31 | 8 | 20,000 / 40,000 | 0.9426 |

补充：

- 8g 更大的规格上限更高【API】：16xlarge 为 48 块，24xlarge 为 64 块，48xlarge 为 128 块。但单台主机故障影响的人数也随之变大。
- 7g 用共享上限：主机每多挂一块网卡（例如按用户分安全组），就少放一个用户。8g 不受影响。
- 主机若另挂一块共享数据卷（如公共缓存），还要再减 1。
- **建议留 1–2 个名额做运维余量**（临时挂修复卷、迁移重试）。实际按 8g 每台 29–30 人、7g 每台 24–25 人规划【推断】。
- **规格 1 的实例 EBS 突发最长 30 分钟。** 表中带脚注 1 的规格「can support maximum performance for 30 minutes at least once every 24 hours」，之后回到基线【文档：gp.html 脚注】。
  - 主机上所有用户卷共用实例的 EBS 基线：m8g.2xlarge 为 12,000 IOPS / 312.5 MB/s。
  - 每块 gp3 自带 3,000 IOPS。4 个用户同时跑满 IOPS（例如同时 `npm ci`）就能用满 2xlarge 的基线。
  - 这是同机用户之间争抢 I/O 的主要来源【推断】，缓解见第 4 节 R13。

**每台主机实际能放多少人【推断，参数待 T11 实测】**

- 假设：
  - 每个常驻用户空闲时（Gateway + Worker 常驻）约占 0.3–0.6 GiB。
  - 活跃的 Attempt 额外占 1.5–3 GiB、1–2 vCPU。
  - 同一时刻 25% 的常驻用户在活跃。
  - 现有边界的资源上限（Gateway MemoryMax 默认 8 GiB / CPU 300%，Worker MemoryMax 4G）只是天花板，不是保留量【项目：`deploy/install/boundary-lib.sh`、`docs/OPERATIONS.md`】。
- 估算结果：
  - m8g.xlarge 约 10 人，c8g.2xlarge 约 12 人，m8g.2xlarge / r8g.2xlarge 约 20 人，这几种由 CPU / 内存先到顶。
  - m8g.4xlarge 及以上由挂卷上限（31）先到顶。
  - 夜间用户全部空闲时，挂卷上限会先到顶（31 人 × 0.5 GiB ≈ 15.5 GiB，2xlarge 放得下）。

### 1.3 attach / detach：状态、耗时、失败模式

#### 1.3.1 状态，以及怎样正确等待

- 挂载关系（attachment）的状态有 `attaching | attached | detaching | detached | busy`【文档：[VolumeAttachment](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_VolumeAttachment.html)】。卷本身另有 `available | in-use` 等状态。
  - 判断「已挂上」要看 `Attachments[].State == attached`，再在主机上等设备节点出现。只看卷是不是 `in-use` 不够。
  - 判断「已卸下」要等卷回到 `available`。
- **不要用 SDK 自带的 waiter。** botocore 的 `VolumeInUse` / `VolumeAvailable` waiter 每 15 秒轮询一次、最多 40 次【社区：[botocore waiters-2.json](https://github.com/boto/botocore/blob/develop/botocore/data/ec2/2016-11-15/waiters-2.json)】，每一步白白多等最多 15 秒。
  - 建议每 1 秒轮询一次。所有进行中的迁移合并成一次 `DescribeVolumes`（带上卷 ID 列表），这样走的是普通读接口的令牌桶（容量 100，每秒补 20），不走未过滤调用的令牌桶（容量 50，每秒补 10）【文档：[EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)】。
- EventBridge 会为 attachVolume / reattachVolume 发事件，主要用于 KMS 失败之类的通知，「not saved, logged, or archived」。另外，AWS 在例行维护后可能重新挂载卷【文档：[EBS EventBridge events](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-cloud-watch-events.html)】。可以拿来告警，不能代替轮询。

#### 1.3.2 典型耗时【推断】

AWS 没有公布 attach / detach 的时延或 SLA，以下是经验量级，以 T1 实测为准：

| 操作 | 典型 | 长尾 | 备注 |
|---|---|---|---|
| DetachVolume → 卷 available | 3–10 s | 30–60 s | 前提是 OS 内已卸载；未卸载会卡在 busy，可能无限期（见下表） |
| AttachVolume → attached，设备节点出现 | 3–10 s | 20–60 s | EBS CSI 驱动给 attach 留了 60 s，并提到过约 20 s 才返回的调用【社区：CSI [faq.md](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/blob/master/docs/faq.md)】 |
| 干净卸载过的 ext4 / xfs 挂载 | < 1 s | — | 非干净卸载要回放日志，可能还要 fsck（见 1.3.5） |

#### 1.3.3 失败模式与处置

| 失败模式 | 现象 / 原因 | 处置 | 依据 |
|---|---|---|---|
| 卷在 OS 内仍被占用 | detach 卡在 `busy`，「can be delayed indefinitely until you unmount the volume, force detachment, reboot the instance, or all three」 | detach 前由主机代理确认：该用户 UID 的进程已全部退出，`umount` 成功（禁用 `umount -l`），`/proc/*/mountinfo` 里没有其他挂载命名空间仍引用这块设备 | 【文档：[Detach](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-detaching-volume.html)、[DetachVolume](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DetachVolume.html)】；命名空间残留【推断】 |
| 卡在 detaching | 文档建议 Force Detach 只作为最后手段 | **主机还活着时不要强制 detach。** 先让主机代理重试卸载；仍不行就隔离主机（stop / terminate 实例），实例终止后卷会被释放。只在主机已失联时才 Force | 【文档】 |
| 强制 detach 的数据风险 | 「can lead to data loss or a corrupted file system ... must perform file system check and repair」；还可能导致同一设备名无法再挂新卷，直到重启 | 被强制卸下的卷下次挂载前必须 fsck（1.3.5） | 【文档：DetachVolume、Detach 排错】 |
| 卡在 attaching | 设备名已被占用，或驱动还没释放这个设备名；用 CMK 加密但调用方缺 KMS 权限时会「fail silently」并一直停在 attaching；也可能达到挂卷上限 | 控制面为每台主机维护设备名分配表，不立即复用刚释放的名字；KMS 权限见 1.4；名额按 1.2 核算；仍不行就 Force detach 后换设备名重挂，再不行重启或 stop/start 主机 | 【社区：[re:Post 卡在 attaching](https://repost.aws/knowledge-center/ebs-stuck-attaching)】 |
| 旧主机终止时卷没有正常卸下 | 新主机 attach 返回 `VolumeInUse: ... already attached`，要等若干分钟或强制处理 | 缩容一律走「排空 → 卸卷 → 终止」，不靠实例终止来释放卷 | 【社区：CSI issue [#2818](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/issues/2818)、[#1955](https://github.com/kubernetes-sigs/aws-ebs-csi-driver/issues/1955)】 |
| 设备链接竞态，挂错卷 | `/dev/disk/by-id/` 的符号链接在少数情况下会过时 | 挂载前用 `lsblk -o SERIAL` 校验设备序列号等于卷 ID，再校验文件系统 UUID 与控制面记录一致；不一致就拒绝挂载。多租户场景下，挂错卷就是跨用户数据泄露 | 【社区：CSI `pkg/mounter/mount_linux.go` `verifyVolumeSerialMatch`、issue #1224】 |
| NVMe I/O 超时 | 默认 30 s，超时后 I/O 报错，文件系统可能变成只读；Amazon Linux 2017.09.01 起默认已设为最大值 | AMI 内确认 `nvme_core.io_timeout` 为最大值 | 【文档：[NVMe timeout](https://docs.aws.amazon.com/ebs/latest/userguide/timeout-nvme-ebs-volumes.html)】 |

#### 1.3.4 设备名、NVMe 映射与挂载标识

- **API 设备名不等于 OS 里的设备名。**
  - 在 Nitro 上，API 里给的设备名（如 `/dev/sdf`）会被改成 `/dev/nvme[0-26]n1`，枚举顺序可能和指定的顺序不同，每次启动也可能变【文档：[Device names](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/device_naming.html)、[EBS and NVMe](https://docs.aws.amazon.com/ebs/latest/userguide/nvme-ebs-volumes.html)】。
  - 数据卷推荐的设备名是 `/dev/sd[b-z]`、`/dev/xvdb[b-z]`，共 50 个，够 31 块卷用。
- **NVMe EBS 卷的序列号就是卷 ID。** 可用 `lsblk -o +SERIAL`、`ebsnvme-id`、`nvme id-ctrl -V` 查到。文档建议用卷 ID 和挂载点作为稳定标识；Amazon Linux 的 udev 规则还会建 `/dev/sdf → nvmeXn1` 的链接【文档：[Map volumes to NVMe device names](https://docs.aws.amazon.com/ebs/latest/userguide/identify-nvme-ebs-device.html)】。
- **建议的挂载方式【推断，参照 CSI 驱动的实现】：**
  - 用 `/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_vol<去掉连字符的卷 ID>` 找设备，这条路径是 CSI 驱动的做法【社区】。
  - 再用 `lsblk` 核对序列号，用 `blkid` 核对 UUID。
  - 挂载选项加 `nodev,nosuid`。
- **不要把用户卷写进 `/etc/fstab`。** 卷会迁走，由主机代理按需挂载。如果一定要写，按文档加 `nofail`，「If you ever boot your instance without this volume attached (for example, after moving the volume to another instance), the nofail mount option enables the instance to boot」【文档：[Make a volume available](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-using-volumes.html)】。
- **文件系统标签不能装下卷 ID。** 标签长度有限，xfs 12 字节、ext4 16 字节，装不下 21 个字符的卷 ID【推断】。用 UUID 加控制面里的「卷 ID ↔ 文件系统 UUID ↔ 用户」记录。

#### 1.3.5 文件系统：同 UUID 冲突、`nouuid`、何时需要 fsck

- **同一来源的卷共享同一个 UUID。** 两块卷来自同一个快照，或一块卷由另一块的快照恢复而来时，它们的文件系统 UUID 相同【文档：[Boot from wrong volume](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-booting-from-wrong-volume.html)】。
  - 如果用「模板快照」批量建新用户卷，或者为备份恢复出第二块卷，同一台共享主机上就会出现同 UUID 的卷。
  - **xfs 默认拒绝挂载同 UUID 的第二个文件系统**，必须加 `nouuid`【文档：man7 [xfs(5)](https://man7.org/linux/man-pages/man5/xfs.5.html)「Don't check for double mounted file systems using the file system uuid」】。CSI 驱动对 xfs 一律自动加 `nouuid`，注释写的就是这个原因【社区：`node.go` `collectMountOptions`】。
  - ext4 能挂上，但按 UUID 查找会有歧义【推断】。
  - **建议：** 每块卷单独 `mkfs`；如果从模板快照建卷，首次挂载前重写 UUID（`xfs_admin -U generate` / `tune2fs -U random`），并在控制面登记【推断】。
- **何时需要 fsck：**
  - 正常卸载再 detach：不需要。
  - 强制 detach、主机崩溃、带着挂载被终止、文件系统被标记有错：需要。文档要求 Force 之后「must perform file system check and repair procedures」【文档】。
    - ext4：挂载时内核回放 jbd2 日志，必要时先跑 `e2fsck -p` 自动修复【文档：man7 [e2fsck(8)](https://man7.org/linux/man-pages/man8/e2fsck.8.html)】。
    - xfs：`xfs_repair` 不能回放脏日志（返回码 2），必须先挂载一次让内核回放；**而且只能在与写入时相同 CPU 架构的机器上回放**【文档：man7 [xfs_repair(8)](https://man7.org/linux/man-pages/man8/xfs_repair.8.html)「DIRTY LOGS」】。
    - Worker 池必须全部是 arm64（Graviton）；node_modules 里的原生扩展同样绑定架构（见第 4 节 R12）。
  - 主机代理在挂载前用 `dumpe2fs -h` 查看文件系统状态，不是 clean 就先 `e2fsck -p`。30 GiB、数十万 inode 的规模，预计数秒到数十秒【推断，T3 实测】。
- **推荐 ext4【推断】：**
  - `e2fsck -p` 能自动修复。
  - 同 UUID 不会挡住挂载。
  - 日志回放没有跨架构限制（jbd2 的磁盘格式固定，与 CPU 架构无关）。
  - 代价是 inode 数在 `mkfs` 时就定死了。node_modules 小文件多，建议 `mkfs.ext4 -i 8192`（30 GiB 约 390 万 inode）。
  - 如果选 xfs（AL2023 的默认），就必须重写 UUID 或加 `nouuid`，并保证全池同架构。

#### 1.3.6 API 速率限制【文档：[EC2 API throttling](https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html)】

- **AttachVolume / DetachVolume** 属于「Mutating actions」：令牌桶容量 50，每秒补 5，按账号、按区域计，与其他未单独列出的写操作共用。
- **DescribeVolumes**：带过滤条件时走普通读接口（容量 100，每秒补 20）；不带过滤、不分页时走小令牌桶（容量 50，每秒补 10）。
- **其他：** CreateSnapshot、CreateVolume、DeleteVolume 各有自己的令牌桶（容量 100，每秒补 5）；RunInstances / StartInstances 按资源限速（容量 1000，每秒补 2）。
- **量级【推断】：**
  - 一台主机排空 30 人约需 60 次写调用，在 50 的突发内，加上约 2 秒补充，就能全部发出。
  - 1000 人整体恢复（例如 AZ 事件后）约需 2,000 次写调用，按每秒 5 次需约 7 分钟；需要按优先级排队，并按指数退避处理 `RequestLimitExceeded`。
  - 1000 块卷夜间做快照，按每秒 5 次需约 3.5 分钟；交给 DLM 分批。

### 1.4 加密卷与 KMS

- **同账号、同区域：不需要额外步骤。**
  - 挂载加密卷时，EC2 先向 KMS 发 `CreateGrant`，再发 `Decrypt` 解密数据密钥；明文数据密钥只存在于 Nitro 硬件内存中，卷挂着时一直在【文档：[How EBS encryption works](https://docs.aws.amazon.com/ebs/latest/userguide/how-ebs-encryption-works.html)、[KMS：How EBS uses KMS](https://docs.aws.amazon.com/kms/latest/developerguide/services-ebs.html)】。
  - 用默认的 `aws/ebs` 托管密钥时，账号内有 EBS 权限的主体可以直接使用这个密钥（推断自托管密钥的默认策略）。
  - 用客户托管密钥（CMK）时，**调用 attach 的主体**（即控制面）需要以下权限：`kms:CreateGrant`（建议加条件 `kms:GrantIsForAWSResource=true`）、`kms:Decrypt`、`kms:DescribeKey`、`kms:GenerateDataKeyWithoutPlaintext`、`kms:ReEncrypt*`【文档：[Encryption requirements](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-encryption-requirements.html)；社区：re:Post 列出的权限清单】。缺权限时 attach 会「fail silently」。
- **实例侧不需要 KMS 权限。**
  - KMS 授权（grant）发给实例的「仅身份角色」（`aws:ec2-infrastructure/<instance_id>`），不需要创建，也和应用使用的实例角色无关。
  - 但这个角色受 SCP 和密钥策略约束：如果 SCP 或密钥策略按 `aws:SourceIp` / `aws:SourceVpc` 等网络条件拒绝访问，必须把它排除在外，否则 attach 会失败【文档】。
- **密钥不可用时：**
  - 已挂上的卷不受影响。
  - **一旦 detach，下次 attach 就会失败**，直到密钥恢复可用【文档】。
  - 迁移恰好是在制造这种场景，所以需要对密钥状态设置告警（第 4 节 R9）。
- **跨账号不可行。**
  - 别的账号最多能查看或复制共享给它的卷，「Consuming accounts can't attach, modify, delete, or create snapshots of a shared volume」；用 `aws/ebs` 加密的卷根本不能共享【文档：[Share a volume](https://docs.aws.amazon.com/ebs/latest/userguide/share-volume.html)】。
  - 用 `aws/ebs` 加密的快照也不能共享给其他账号【文档：[Share a snapshot](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modifying-snapshot-permissions.html)】。
  - 将来如果要做跨账号灾备，就需要 CMK。
- **成本：** CMK 每个每月 $1，KMS 请求每 1 万次 $0.03【API：awskms】。每次 attach 只有几次请求，可以忽略。
  - 全体用户共用一个 CMK 就够。
  - 每人一个 CMK 能做到按人销毁密钥，但 1000 人要多 $1,000/月【推断】。

### 1.5 IAM：谁调用 attach

- AttachVolume / DetachVolume 支持资源级权限，同时校验**实例**和**卷**两个资源，可以按标签分别加条件（文档示例：卷标签 `volume_user=${aws:username}`，实例标签 `department=dev`）【文档：[EBS IAM policy examples](https://docs.aws.amazon.com/ebs/latest/userguide/security_iam_id-based-policy-examples.html)】。
- **只有控制面的角色持有以下权限：**
  - `ec2:AttachVolume` / `ec2:DetachVolume`，条件：卷带 `kirocrew:owner` 标签，实例带 `kirocrew:role=worker`。
  - `ec2:CreateVolume`（条件 `ec2:Encrypted=true`，并且创建时必须带标签）。
  - `ec2:CreateSnapshot`、`ec2:Describe*`。
  - CMK 上 1.4 所列的 KMS 权限。
- **Worker 实例角色不能有这些权限。**
  - 本项目已经记录：「AWS 凭据：实例角色（Bedrock）由主机上所有进程共享，**不隔离**」【项目：`docs/WORKERS.md` §5】。
  - 共享主机上任何一个用户的 Agent 都能用实例角色。如果这个角色能 attach，就能把别人的卷挂到自己所在的主机上读取【推断】。
  - 建议在 Worker 角色上显式 Deny 卷与快照的写操作，以及 EBS 密钥上的 `kms:CreateGrant`。
- **在主机上执行 `umount` / `mount` / 启动需要 root。** 现有的边界 Worker 进程以用户 UID 运行，做不了这些。需要一个**主机级代理**（root 运行，只在私网接受控制面调用，带认证），提供两个幂等操作：「准备用户」（等设备、校验、fsck、挂载、建用户、写边界配置、启动）和「释放用户」（停止、清理进程、卸载）。这符合「Worker 不回调控制面」：控制面主动调用，并轮询结果【推断】。
  - 用 SSM Run Command 也能做，但每条命令要多出约 2–5 秒的下发和轮询开销【推断】。

### 1.6 执行用户的 UID 必须全局统一

- **原因：** ext4 / xfs 在磁盘上记录的是数字 UID / GID，0700 目录的隔离也靠 UID【项目：WORKERS.md「不同 OS 用户 + 0700 目录」】。
  - 如果 `crew-alice` 在 A 机上是 1003、在 B 机上是 1005，迁移后 alice 读不了自己的文件。
  - 更糟的情况：B 机上 1003 恰好是 bob，那 alice 的工作区和 **Agent 登录凭证都归 bob 所有**，构成跨用户泄露【推断】。
- **做法【推断】：**
  - 控制面给每个用户分配固定的 UID / GID，从不复用（建议用 100000–199999，避开 `login.defs` 默认的 1000–60000 和 65534）。
  - 主机代理在挂载时 `useradd -u <UID> -g <GID>`；如果这个 UID 已被别的名字占用，就拒绝挂载。
  - 挂载后校验卷根目录的属主。
  - 主机层的 `octopus` 用户如果需要访问用户卷，也用固定 GID 或 ACL。
- **替代方案：** idmapped mount（Linux 5.12+ 支持 ext4 / xfs）可以在挂载时做 UID 映射，但复杂度高，MVP 不建议【推断】。

### 1.7 进行中的会话与一次迁移的用户可见时长

- **迁移必然中断该用户的所有进程**：Gateway、Worker、原生会话、会话预览的 dev server。
  - 本项目已实测：Gateway 重启时，运行中的原生任务会被取消，`/context` 背景会丢失【项目：`docs/MVP-RELEASE.md` K15】。
  - 所以只能在用户「静默」时迁移，**而不只是两次 Attempt 之间**。流水线里的下一个 Attempt 通常紧接着就派发。
  - 静默的判定建议为【推断】：没有运行中或待派发的 Attempt、没有打开的原生入口连接（WebSocket）、没有运行中的预览、最近活动超过 10 分钟。
- **迁移后要更新路由：** Worker 地址、端口，以及平台主机 nginx 里指向该边界 Gateway 的原生入口中转配置【项目：`docs/OPERATIONS.md`「boundary native」】。
- **一次同 AZ 完整迁移，用户可见时长【推断，T6 实测】**

| 步骤 | 典型 | 长尾 | 依据 |
|---|---|---|---|
| 0 判定静默、加迁移锁；新任务转入平台排队 | < 1 s | — | 推断 |
| 1 停止该用户的 Gateway、Worker、预览，等其 UID 下的进程全部退出 | 3–10 s | 30 s（需要 SIGKILL） | 推断 |
| 2 `sync` + `umount`（不用 lazy），确认没有占用 | < 1–2 s | 5 s | 推断 |
| 3 DetachVolume，直到卷 available | 3–10 s | 30–60 s；卡在 busy 时无上限 | 推断；文档 |
| 4 AttachVolume，直到 attached 且设备出现 | 3–10 s | 20–60 s | 推断；社区 |
| 5 校验序列号和 UUID，必要时 fsck，然后 mount | < 1 s | 非干净卸载时数秒到数十秒 | 推断 |
| 6 启动 Gateway + Worker，直到 wait-ready | 5–30 s | 60 s | 项目：EC2 整机重启「约 30 s 恢复服务」（MVP-RELEASE.md）；推断 |
| 7 更新路由，释放锁，排队任务出队 | 1–3 s | — | 推断 |
| **合计** | **约 20–60 s** | **1–3 min** | 推断 |

- **缩短时间的办法【推断】：**
  - 在目标主机上提前建好用户、分配好端口、渲染好配置。
  - 轮询间隔用 1 s，不用 SDK waiter。
  - 在共享 ↔ 专用切换时，先把专用实例开好，等用户静默后再做约 1 分钟的切换（3.6）。

### 1.8 与「工作区放本地盘，靠 git bundle / 检查点在新主机重建」比较

| 维度 | 每人一块 EBS 卷，移动卷 | 本地盘 + git bundle / 检查点 |
|---|---|---|
| 换机速度 | 同 AZ 约 20–60 s，与数据量无关【推断】 | 先在旧主机打检查点、上传，再下载、解包，node_modules 若被排除还要 `npm ci`；几 GB 的数据约 1–5 min，随数据量增长【推断】 |
| 凭证与记忆 | 自动跟着卷走，满足「认证信息不另存」 | **git bundle 只带代码。** 现有发布流程的 bundle 本来就排除依赖、缓存和 `.env`，上限 100 MB【项目：OPERATIONS.md】。凭证、Agent 家目录和 KiroCrew 记忆都不跟随，用户要重新登录 kiro-cli、Claude Code、Codex，**这是新增的用户步骤**。如果要它们跟随，就得把 HOME（含凭证）打包进 S3，**违背「不另存」**，需负责人决定 |
| 跨 AZ | 要快照加恢复，分钟级 | S3 与 AZ 无关，这是该方案的优势 |
| 主机故障 | 卷保留，已落盘的数据不丢；可能需要 fsck | 上次检查点之后的数据全部丢失 |
| 并发写保护 | EBS 单挂载，天然只有一台主机能写 | 由应用自己保证 |
| 复杂度 | 卷编排、名额核算、AZ 绑定、固定 UID、主机代理 | 活跃目录（SQLite、转录文件）的一致性快照、增量同步、排除规则、版本管理、恢复校验、各工具数据格式的兼容 |
| 存储成本 | 每人每月 $2.4（30 GiB）+ 快照 | S3 每人每月约 $0.05–0.14，加上主机本地盘的分摊（2.6） |
| 磁盘配额隔离 | 每人一块卷，写满只影响自己，还能按设备单独限 IO | 共享本地盘，需要 xfs project quota 等手段 |

**判断【推断】：** 「凭证与记忆随卷走、不新增用户步骤」是硬要求，EBS 卷是满足它最直接的方案。检查点方案只在需要跨 AZ 时才有优势，可以作为灾备手段（用 EBS 快照即可，见 2.5）。

---

## 2. 成本（us-east-1，按需价）

### 2.1 单价【API：Pricing API，2026-10-25 查询；命令见附录 A】

| 项目 | 单价 | Pricing API usagetype |
|---|---|---|
| gp3 存储 | $0.08 / GB-月 | `EBS:VolumeUsage.gp3` |
| gp3 IOPS（超出免费的 3,000 部分） | $0.005 / IOPS-月 | `EBS:VolumeP-IOPS.gp3` |
| gp3 吞吐（超出免费的 125 MiB/s 部分） | $0.04 / MiBps-月（API 记为 $40.96 / GiBps-月） | `EBS:VolumeP-Throughput.gp3` |
| 快照（标准层） | $0.05 / GB-月 | `EBS:SnapshotUsage` |
| 快照归档层 | 存储 $0.0125 / GB-月；取回 $0.03 / GB；最短存 90 天 | `EBS:SnapshotArchiveStorage` / `...Retrieval` |
| Fast Snapshot Restore | 每快照每 AZ $0.75 / 小时，最少 1 小时 | `USE1-EBS:FastSnapshotRestore` |
| 卷初始化速率 | 100–200 MiB/s 档 $0.0024 / GB；201–300 MiB/s 档 $0.0036 / GB | `EBS:ProvisionedRateVolumeInitialization(.tier2)` |
| KMS | CMK $1 / 月；请求 $0.03 / 万次 | awskms |
| AZ 间流量 | $0.01 / GB（每个方向） | AWSDataTransfer `DataTransfer-Regional-Bytes` |
| EFS Standard / One Zone | $0.30 / $0.16 GB-月 | AmazonEFS `USE1-TimedStorage(-Z)-ByteHrs` |
| EFS IA（Elastic 吞吐）/ IA / Archive | $0.016 / $0.025 / $0.008 GB-月 | AmazonEFS |
| EFS Elastic 吞吐访问 | 读 $0.03 / GB，写 $0.06 / GB | AmazonEFS `USE1-ETDataAccess-Bytes` |
| S3 Standard | $0.023 / GB-月（前 50 TB）；PUT $0.005 / 千次；GET $0.0004 / 千次 | AmazonS3 |

说明：

- 所有 gp3 卷都自带 3,000 IOPS / 125 MB/s 的免费基线，与卷大小无关；按秒计费，最少 60 秒【文档：[EBS pricing](https://aws.amazon.com/ebs/pricing/) Example 1】。
- 定价页 Example 1 里的吞吐示例价是「$0.06 per provisioned MB/s-month」，与 Pricing API 的 $0.04 不一致。以 API 为准；本方案也不需要购买额外吞吐。

### 2.2 attach / detach 本身是否收费

- **不收费【推断，有依据】：**
  - Pricing API 的 AmazonEC2 产品族（Storage、Storage Snapshot、System Operation、Provisioned Throughput、Fast Snapshot Restore、ProvisionedRateVolumeInitialization、EBS direct API Requests 等）里没有任何 attach / detach 计费维度【API】。
  - EBS 定价页的计费项只有存储、IOPS、吞吐、快照、FSR、初始化速率、Volume Clones、Time-based Copy 等【文档】。
  - EBS direct APIs 是读写快照块的 API，与 attach 无关。
- **没有数据传输费：** 同 AZ 移动卷不搬数据【推断】。
- **附带的小额费用：** KMS 请求（每次 attach 只有几次，可以忽略）；CloudTrail 管理事件的第一份副本按 AWS 惯例免费【推断】。
- **卸下的卷照常计存储费：** 「After you detach a volume, you are still charged for volume storage」【文档：Detach】。

### 2.3 每人卷的大小建议

- **用量估算：**
  - 工作区（实测，由委托方提供）：每个 100–155 MB，其中约 95% 是 node_modules。一个用户保留 20–50 个会话工作区，约 2–8 GB【推断】。
  - Agent 家目录与缓存的上界参考【本机】：本机开发者使用数月后，`~/.claude` 2.6 GB（其中转录 2.2 GB）、`~/.codex` 1.4 GB、`~/.kiro` 2.8 GB、`~/.npm` 0.6 GB、`~/.cache/uv` 2.8 GB，合计约 10 GB。服务端边界的用量应当更轻，头几个月预计 1–5 GB【推断】。
  - KiroCrew 记忆和 SQLite：MB 量级【推断】。
- **建议：默认 30 GiB，按需在线扩容。**
  - gp3 的性能与大小无关，所以没必要一开始就分大卷。
  - 用量超过 80% 时自动加 10–20 GiB：ModifyVolume 可在线进行，24 小时内最多改 4 次，**不能缩小**【文档：[Modify a volume](https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modify-volume.html)、[Request modifications](https://docs.aws.amazon.com/ebs/latest/userguide/requesting-ebs-volume-modifications.html)】。之后用 `resize2fs` / `xfs_growfs` 在线扩展文件系统。
  - 重度用户 50 GiB；100 GiB 暂不需要。
  - 配合工作区保留策略（例如关闭超过 30 天的会话就清理 node_modules）【推断】。
- **每人每月存储费【API 单价 × 大小】：** 30 GiB 为 **$2.40**，50 GiB 为 **$4.00**，100 GiB 为 **$8.00**。
- **空闲卷的成本：**
  - 用户不用时卷照样存在，存储费全额照收：30 GiB 仍是 $2.40/月。
  - 可选的「冷存」做法【推断，需负责人批准】：用户连续 30 天没有活动时，先做快照再删卷，按约 5 GB 实际数据计每月约 $0.25。回来时用快照建卷，加初始化速率，约 $0.01，首个任务多等约 1–2 分钟。
  - 快照归档层不适合这个场景：恢复「can take up to 72 hours」，而且最短存 90 天【文档：[Restore archived snapshot](https://docs.aws.amazon.com/ebs/latest/userguide/restore-archived-snapshot.html)、[Archive pricing](https://docs.aws.amazon.com/ebs/latest/userguide/snapshot-archive-pricing.html)】。
- **配额：** 本账号 us-east-1 的「Storage for General Purpose SSD (gp3) volumes, in TiB」当前为 **50 TiB**（可提额）【API：Service Quotas】。1000 人 × 50 GiB ≈ 48.8 TiB，加上各主机根卷就会超出，规模化之前要先申请提额。

### 2.4 100 / 500 / 1000 用户的月成本（卷 + 快照）

快照按每人 8–20 GB 计，即 $0.40–1.00/人/月（见 2.5）【推断】：

| 每人卷大小 | 100 人 | 500 人 | 1000 人 |
|---|---|---|---|
| 30 GiB：只算卷 | $240 | $1,200 | $2,400 |
| 30 GiB：卷 + 快照 | $280–340 | $1,400–1,700 | $2,800–3,400 |
| 50 GiB：只算卷 | $400 | $2,000 | $4,000 |
| 50 GiB：卷 + 快照 | $440–500 | $2,200–2,500 | $4,400–5,000 |
| 100 GiB：只算卷 | $800 | $4,000 | $8,000 |
| 100 GiB：卷 + 快照 | $840–900 | $4,200–4,500 | $8,400–9,000 |

另有各主机自己的根卷，例如每台 30 GiB gp3 为 $2.40/台/月，可以忽略。

### 2.5 快照备份（增量）的成本

- **计费规则：** 快照只存写过的块。第一次是全量（按实际写入的数据量），之后只存变化的块【文档：EBS pricing「Snapshot storage ...」】。快照费用基本与卷的大小无关，取决于写入量和变化量。
- **估算【推断】：**
  - 基线（已写入的块）约 5 GB。
  - 活跃用户每天变化 0.3–1 GB（重装 npm、新建工作区、转录增长）。注意：删掉文件后释放的块被重新写入，也算变化的块。
  - 保留策略「每日快照留 7 天 + 每周快照留 4 周」：占用约 8–20 GB，即 **$0.40–1.00/人/月**；1000 人 $400–1,000/月。
- **工具：** Amazon Data Lifecycle Manager「at no additional cost」【文档：[DLM](https://docs.aws.amazon.com/ebs/latest/userguide/snapshot-lifecycle.html)】。配额是每区域 100,000 个快照【API：Service Quotas】，1000 人 × 11 个快照远低于此。
- **一致性：**
  - 快照只包含请求那一刻已经写入的数据，不含应用或 OS 缓存。文档建议先暂停写入，或者先卸载【文档】。
  - 建议在用户静默时做快照，或者先 `fsfreeze`。SQLite 的 WAL 依靠回放可以达到崩溃一致【推断】。

### 2.6 与 EFS、本地 NVMe + S3 检查点的粗略对比（只看量级）

| 方案 | 每人每月存储相关费用 | 1000 人 | 主要差别 |
|---|---|---|---|
| **每人一块 EBS（30 GiB）+ 快照** | ~$2.8–3.4 | ~$2.8–3.4k | 本报告的方案 |
| **EFS（Regional，按实际用量）** | 存储：约 5 GB × $0.30 = $1.5（部分转到 IA 后约 $0.5–1.0）；Elastic 吞吐访问：每月 60 个任务 × 写 0.15 GB / 读 0.3 GB ≈ $1.1；合计 **~$1.6–2.6**【推断】 | ~$1.6–2.6k | 同一量级。没有挂卷上限，不绑定 AZ，「迁移」就是换台主机重新挂载。但单次操作延迟为读约 1 ms、写约 2.7 ms（最好情况）【文档：[EFS performance](https://docs.aws.amazon.com/efs/latest/ug/performance.html)】，node_modules 小文件多，`npm ci` 和 `git status` 会明显变慢【推断】。SQLite 官方提醒网络文件系统的同步和锁不可靠，可能损坏数据库【文档：[sqlite.org/useovernet](https://www.sqlite.org/useovernet.html)】。两台主机能同时挂同一个用户目录，需要应用自己加锁 |
| **本地 NVMe + S3 检查点** | S3：2–6 GB（多个版本）× $0.023 ≈ **$0.05–0.14**；另加主机本地盘：带实例存储的 m8gd.2xlarge 每小时 $0.4613，比 m8g.2xlarge 的 $0.3590 多 $0.1023，约 +$75/台/月【API】；或者加大根卷 gp3，例如 200 GiB 约 $16/台/月 | S3 ~$50–140，外加主机分摊 | 存储费低一个数量级，但换机要分钟级、上次检查点之后的数据会丢，**凭证要放进 S3**（与「不另存」冲突）。同区域 S3 到 EC2 的流量免费【文档：[S3 pricing](https://aws.amazon.com/s3/pricing/)】 |

**结论【推断】：** 三种方案的存储费都在每人每月 $0.1–3.4 之间，与主机费（2.7）比都是小头。选型应看的是性能、凭证不另存、并发写保护，在这几项上 EBS 最合适。

### 2.7 参照：主机成本与「主机数下限」

- **单台主机月成本【API】：** m8g.xlarge $131，m8g.2xlarge $262，m8g.4xlarge $524（按 730 小时）。EC2 按秒计费，最少 1 分钟【文档：[Stop/start](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html)】。
- **主机数下限：** 每个用户的卷始终挂在某台主机上时（选项 A），主机数不能少于「用户数 ÷ 31」（m8g）。这个下限对缩容的影响远大于存储费：

| 用户数 | A：最少主机数（31 人/台） | A：月成本（m8g.2xlarge，全天候） | C：估算 | B：估算 |
|---|---|---|---|---|
| 100 | 4 | ~$1,050 | ~$500 | ~$340 |
| 500 | 17 | ~$4,450 | ~$1,530 | ~$820 |
| 1000 | 33 | ~$8,650 | ~$2,970 | ~$1,550 |

上表的假设【推断】：

- 每月 730 小时，其中工作时段 220 小时（每天 10 小时 × 22 个工作日）。
- 一台 m8g.2xlarge 能放 20 个常驻且活跃的用户，或 31 个空闲用户。
- 选项 B：工作时段 30% 的用户常驻，其余时间 3%。
- 选项 C：工作时段全部常用用户都预热，其余时间 3%。

存储费在三个选项下相同。账号的 On-Demand 标准实例 vCPU 配额为 1,152【API：Service Quotas L-1216C47A】，A 方案 1000 人时需要 33 × 8 = 264 vCPU，在配额之内。

---

## 3. 缩容策略建议

### 3.1 前提：无论选哪个选项都要做

1. **单 AZ 的 Auto Scaling 组，支持多机型。**
   - 用 mixed instances policy：首选 m8g.2xlarge，备选 m7g.2xlarge（只有 26 个名额）。
   - 全部 arm64，用同一个 AMI 和同一份发布（工具版本一致）。
   - 用来应对单 AZ 容量不足（`InsufficientInstanceCapacity`）【文档：[Troubleshoot launch](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html)】。
2. **不让 ASG 自己挑实例来终止。**
   - 所有实例开启 scale-in protection。
   - 缩容一律由控制面执行：排空，卸卷，解除保护，再调用 `TerminateInstanceInAutoScalingGroup --should-decrement-desired-capacity`。
   - 扩容策略可以继续按 CPU / 内存目标跟踪，但要禁用它的缩容（`DisableScaleIn`）。
   - 暂停 `AZRebalance`。
   - 依据【文档：[Scale-in protection](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-instance-protection.html)、[Suspend processes](https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-suspend-resume-processes.html)】。
   - 注意：scale-in protection **挡不住健康检查替换**【文档】。
3. **终止生命周期钩子作为兜底。**
   - 健康检查替换等意外终止时，给主机代理一个停止和卸卷的机会。
   - 心跳超时默认 1 小时；全局上限是 48 小时与 100 倍心跳中较小者；终止钩子是「best-effort」的，超时就直接终止【文档：[Lifecycle hooks](https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html)】。
   - 就算钩子没生效：用户卷是启动后才挂上的，`DeleteOnTermination` 默认为保留，实例终止后卷还在【文档：[Preserve data on termination](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/preserving-volumes-on-termination.html)】。之后按 fsck 流程重新安置。
4. **可选：warm pool。** 保留几台处于 stopped 状态的实例，只付卷费（「you pay only for the volumes that you use and the Elastic IP addresses」）【文档：[Warm pools](https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html)】，用来缩短扩容和专用 Worker 的开机时间。
5. **迁移只在用户静默时进行**（定义见 1.7）。每个用户每天最多被迁 2 次。每台主机同时最多迁 4 人，全局同时最多 20 人，留在写操作令牌桶的余量之内【推断】。

### 3.2 三个选项

**选项 A：常驻挂载 + 静默合并 + 空主机回收（不新增等待；不需要批准）**

- **做法：**
  - 新用户或新挂载按 3.5 的规则装箱。
  - 每 15 分钟评估一次。池的 CPU 和内存利用率持续 30 分钟低于 40–50%，并且把负载最低的主机 H 上的用户全部移走之后，其余主机仍低于阈值（CPU 70%、内存 75%、名额 29 个）时，把 H 标记为「排空中」：不再往 H 上放人，逐个迁走 H 上已静默的用户。
  - 排空 2 小时还没完成就放弃（H 恢复正常），绝不强迫正在用的用户迁移。
  - **主机上没有任何用户卷时，10 分钟后回收。**
- **用户体验：**
  - 进行中的工作不会被打断。
  - 只有在约 1 分钟的迁移窗口里新提交的任务会进入现有排队，然后自动开始。
- **成本：** 负载下降后约 30–60 分钟回收多余主机，但**降不到 ⌈用户数 ÷ 31⌉ 台以下**（2.7）。
- **适合：** 用户数 ≤ 约 60–100 时（下限只有 2–4 台）。

**选项 B：空闲停放 + 按需落位（新增等待；需负责人批准）**

- **做法：**
  - 用户静默超过 X（建议 2–4 小时）后，控制面停掉该用户的进程并卸卷，卷进入 available 状态，称为「停放」，并空出名额。
  - 下一个任务到来时（或用户打开原生入口时），按 3.5 选一台主机，挂载并启动，再派发任务。
  - 主机上没有任何用户卷时，10 分钟后回收。
- **用户体验：** 停放之后的第一个任务要多等约 30–60 s 的冷启动【推断，T6】。期间任务显示在现有排队里，理由写「正在准备执行环境」。
- **成本：** 三个选项中最低（2.7）。空闲用户不占内存，同一台主机能服务更多活跃用户。
- **代价：** attach / detach 次数明显增多（每人每天数次），1.3.3 的失败模式出现得更频繁；原生入口需要有「打开时唤醒」的机制。

**选项 C：夜间停放 + 晨间预热（只有非工作时间新增等待；需负责人批准）**

- **做法：**
  - 工作时段按选项 A 运行。
  - 每天固定时间（例如当地 21:00）停放所有静默用户。
  - 工作日上班前（例如 07:30）预先把过去 N 天内活跃过的用户挂上并启动，主机提前扩出来。
  - 周末不预热。
- **用户体验：** 工作时段内不会多等。夜间和周末的第一个任务多等约 30–60 s。
- **成本：** 介于 A 和 B 之间。1000 人时约 $3.0k/月，A 约 $8.6k/月（2.7）。

### 3.3 推荐与参数

- **推荐：先上线选项 A（不需要批准）。用户数超过约 100 时，切换到选项 C，C 需负责人批准它在非工作时间的冷启动等待。** 如果负责人能接受白天也偶尔冷启动，选项 B 最省。
- **参数建议【推断，T6 / T8 / T11 实测后调整】：**

| 参数 | 建议值 | 理由 |
|---|---|---|
| 空主机（无用户卷）多久回收 | 10 分钟 | m8g.2xlarge 空转 10 分钟约 $0.06。开一台新主机要 2–4 分钟（推断），空闲时间设得太短容易来回开关 |
| 排空触发 | 池 CPU / 内存利用率持续 30 分钟低于 40–50%，且移走后其余主机低于 70% / 75% / 29 个名额 | 留出余量，避免刚排空就要扩容 |
| 排空期限 | 2 小时，超时放弃 | 不强迫正在用的用户 |
| 用户静默 | 没有运行中或待派发的 Attempt、没有原生连接、没有预览，且最近活动超过 10 分钟 | 1.7 |
| 停放阈值（选项 B） | 静默 2–4 小时 | 午休后不至于每次都冷启动 |
| 停放与预热时刻（选项 C） | 21:00 停放；工作日 07:30 预热 | 按组织所在时区 |
| 最小池 | 工作时段至少 1 台 | 第一个任务不必等扩容 |

### 3.4 迁移期间该用户的新任务怎么办

- **推荐：进入平台控制面现有的排队**，也就是 D60「排队可见」、D61 并发上限用的那套机制【项目：MVP-RELEASE.md】。理由写「执行环境迁移中（约 1 分钟）」，迁移完成后自动出队。
- **不能交给 KiroCrew Gateway 内部的队列。** 迁移期间这个用户的 Gateway 是停着的【推断】。
- **不派到别处。** 用户的工作区、凭证、记忆都在这块卷上，没有这块卷的主机执行不了他的任务【推断】。
- **迁移失败时：**
  - 先挂回原主机，在原主机上出队。
  - 原主机已经没了，就挂到任何一台有名额的主机。
  - 都不行就保持排队，并告警给管理员。提示写明原因，并指向管理员（与 D53「管理与使用分开」一致）。

### 3.5 装箱（bin-packing）策略【推断】

- **放置：优先「最满且仍有余量」的主机**（MostAllocated），让负载集中，低负载主机自然空出来。
  - 硬约束：同 AZ；挂卷名额（留 1–2 个余量）；架构一致。
  - 软约束：
    - 内存：按该用户近 7 天 p95 RSS 估算。
    - CPU：按近 7 天的活跃度估算。
    - 实例 EBS IOPS 预算：m8g.2xlarge 基线 12,000，每个重度用户按 3,000 计。
- **反亲和：** 重度用户分散到不同主机。同一个 Team 不必放在一起（会话共享不依赖同机，见 WORKERS.md）。
- **I/O 隔离：** 每人的卷是一个独立块设备，可以用 cgroup v2 的 `io.max` 按设备给每个用户限 IOPS / 吞吐，缓解同机用户之间的争抢。
- **合并（consolidation）按 3.2 选项 A 的规则周期执行。**

### 3.6 专用 Worker：停机与开机

- **停机后卷是否保留：保留。**
  - stop 后，根卷和数据卷都保持挂载，数据不丢，照常计存储费；实例本身不计费。
  - start 时实例多半会换到新的物理主机，卷和网卡会重新挂上；公网 IPv4 会变（应当用私网地址）。
  - 依据【文档：[How stop/start works](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html)】。
  - 停机期间：根卷 20–30 GiB 约 $1.6–2.4/月，用户卷照计。
- **开机延迟：** StartInstances 到 running，再到 OS 启动和 Gateway / Worker 就绪，约 1–2 min【推断，T9】。
- **开机可能失败：** 会遇到 `InsufficientInstanceCapacity`【文档】。兜底做法是把用户卷从停着的实例上卸下（非根卷可以直接卸），先挂到同 AZ 的共享主机上服务【推断】。
- **休眠（hibernate）可选。** 休眠保留内存，恢复后进程还在，不必重启 Gateway / Worker。
  - m7g / m8g 等支持；要求 AL2023 arm64 AMI 是 2024.07.01 或之后的版本；内存小于 150 GiB；根卷加密且装得下内存内容；必须在启动实例时就开启【文档：[Hibernation prerequisites](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html)；API：`HibernationSupported=True`】。
  - 休眠前的网络连接（例如对 Bedrock 的请求）会断，所以同样只能在静默时休眠【推断】。
- **另一种做法：终止后重开。** 空闲时终止实例（只保留用户卷）；需要时从 AMI 重新 RunInstances，再挂卷。不付根卷的钱，延迟约 1–3 min【推断】。
- **建议：**
  - 默认在工作时段保持运行。
  - 按时段停机（例如 21:00 停，工作日 07:30 开）。工作时段内不新增等待，非工作时间的第一个任务要等开机（**需负责人批准**）。
  - 如果负责人接受「空闲 60 分钟就停机」，成本更低，但白天也会遇到开机等待（**需负责人批准**）。
  - 运行时长参考：m8g.xlarge 每天 10 小时 × 22 天 ≈ $39/月，全天候 $131/月【API 单价 × 时长】。
- **共享 ↔ 专用切换：**
  - 共享 → 专用：先在同一 AZ 开好专用实例，用户继续在共享主机上用；等用户静默时，做约 1 分钟的卷切换。这样不增加等待。
  - 专用 → 共享：先给用户找好共享主机上的名额，再切换，然后停止或终止专用实例。

### 3.7 需要负责人批准的事项

1. 选项 B 或选项 C 引入的冷启动等待（约 30–60 s，前者可能在白天出现，后者只在非工作时间）。
2. 专用 Worker 停机后的开机等待（约 1–2 min）。
3. 「冷存」：30 天没有活动的用户，卷转成快照；回来时首个任务多等约 1–2 min。
4. 是否允许把凭证放进 S3 检查点。本报告不推荐，只在采用 1.8 的检查点方案时才会涉及。
5. 第 5 节的实测需要创建 AWS 资源，预计 ≤ $20。

---

## 4. 风险清单

| # | 风险 | 影响 | 缓解 | 依据 |
|---|---|---|---|---|
| R1 | 所有用户绑定在单个 AZ：AZ 故障或该 AZ 缺容量 | 全部用户不可用 | 多机型的 ASG；必要时购买按需容量预留；DLM 定期快照，并写好「跨 AZ 用快照加初始化速率恢复」的 runbook（RTO 分钟级，RPO 等于快照间隔） | 文档；推断 |
| R2 | 挂卷名额上限（8g 31 人、7g 26 人），7g 上网卡还会占名额 | 主机数有下限，压低成本的空间受限 | 选 8g；名额留余量；选项 B / C 停放空闲用户 | 文档；API；社区 |
| R3 | detach 卡在 busy / detaching：残留进程、`umount -l`、其他挂载命名空间仍引用设备 | 迁移卡住，用户的任务一直排队 | 主机代理严格检查；超时就隔离主机；不在活着的主机上强制 detach | 文档；推断 |
| R4 | 强制 detach、主机崩溃导致文件系统损坏或丢失最后的写入 | 用户数据受损 | 只在主机失联时才 Force；挂载前检查文件系统状态并 fsck；快照兜底 | 文档 |
| R5 | 设备链接竞态或设备名映射错误导致挂错卷 | **跨用户数据泄露** | 用 by-id 找设备，再用 `lsblk` 核对序列号、核对 UUID，任一不符就拒绝 | 社区（CSI #1224） |
| R6 | 同 UUID：模板快照或备份恢复出的卷 | xfs 挂不上；按 UUID 查找有歧义 | 每块卷单独 mkfs，或首次挂载前重写 UUID；如用 xfs 加 `nouuid` | 文档；社区 |
| R7 | 各主机上的 UID 不一致 | 权限错误，或**凭证被其他用户读到** | 控制面分配全局固定的 UID / GID，从不复用 | 推断 |
| R8 | 实例角色是共享的；如果它有 attach 权限 | 任何一个 Agent 都能挂别人的卷 | 只有控制面持有权限；Worker 角色显式 Deny | 项目；文档 |
| R9 | KMS：CMK 权限不足导致 attach 静默卡住；密钥被禁用后，卷一旦卸下就挂不回去；SCP 的网络条件拒绝了仅身份角色 | 迁移失败；严重时所有迁移都失败 | 告警密钥状态；告警 EventBridge 的 attach 失败事件；迁移前先用 DryRun 或校验 | 文档；社区 |
| R10 | EC2 API 限流（写操作令牌桶 50，每秒补 5，整个账号共用） | 大批迁移或恢复时变慢 | 全局令牌桶、批量 Describe、指数退避；申请提额 | 文档 |
| R11 | Agent 工具的登录凭证可能绑定机器（machine-id、主机名、keyring） | 迁移后需要重新登录，等于新增用户步骤 | **T5 必测**：kiro-cli、Claude Code、Codex 各测一遍 | 推断（未知） |
| R12 | 不同架构或不同版本：工具版本漂移；node_modules 原生扩展绑定架构；xfs 日志只能在同架构上回放 | 迁移后工具或工作区不可用 | 全池 arm64，用同一个 AMI 和同一份发布；滚动升级时按批迁移 | 文档（xfs_repair）；推断 |
| R13 | 实例 EBS 带宽和 IOPS 由同机用户共用（2xlarge 基线 12k IOPS，突发每 24 小时只有 30 分钟） | 同机用户相互拖慢 | 装箱时计入 IOPS 预算；用 cgroup `io.max` 按设备限速 | 文档；推断 |
| R14 | ASG 自主缩容、健康检查替换或 AZRebalance 在未排空时终止主机 | 进程被中断；卷可能没有正常卸下 | scale-in protection、禁用自主缩容、暂停 AZRebalance、终止生命周期钩子 | 文档 |
| R15 | gp3 存储配额 50 TiB（本账号当前值） | 规模化时无法再建卷 | 提前申请提额 | API |
| R16 | 快照时仍有写入 | 恢复出的状态不一致 | 静默时做快照，或先 `fsfreeze` | 文档 |
| R17 | 迁移中断原生入口和预览；`/context` 背景丢失 | 用户正在看的页面断开 | 静默判定把原生连接和预览算进去；迁移后更新中转配置 | 项目 |
| R18 | 单个用户的卷写满 | 该用户的任务失败 | 监控并自动扩容（24 小时内最多 4 次，不能缩小）；清理旧会话的 node_modules | 文档 |
| R19 | 选项 B / C 下 attach 次数多，失败模式出现得更频繁 | 冷启动失败 | 自动重试，换主机和设备名；把失败率纳入监控 | 推断 |
| R20 | 专用 Worker 开机时遇到 `InsufficientInstanceCapacity` | 专用用户无法使用 | 兜底挂到共享池 | 文档 |
| R21 | 卷不能跨账号挂载 | 将来拆分账号时卷不能直接挂过去 | 使用 CMK，通过快照共享（不能用 `aws/ebs`） | 文档 |
| R22 | 孤儿卷和孤儿快照（用户已删除、迁移半途失败） | 持续产生费用 | 打标签，做每日对账，删用户时一并删卷和快照 | 推断 |

---

## 5. 建议的小规模实测（本次未执行；需负责人批准后创建资源）

全部放在同一 AZ（跨 AZ 项除外），用 gp3 30 GiB 卷和按需实例；做完立即删除，费用按 Pricing API 单价估算。

| 编号 | 测什么 | 怎么测 | 通过标准 | 资源与时长 | 预计费用 |
|---|---|---|---|---|---|
| T1 | attach / detach 时延分布与失败率 | 2 台 m8g.large，2 块 30 GiB 卷，脚本循环「umount → detach → attach 到另一台 → 等设备 → mount」200 次，以 1 s 间隔轮询，记录每一步耗时 | 得到 p50 / p95 / p99；无卡死 | 2 台 × 3 小时；60 GiB × 3 小时 | 约 $0.6 |
| T2 | 挂卷上限：根卷是否占名额 | m8g.large 挂到第 32 块数据卷，m7g.large 挂到第 27 块，确认 `AttachmentLimitExceeded` 出现的位置；在 m7g 上再加一块网卡复测 | 证实 31 / 26 / 25 | 2 台 × 1 小时；33 块 1 GiB 卷 × 1 小时 | 约 $0.2 |
| T3 | busy、强制 detach 与 fsck | 用 fio 写入时直接 detach（会卡在 busy）；保留一个进程或一个挂载命名空间的引用再 detach；Force detach 后分别测 ext4 和 xfs 的回放、`e2fsck -p` 耗时与数据完整性，各 20 次 | 摸清每种情况的现象与恢复时长 | 1–2 台 × 2 小时 | 约 $0.4 |
| T4 | 同 UUID 与 `nouuid` | 对一块卷做快照，恢复出 2 块，挂到同一台主机（xfs 与 ext4 各一次）；再测重写 UUID 的流程 | 确认现象与规避方法 | 快照几 GB，存不到 1 天 | < $0.1 |
| T5 | **凭证与记忆能否跟随** | 在 A 机上，以卷上的 HOME 登录 kiro-cli、Claude Code、Codex（**需要负责人配合设备码登录**），迁到 B 机（machine-id、主机名都不同）后各跑一个小任务；同时验证 KiroCrew 记忆和 SQLite | 不需要重新登录 | 沿用 T1 的主机，多 1 小时；少量模型调用 | 约 $0.2 + 模型费 < $1 |
| T6 | 端到端边界迁移（真实的 Gateway / Worker） | 2 台 m8g.xlarge 部署同一份发布，固定 UID，主机代理原型；静默判定、排队、迁移、出队跑 20 次，包括原生入口中转的切换 | 合计 p95 ≤ 60 s（目标） | 2 台 × 3 小时 | 约 $1.1 |
| T7 | 跨 AZ：快照加初始化速率 | 约 5 GB 数据：先做增量快照，在另一个 AZ 分别用 200 MiB/s 初始化速率和默认方式建卷，测就绪时间和首次 `npm ci` 时间；FSR 可选测 1 小时 | 得到实际耗时 | 1 台 × 1 小时；初始化费 | 约 $0.3（测 FSR 再加 $0.75） |
| T8 | ASG 排空流程 | 单 AZ 的 ASG，2 台 m8g.large，开启 scale-in protection、终止钩子，warm pool 放 1 台 stopped；模拟控制面排空，再模拟一次健康检查替换 | 卷都能正常卸下；意外终止后能恢复 | 3 台 × 2 小时 | 约 $0.6 |
| T9 | 专用 Worker 的停机开机与休眠 | m8g.xlarge（根卷加密 32 GiB）stop/start 10 次、hibernate/resume 10 次，测到 Gateway 就绪的时间 | 得到开机延迟 | 1 台 × 2 小时 | 约 $0.4 |
| T10 | API 限流 | 30 块小卷在 2 台主机之间并发迁移，观察 `RequestLimitExceeded` 和退避效果 | 30 人同时迁移不出错 | 沿用 T1 | 约 $0.1 |
| T11 | 每台主机能放多少人（装箱参数） | m8g.2xlarge 上开 N 个边界：测空闲时的 RSS；按真实任务组合测并发时的 CPU、内存和 IOPS | 得到每台的人数（替换 1.2 中的推断） | 1 台 × 2 小时 | 约 $0.7 |
| T12 | （可选）EFS 对比 | 同一个仓库分别在 EFS 和 gp3 上跑 `npm ci`、`git status`、SQLite 写入基准 | 验证 2.6 的性能判断 | 1 台 × 1 小时；EFS 数 GB 和访问费 | < $1 |
| **合计** | | | | | **约 $5–8，含余量 ≤ $20** |

---

## 附录 A：只读查询命令与关键结果

```bash
# 机型的 EBS 上限、网卡数、EBS 性能（结果见 1.2）
aws ec2 describe-instance-types --region us-east-1 --instance-types m7g.xlarge m8g.xlarge ... \
  --query 'InstanceTypes[].[InstanceType,EbsInfo.MaximumEbsAttachments,EbsInfo.AttachmentLimitType,NetworkInfo.MaximumNetworkInterfaces,...]'
# 结果：7g 系列 MaximumEbsAttachments=27, AttachmentLimitType=shared；8g 系列（medium–12xlarge）=32, dedicated；
#      8g 16xlarge=48, 24xlarge=64, 48xlarge=128；NvmeSupport=required；HibernationSupported=True（m7g/m8g）

# 可用区
aws ec2 describe-instance-type-offerings --region us-east-1 --location-type availability-zone \
  --filters Name=instance-type,Values=m8g.2xlarge,...   # us-east-1a/b/c/d/f

# EBS 单价
aws pricing get-products --region us-east-1 --service-code AmazonEC2 \
  --filters Type=TERM_MATCH,Field=location,Value="US East (N. Virginia)" Type=TERM_MATCH,Field=volumeApiName,Value=gp3
#  EBS:VolumeUsage.gp3 $0.08/GB-Mo; EBS:VolumeP-IOPS.gp3 $0.005/IOPS-Mo; EBS:VolumeP-Throughput.gp3 $40.96/GiBps-mo（=$0.04/MiBps-mo）
aws pricing get-products ... Type=TERM_MATCH,Field=productFamily,Value="Storage Snapshot"
#  EBS:SnapshotUsage $0.05/GB-Mo; EBS:SnapshotArchiveStorage $0.0125; EBS:SnapshotArchiveRetrieval $0.03/GB
aws pricing get-products ... Type=TERM_MATCH,Field=productFamily,Value="Fast Snapshot Restore"
#  USE1-EBS:FastSnapshotRestore $0.75 per DSU-Hour
aws pricing get-products ... Type=TERM_MATCH,Field=productFamily,Value="ProvisionedRateVolumeInitialization"
#  100–200 MiB/s $0.0024/GB；201–300 MiB/s $0.0036/GB
aws pricing get-products --service-code AmazonEC2 ... Field=instanceType,Value=m8g.2xlarge Field=operatingSystem,Value=Linux \
  Field=tenancy,Value=Shared Field=preInstalledSw,Value=NA Field=capacitystatus,Value=Used    # 各机型按需价见 1.2
aws pricing get-products --service-code AmazonEFS ...   # Standard $0.30；One Zone $0.16；IA $0.025；ET 读 $0.03 / 写 $0.06 per GB
aws pricing get-products --service-code AmazonS3 ... Field=usagetype,Value=TimedStorage-ByteHrs   # $0.023/GB-Mo（前 50 TB）
aws pricing get-products --service-code AWSDataTransfer ... Field=usagetype,Value=DataTransfer-Regional-Bytes   # $0.01/GB
aws pricing get-products --service-code awskms ...   # $1/CMK-月；$0.03/万次请求

# 配额（只读）
aws service-quotas list-service-quotas --region us-east-1 --service-code ebs
#  Storage for General Purpose SSD (gp3) volumes, in TiB = 50；Fast snapshot restore = 5；Snapshots per Region = 100000；
#  Provisioned Rate for Volume Initialization across concurrent requests = 5000 MiB/s；Max concurrent copy volume operations = 5
aws service-quotas get-service-quota --region us-east-1 --service-code ec2 --quota-code L-1216C47A   # 1152 vCPU
```

## 附录 B：来源

**AWS 官方文档**

- EBS 卷上限：<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/volume_limits.html>
- 机型规格（EBS 上限、突发脚注）：<https://docs.aws.amazon.com/ec2/latest/instancetypes/gp.html>、<https://docs.aws.amazon.com/ec2/latest/instancetypes/co.html>、<https://docs.aws.amazon.com/ec2/latest/instancetypes/mo.html>
- EbsInfo / VolumeAttachment / DetachVolume API：<https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_EbsInfo.html>、<https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_VolumeAttachment.html>、<https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_DetachVolume.html>
- 卷基础、挂载、卸载、Multi-Attach：<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volumes.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-attaching-volume.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-detaching-volume.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-volumes-multi.html>
- NVMe 与设备名：<https://docs.aws.amazon.com/ebs/latest/userguide/nvme-ebs-volumes.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/identify-nvme-ebs-device.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/timeout-nvme-ebs-volumes.html>、<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/device_naming.html>
- 挂载与 UUID：<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-using-volumes.html>、<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/instance-booting-from-wrong-volume.html>
- 快照、初始化、FSR、复制：<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-creating-snapshot.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-creating-volume.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/initalize-volume.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-fast-snapshot-restore.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/volume-creation-credits.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/view-fsr-enabled-snapshots.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-copying-volume.html>
- 归档：<https://docs.aws.amazon.com/ebs/latest/userguide/restore-archived-snapshot.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/snapshot-archive-pricing.html>；DLM：<https://docs.aws.amazon.com/ebs/latest/userguide/snapshot-lifecycle.html>
- 扩容：<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modify-volume.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/requesting-ebs-volume-modifications.html>
- 加密与 KMS：<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-encryption.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/how-ebs-encryption-works.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-encryption-requirements.html>、<https://docs.aws.amazon.com/kms/latest/developerguide/services-ebs.html>
- 共享（跨账号）：<https://docs.aws.amazon.com/ebs/latest/userguide/share-volume.html>、<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-modifying-snapshot-permissions.html>
- IAM 示例：<https://docs.aws.amazon.com/ebs/latest/userguide/security_iam_id-based-policy-examples.html>
- API 限流：<https://docs.aws.amazon.com/ec2/latest/devguide/ec2-api-throttling.html>；EventBridge：<https://docs.aws.amazon.com/ebs/latest/userguide/ebs-cloud-watch-events.html>
- 实例生命周期：<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/preserving-volumes-on-termination.html>、<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/how-ec2-instance-stop-start-works.html>、<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/hibernating-prerequisites.html>、<https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html>
- Auto Scaling：<https://docs.aws.amazon.com/autoscaling/ec2/userguide/lifecycle-hooks.html>、<https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-instance-protection.html>、<https://docs.aws.amazon.com/autoscaling/ec2/userguide/as-suspend-resume-processes.html>、<https://docs.aws.amazon.com/autoscaling/ec2/userguide/ec2-auto-scaling-warm-pools.html>
- 价格页：<https://aws.amazon.com/ebs/pricing/>、<https://aws.amazon.com/s3/pricing/>；EFS 性能：<https://docs.aws.amazon.com/efs/latest/ug/performance.html>

**其他**

- Linux 手册：xfs(5) <https://man7.org/linux/man-pages/man5/xfs.5.html>、xfs_repair(8) <https://man7.org/linux/man-pages/man8/xfs_repair.8.html>、e2fsck(8) <https://man7.org/linux/man-pages/man8/e2fsck.8.html>
- SQLite：<https://www.sqlite.org/useovernet.html>
- 社区：
  - re:Post：<https://repost.aws/knowledge-center/ebs-stuck-attaching>
  - aws-ebs-csi-driver：`pkg/driver/node.go`、`pkg/mounter/mount_linux.go`、`docs/faq.md`、issue #2818 / #1955 / #1224
  - botocore `waiters-2.json`
- 本仓库：`docs/WORKERS.md`、`docs/OPERATIONS.md`、`docs/MVP-RELEASE.md`、`deploy/install/boundary-lib.sh`

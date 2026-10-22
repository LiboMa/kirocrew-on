# M4-2 Worker Token 移出数据库（K10，D59，设计稿）

日期：2026-10-22。负责人：「继续完成 M3、M4、M5 的任务，尽可能自动，不要停下来」。范围是看板上的 M4-2（已知问题 K10 的后半）。

## 1. 现状

- 平台调用 Worker 用 `workers.config` 里的 `{"url", "token"}`，Token 以明文存放在数据库中。数据库备份、`restore-verify`、
  管理员手工查看数据库时，都能看到每个执行环境的访问 Token；拿到它就能直接调用 Worker（派发任务、读工作区）。
- 代码仓库凭证（D35）已经用 `octopus/vault.py` 密封：openssl 对称加密 + MAC，密钥文件由 `credentials_key_file` 配置，不在数据库里。

## 2. 做法（我的取舍）

1. **同一把密钥**：Worker Token 用 D35 的 vault 与密钥文件密封，存为 `workers.config.token_sealed`，数据库里不再有 `token`。
   不另设第二把密钥，部署与备份说明不变（密钥文件已单独备份、不进入数据库备份）。
2. **只在发请求时解封**：`transport` 是唯一用到 Token 的地方（`worker_request`、`worker_stream`、`worker_upload`）。它接受
   `{"url", "token"}` 或 `{"url", "token_sealed"}`，后者在发请求前解封，解封结果不缓存到配置字典里，也不写日志。
   读取 `workers.config` 的其他地方不必改；只按键名挑选 `url` 与 `token` 的两处（D56 diff、D58 sync）改为同时接受 `token_sealed`。
3. **写入即密封**：注册 Worker（`POST /v1/workers`）、更新连接（`POST /v1/workers/<id>/connection`）、`bootstrap-worker` 与
   `deploy.sh` 的边界注册都经平台 API，平台在写入数据库前密封。
4. **已有数据**：平台启动时（迁移之后）把仍是明文的 `token` 密封并改写，可重复执行；改写前已有迁移前的自动备份。
   这一步需要密钥文件；没有配置 `credentials_key_file` 时平台按 D35 自动生成（与凭证相同的路径与权限）。
5. **不做**：Token 轮换（`deploy.sh` 重新注册即可）、外部密钥管理（KMS / Secrets Manager，M5 之后）。

## 3. 验收

- 本机：注册与更新连接后，数据库 `workers.config` 中没有 `token`，只有 `token_sealed`；平台照常派发、读取、下载、同步。
- 迁移：一个含明文 Token 的旧数据库启动后被密封，且可重复执行；缺密钥文件时自动生成。
- EC2：部署后 `workers` 表中所有执行环境都只有 `token_sealed`；一个会话完整跑通（派发、检查、审查、下载）。

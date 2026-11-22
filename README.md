# 双时间会员资格历史服务（连锁药房）

面向会员分析团队：在**任意历史时点**解释会员为何获得/失去等级资格，并为营销、门店预算、模型训练分别提供「当时可见」「事后补全」及「二者差异」三种口径的数据集。

技术栈：Vue 3 + FastAPI + PostgreSQL。

## 架构选型

题目要求在两种可复演历史方案中二选一，本实现选择 **追加式双时间事件账 + 可重建投影**：

- `events` 接入账是唯一事实来源：追加-only（DB 触发器禁止 UPDATE/DELETE），每行保存
  **来源哈希**（payload 的 sha256）、**链式哈希**（前序行哈希链，防篡改）、**两类时间**
  （`biz_time` 业务发生时间 / `ingested_at` 平台接收时间）、**修订链**（`supersedes_id`）、
  **幂等键**（唯一约束，重放不重复入账）。
- 身份关系图、规则版本、会员名录均为**可重建投影**：由事件重放生成，
  `POST /api/admin/rebuild` 可随时全量重建并验证结论一致。
- 取舍：更正因果直接可审计（每次变化都是一条带双时间戳的事件），代价是长窗口查询需按
  双时间过滤重放——本服务以「按 (业务时点, 系统时点) 过滤后即时重放」实现任意时点资格计算，
  并通过发布库把常用口径物化冻结，避免重复回放。

## 核心组件

| 组件 | 实现 |
| --- | --- |
| 接入账 | `events` 表：追加-only、来源哈希、链式哈希、双时间、修订链、幂等键 |
| 身份关系图 | `identity_edges` 投影：合并/拆分/待核/撤销的双时间有效区间；误合并拆分支持追溯（`retroactive`） |
| 时间语义目录 | `semantics` 表：`AS_OF`（当时可见）/ `AS_KNOWN_NOW`（事后补全）/ `DELTA`（二者差异） |
| 资格计算器 | 按业务时点、系统时点、规则版本、身份簇即时重放计算等级，输出逐范围解释 |
| 发布库 | `publications`：冻结证据水位（最大事件 id）、身份图版本、规则集版本，并物化各会员资格 |
| 差异归因器 | 比较两个发布，把每个会员的变化归因到水位之间的具体事件（迟到交易/更正/身份变更/规则修订/撤回） |
| 影响索引器 | 把等级变化映射到营销名单、门店预算、训练样本，可按下游实体反查 |

规则解析语义（确定性）：对门店 s、业务时点 T_b、系统时点 T_s，取「入账 ≤ T_s、生效区间覆盖
T_b、门店在范围内、未被覆盖 (版本, s, T_b) 的撤回命中」的版本中 (生效起点, 版本号) 最大者。
并发修订（如 v2、v3 相隔 5 分钟先后修订 v1）由该全序确定性解决，修订链完整保留在账本中。

## 快速启动

### 方式一：Docker Compose（一键）

```bash
docker compose up --build
```

- 前端：http://localhost:8080
- 后端 API：http://localhost:8000 （Swagger UI: http://localhost:8000/docs）
- 数据库：localhost:15432（ccdev/ccdev，库 member_bitemp）

compose 启动时 `AUTO_SEED=1` 会自动灌入演示场景（仅当账本为空）。

### 方式二：本地开发

前置：容器内 PostgreSQL（127.0.0.1:5432，ccdev/ccdev）已运行。

```bash
# 后端（首次会自动创建 member_bitemp 库与表结构）
cd backend
/opt/python-3.12/bin/python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000

# 前端（另开终端）
cd frontend
/opt/node-22/bin/npm install
/opt/node-22/bin/npm run dev        # http://localhost:5173，已配置 /api 代理

# 灌入演示数据（或在前端「总览」页点击"灌入演示场景"）
curl -X POST http://127.0.0.1:8000/api/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}'
curl -X POST http://127.0.0.1:8000/api/seed -H 'Authorization: Bearer token-admin-2026'
```

## 测试账号

| 账号 | 密码 | 角色 |
| --- | --- | --- |
| admin | admin123 | 管理员 |
| analyst | analyst123 | 分析师 |

## 演示场景（种子数据）

同一会员 A（13800000001）贯穿四类历史变更，另有多名对照会员：

1. **加盟店离线补传**：交易 T4 业务时间 2026-01-28，2026-03-15 才到达平台；
2. **误合并后拆分**：2026-02-15 人工裁定把 B 并入 A，2026-04-05 追溯拆分并撤销裁定；
3. **部分追溯规则撤回**：规则 v2（2026-02-01 起追溯生效，GOLD 阈值 1000→1500）于
   2026-04-10 仅对门店 S002 撤回；
4. **并发修订**：v2、v3 相隔 5 分钟先后修订 v1（另有交易 T3 的更正链 300→380）。

关键结论（验证脚本逐项断言）：

| 查询 | 当时可见 (AS_OF) | 事后补全 (AS_KNOWN_NOW) |
| --- | --- | --- |
| A @ 2026-01-31 | SILVER（¥900） | **GOLD**（¥1250，补传改判） |
| A @ 2026-02-28 | GOLD（¥1800，簇 {A,B}） | **SILVER**（v2 范围 ¥1280 + S002 撤回后 v1 范围 ¥350） |
| A @ 2026-03-31 | GOLD（¥2230） | GOLD（¥1630，构成不同） |
| D @ 2026-02-28 | GOLD | GOLD（**被 S002 撤回保护**，否则落入 v2 的 1500 线） |
| F @ 2026-02-28 | GOLD | **SILVER**（被 v2 追溯阈值击中） |

## 验证方式

### 一键端到端验证（39 项断言）

```bash
# 后端运行中执行：
python3 scripts/verify.py            # 默认 http://127.0.0.1:8000
python3 scripts/verify.py http://localhost:8000
```

脚本会重置业务数据、灌入种子，然后断言：任意时点资格（上表全部结论）、更正链前后口径、
DELTA 语义、发布冻结水位、差异归因（迟到交易/更正/身份拆分/规则修订/规则撤回）、
下游影响（营销名单进出、门店预算、训练样本标签重述）、链式哈希完整性、幂等重放、
追加-only 触发器、投影重建一致性、并发修订确定性解决。

**本仓库交付前已实际执行：`39 通过, 0 失败`。**

### 手工验证示例

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/api/login -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin123"}' | python3 -c 'import sys,json;print(json.load(sys.stdin)["token"])')

# 任意双时点资格：A 在 2026-02-28 的两种口径
curl -s "http://127.0.0.1:8000/api/members/13800000001/eligibility?biz_time=2026-02-28T23:59:00Z&semantics=DELTA&sys_time=2026-02-28T23:59:00Z" \
  -H "Authorization: Bearer $TOKEN"

# 账本完整性
curl -s http://127.0.0.1:8000/api/admin/ledger/verify -H "Authorization: Bearer $TOKEN"

# 追加-only：直接改账应被拒绝
psql postgresql://ccdev:ccdev@127.0.0.1:5432/member_bitemp -c "UPDATE events SET actor='x' WHERE id=1"
# => ERROR: events 账为追加式：禁止 UPDATE，请以新事件修订
```

前端页面同样可完成全部验证：总览（语义目录/重建投影）、接入账（写入与查看哈希链）、
资格查询（三种口径 + 时间线）、身份关系图（任意时点簇解析）、规则中心、发布与差异
（冻结发布 → 两两比较 → 查看归因与下游影响）。

## API 概览

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | /api/login | 登录取令牌 |
| POST | /api/events | 写入事件（幂等键去重） |
| GET | /api/events | 账本流水查询 |
| GET | /api/semantics | 时间语义目录 |
| GET | /api/members | 会员名录 |
| GET | /api/members/{key}/eligibility | 任意双时点资格（semantics=AS_OF/AS_KNOWN_NOW/DELTA） |
| GET | /api/members/{key}/timeline | 等级时间线 |
| GET | /api/members/{key}/cluster | 任意时点身份簇 |
| GET | /api/identity/edges | 身份关系边（双时间区间） |
| GET | /api/rules/versions · /api/rules/withdrawals | 规则版本与撤回 |
| POST/GET | /api/publications | 冻结发布（证据水位/身份图/规则集版本） |
| POST/GET | /api/diffs | 差异归因 |
| GET | /api/impacts | 下游影响（可按 dimension/entity 反查） |
| POST | /api/admin/rebuild | 从事件账重建全部投影 |
| GET | /api/admin/ledger/verify | 链式哈希校验 |
| POST | /api/admin/reset | 清空业务数据（演示用） |

完整交互式文档见 `/docs`（Swagger UI，FastAPI 自动生成）。

## 设计红线

- **不得以当前余额或当前主档覆盖历史**：系统不保存"当前等级/当前余额"字段；任何时点的
  资格都由事件账按双时间口径重放得出。误合并拆分、规则撤回等更正只追加新事件并闭合
  旧区间，历史行永不修改、永不删除。

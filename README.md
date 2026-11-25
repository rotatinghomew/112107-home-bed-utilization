# 家庭养老床位建设与使用效益核算

以下资料为合成记录，不含实际个人信息。文件以 UTF-8 编码，普通字段含日期或本地时间（东八区），分号表示同一字段的多值。空值表示在该记录中未提供，不自动当零。表内的凭据编号用于核对记录归属。

## 数据来源

- `data/households.csv`：批准服务期与适配及可及性分组。
- `data/quotes.csv`：组合的组成项；bundle_price 仅适用整包，独立组合的取值同 line_price，金额单位分。
- `data/assets.csv`：设备保留、回库与周转；周转期限从回库验收入库起算，天为单位。
- `data/delivery.csv`：同一次有效服务由机构可用时段与有效首访共同说明。
- `data/decisions.csv`：往期已批准占用与请求中的方案约束，承诺期右端不含。
- `data/budgets.csv`：经费会商定的承诺底线；缺失不是零。
- `data/visit_windows.csv`：责任团队供给资料；记录时间与承诺窗口分开。
- `data/analogue_cases.csv`：历史家庭仅保留项目类型、地区、服务可及条件和阶段时间。observation_end 是可核观测结束日，并非退出。

## 环境及检查

使用 Python 3.11.2 标准库，无需网络或额外安装。在项目目录可执行 `python3 -m unittest discover -s tests -v` 核对输入文件、表头与记录形状。各来源字段及记录数量见 `data-contract.json`。

## 核算口径

所有日期区间按 `[start, end)` 计算，因此 `2026-06-30` 至 `2026-09-01` 为 63 天。

- **启用天数**：在批准服务期内，适配验收、指定资产的设备可用证据、责任团队且落在其可用窗口内的首个服务记录三者同时满足后起算。
- **连带标段**：同一报价包/同一户的多个标段取共同满足后的区间，天数不相加；系统按户聚合。
- **凭证冲正**：`delivery.csv` 中被新记录 `replaces` 的证据在冲正记录可见后不再支持确认天数；原签发预测仍快照保留。
- **可能上界**：只列示缺少/冲正关键凭证但尚未被硬约束排除的天数，绝不并入确认成效。所有候选方案使用同一套保守/中位/乐观/硬上界口径。
- **历史类比**：按地区、`kind`、`access` 和 `recorded_at <= as_of` 取点时可比样本；`observation_end < approved_until` 且无首访者为等待/右删失，`observation_end >= approved_until` 且无合格启用记录者才列为已观察到期未启用。
- **资产**：归还设备从 `received_back + cooldown_days` 起才可给新户；`reserved_until` 不是回库。已签发时段和 `decisions.csv` 的 signed 时段均作为占用，同一资产不能在重叠区间服务两户。
- **报价**：`bundle_price` 只有整个报价包选择时适用；系统按整包枚举预算—天数 Pareto 方案。
- **审批闸口**：预算、报价有效期、资产冲突、地区保守天数底线，以及 signed/已保护承诺自身的天数底线均通过后才标记 `approvable`。

## 常用命令

```bash
# 某区方案比较（建议用独立 store 复盘原始枚举；默认 store 会把已签发方案算作真实占用）
python3 -m bed_accounting options --store /tmp/planning.json \
  --district 西 --as-of 2026-05-20

# 敏感性：少一台 X1；服务队晚一周
python3 -m bed_accounting options --store /tmp/planning.json \
  --district 西 --as-of 2026-05-20 --remove-asset X1
python3 -m bed_accounting options --store /tmp/planning.json \
  --district 西 --as-of 2026-05-20 --delay-days 7 --format json

# 签发并持久化（不修改 data/*.csv）
python3 -m bed_accounting issue W-2026Q3 --district 西 \
  --issue-date 2026-05-20 --select B2

# 结算复盘；如已有支付台账，用 --paid-cents 传入已付金额（分）
python3 -m bed_accounting settle W-2026Q3 --as-of 2026-11-25 \
  --paid-cents 600000

# 只按当前凭证审计三户
python3 -m bed_accounting audit --as-of 2026-11-25
```

签发记录写入 `state/commitments.json`，结算 JSON/Markdown 写入 `state/settlements/`。当前仓库已保留 `W-2026Q3`（西区 B3+B2）和 `E-2026Q3`（东区 B1）的示例签发与复盘。完整结论见 `reports/经费会核算与复盘.md`。

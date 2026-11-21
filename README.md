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

使用 Python 3.11.2 标准库，无需网络或额外安装。在项目目录可执行 `python3 -m unittest discover -s tests -v` 核对输入文件、表头与记录形状。各来源字段及记录数量见 `data-contract.json`。目前提供资料读取检查，不生成业务分析结果。

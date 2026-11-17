# 验证记录

## 环境
本地 Python 3.11 + SQLite 临时库 + fakeredis，真实 FastAPI 路由、服务层与 worker 计算函数；不使用 Docker。

```bash
python3 -m pip install -r backend/requirements.txt -r backend/requirements-dev.txt
python3 backend/tests/test_destruction_e2e.py
```

结果：46 项断言全部通过（2026-11-17）。

## 覆盖
1. 权限：分析师不能发起/恢复销毁；负责人可发起；观察员可查状态与凭据。
2. 去重：同一批次+编号集合重复提交复用原任务；恢复重试不新增任务；完成后重试为无操作。
3. 校验：空编号、越界编号及属于其他批次的编号被拒绝。
4. 封存后入口：源记录读取/新增/编辑/删除、新建运行、运行详情、复核、导出、运行列表内容全部拒绝或去值。
5. 晚到计算：封存前已 `running` 与仍 `queued` 的任务均不写回结果、不生成共享产物。
6. 清理失败：删除共享留档失败时任务记 `failed` 并保留定位编号；恢复清理后成功。
7. 同名异批：失败与恢复过程均未误删其他批次相同源记录编号；其他批次运行与导出保持可用。
8. 核对与凭据：完成需所有副本核对清空（源观测/冻结输入/结果/产物卷/复核留档/留痕正文）；凭据不含原值，仅保留集合 SHA256、计数、运行编号与核对结果；任务完成后不再保留定位编号。
9. 状态轨迹：`pending → cleaning → failed → pending → cleaning → completed` 完整可查。
10. 竞态：模拟计算结果产生后、写回前封存落地，结果被丢弃且无产物文件（`/tmp` 独立脚本验证）。

## 未覆盖
- 真实 PostgreSQL/Redis/Docker 编排下的并发压测。
- 浏览器端视觉回归；前端仅做 ES module 语法检查。

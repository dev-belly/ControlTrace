# 参与 ControlTrace

先阅读 [控制目录](docs/CONTROL_CATALOG.md)、[数据字典](docs/DATA_DICTIONARY.md)和[复核完整性设计](docs/REVIEW_INTEGRITY.md)。本项目处理合成的访问权限与生产变更证据；新增案例也应使用可公开的合成数据。

## 本地开发

在仓库根目录使用 Python 3.11+：

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest -q
```

Windows PowerShell 可直接使用环境中的程序，无需激活：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m pytest -q
```

现有 CI 在 Linux Python 3.11/3.12、Windows Python 3.12 运行 Ruff 与完整 pytest。修改依赖定义时保持已有锁文件流程一致；不要通过更新依赖或证据文件隐藏失败。

## 修改与复核

- 控制规则变更应给出最小来源记录、截止时点、预期发现及未命中反例；阈值属于演示政策，不应写成法规要求。
- 复核变更应覆盖证据指纹变化、相同证据重跑、旧表单冲突及事务失败；保留人工结论与自动发现的区别。
- 导出或校验变更应说明 ZIP 格式兼容性、CSV 原值与展示值、时间排序，以及历史复核不能继承到新证据的边界。
- 修改历史格式逻辑时使用现有历史夹具验证，不用当前序列化器重新生成“旧版本”样本。

提交说明应包含触发场景、修改后行为和实际运行的检查。纯文档或配置修改可只做相关链接、格式与 YAML 检查。问题入口见 [SUPPORT.md](SUPPORT.md)；敏感安全问题见 [SECURITY.md](SECURITY.md)。不要提交真实 HR/IAM/工单、日志、账号或审计底稿。

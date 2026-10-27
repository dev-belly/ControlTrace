# ControlTrace 支持说明

先查阅 [使用流程](README.md)、[数据字典](docs/DATA_DICTIONARY.md)和[控制目录](docs/CONTROL_CATALOG.md)。开发环境可运行：

```bash
python -m pip install -e ".[dev]"
python -m ruff check src tests
python -m pytest -q
```

通过 [Issue 表单](https://github.com/dev-belly/ControlTrace/issues/new/choose)报告问题时，请附版本或提交、OS/Python、具体命令、预期与实际行为。规则问题需标明控制 ID、合成来源 ID、审计截止日及时区；复核问题需说明证据是否变化、界面是否重新加载，以及两个会话的操作顺序；验证问题需附底稿格式版本和错误说明。

仅提交可公开的最小合成案例。真实 HR、IAM、访问日志和审计底稿不应进入公开 Issue。源证据变化后，旧结论保留在历史而当前状态返回待复核，是既定行为，详见[复核完整性](docs/REVIEW_INTEGRITY.md)及[时间线迁移](docs/TIMELINE_ORDER.md)。

贡献检查见 [CONTRIBUTING.md](CONTRIBUTING.md)，疑似安全漏洞按 [SECURITY.md](SECURITY.md)处理。规则命中仍需审计人员复核。

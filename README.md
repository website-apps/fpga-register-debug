# IPT 寄存器 debug 小帮手

独立应用源码，平台自动跟踪 `main`。保持主站暖白和青绿色配色。

平台提供 `DATABASE_URL`、`APP_SESSION_SECRET`、`/app-data` 和只读 `/app-config`。生产数据继续使用 `website_business.fpga` 和原应用账号；原始资料及真实配置不进入公开仓库。

提交后自动构建、测试和健康检查，通过后发布。负责人可通过 `git revert` 提交回退；主站管理员可选择兼容历史产物。回退不删除业务数据，相同 `data_contract` 的版本必须向后兼容。

`python3 -m unittest discover -s tests` 运行隔离测试，测试专用 SQLite 连接仅在测试文件中注入，生产适配器没有 SQLite 回退。

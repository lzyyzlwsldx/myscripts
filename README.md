# 1.交付物说明

> - 🔹 [demo 仓库示例](http://10.38.124.5:8080/231_NewVideoPlatformBaseRedevelope/install_demo)
> - 🔹 [变量替换&自动部署工具](https://fnsdaxgdmb.feishu.cn/wiki/Il56wZaz5iCDz4khKEJc81Ornsc)
> - 🔹 [属地交付物规范](https://fnsdaxgdmb.feishu.cn/wiki/UTQ0wdhuui97LJkMhsKcAoMTnwh)
> - 🔹 [交付物部署说明](https://fnsdaxgdmb.feishu.cn/wiki/JpoOw6ZkOibwCVkNgQecNpqOnHb)

# 2.仓库说明

## 2.1 关于分支

1. 先推送至 develop，后续由 develop 合并至 release。
2. 正式发版交付前，请提交 develop → release 的 MR，联系**属地开发支撑团队**完成合并。
3. 运维使用 release 分支下的对应版本包完成部署。

## 2.2 关于目录

1. 请在同一分支下创建交付目录，并按版本号进行命名，例如：1.2.3、3.3.154、3.3.5-3.4.0、3.4.0-国产化、3.4.x 等。
2. 目录可用于区分不同交付类型，如增量、全量或特殊版本等，命名应清晰反映实际内容。

```bash
install_xxx/                              # 交付仓库
├── 3.3.x/                              # 增量小版本
├── 3.3.16x/                              # 增量小版本
├── 3.3.5-3.4.x/                          # 跨版本增量
├── 3.4.x/                                # 全量大版本
├── 3.4.x-国产化/                          # 国产化全量版本
├── ...
└── README.md              
```

# 3.实施说明

## 3.1 编写说明

1. 建议使用 IDE 工具编写交付物；CSV 推荐使用 WPS 编辑，以 UTF-8 保存。
2. 本机使用[变量替换&自动部署工具](https://fnsdaxgdmb.feishu.cn/wiki/Il56wZaz5iCDz4khKEJc81Ornsc) 进行检查，提前发现问题。
3. 部署自测，具备测试环境后参考[交付物部署说明](https://fnsdaxgdmb.feishu.cn/wiki/JpoOw6ZkOibwCVkNgQecNpqOnHb)。
4. 注意**地址类环境变量**的区分，例如脚本中的 Nacos 连接地址与服务配置中的连接地址可能不同，需在全局变量中明确区分。

## 3.2 占位符说明

1. 使用 **`\x02VAR_NAME\x03`** 包裹变量名称，变量名称可自定义，将变量定义及说明写入 **`global-vars.csv`** 文件中。
2. **`\x02...\x03`** 占位符可为控制符或字符串，建议使用控制符，控制符较难手动打出，使用 idea 或 vscode 等 IDE 复制粘贴控制符使用。
3. 除了 **`global-vars.csv`** 文件外，**`controls/, scripts/, k8s-resources/`** 目录下的所有文件中均可定义全局变量。
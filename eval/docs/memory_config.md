# 内存参数配置

模块：configuration
标签：memory

## 能力说明
- 内存配置 支持 work_mem 调整。
- 内存配置 支持 shared_buffers 配置。
- 内存配置 支持 排序内存控制。
- 内存配置 支持 Hash Join 内存控制。

## 故障关系
- 内存配置 可能原因 work_mem 过小。work_mem 过小 导致 排序落盘。
- 内存配置 可能原因 shared_buffers 过低。shared_buffers 过低 导致 缓存命中下降。
- 内存配置 可能原因 并发过高。并发过高 导致 内存争用。
- 内存配置 可能原因 Hash 表过大。Hash 表过大 导致 临时文件增加。

## 关键词
排查该主题时经常出现这些术语：work_mem、shared_buffers、temp file、hash join。

## 处理建议
建议先确认模块和标签，再结合系统视图、执行计划、参数配置和历史变更记录进行排查。

# BiliBili 醒目留言记录器

一个简易的二次开发玩具，vibe code含量接近100%，可能会出现莫名其妙的bug

使用jingguanzhang/gift-book作为前端，xfgryujk/blivedm获取数据来达成以“礼簿”形式实时记录醒目留言或者大航海信息的效果，支持多直播间并行，除了消息获取部分完全离线运行

## 安装
- 确保你的系统PATH中安装了Python，仅在Python 3.14上进行过测试
- 将项目clone到本地 ```git clone --recurse-submodules https://github.com/sadbee166/bilibili-sc-in-a-giftbook.git```
- 在你的环境下运行 ```pip install -r requirements.txt``` 来安装依赖

## 配置和使用
- 可选地复制 giftbook.config.example.json 的副本并在对应文件中配置主要参数
    - room_id   直播间号，多个可用半角逗号分隔
    - host  监听地址
    - port  端口
    - sessdata  bilibili账号登录会话Cookie，没有遇到问题就不需要填写
    - membership_logging    是否记录大航海
- 运行 ```python -m giftbook_bridge --config CONFIG_PATH``` 来启动gift-book的webui
- 创建事项后在**设置此事项**页面中可以选择一个事项关联的直播间ID


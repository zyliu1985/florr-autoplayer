"""从florr.io官方(M28 Games)的地图服务器查询接口拿实时有效的服务器码 ——
不是社区追踪站(ashish.top/craft.darkmax.top)转发/缓存的数据, 是这些网站自己
也在用的同一个官方接口, 直接调, 每次调都是当下最新数据, 不存在"码过期"这个
问题(之前DESERT_SERVER_IDS那版手动抄的静态列表才有这个问题).

来源: 读的是greasyfork.org一个开源(MIT协议)、完全没混淆的florr.io服务器
切换油猴脚本(https://greasyfork.org/scripts/461100)的源码, 里面这段fetch
调用变量名/结构都清清楚楚, 没有任何隐藏手法 —— 跟florr-auto-sszone那个
main.py刻意混淆的情况不是一回事, 这才放心照着抄这段请求逻辑. 实测过接口本身
(curl直接调, 返回的id "254j/254k/254l"跟社区追踪站数据完全对得上).
"""
import json
import ssl
import urllib.request

import certifi

M28_ENDPOINT_TEMPLATE = "https://api.n.m28.io/endpoint/florrio-map-{index}-green/findEach/"

# Windows下Python标准库的urllib/ssl不会自动去读系统证书库(跟macOS/Linux不
# 一样) —— 实机在Windows上跑switch_server()复现过这个报错:
# "ssl.SSLCertVerificationError: unable to get local issuer certificate".
# 显式用certifi自带的证书链建一个SSLContext, 不依赖某台机器系统证书装没装好.
_SSL_CONTEXT = ssl.create_default_context(cafile=certifi.where())

# 接口挂了Cloudflare, 默认urllib的UA("Python-urllib/3.11")会被拦成403 ——
# 实测确认过(curl不带任何特殊UA能通, 纯Python默认UA不行). 老实标明这是什么
# 工具就够用了, 不用伪装成浏览器/curl.
_USER_AGENT = "florr-auto-pathing (github.com/greatluca666/florr-auto-pathing)"

# 顺序跟油猴脚本源码里matrixs数组完全一致, index对应关系不能改(是这个接口的
# URL路径参数, 不是我们能自己定义的东西).
BIOME_INDEX = {
    "garden": 0,
    "desert": 1,
    "ocean": 2,
    "jungle": 3,
    "ant_hell": 4,
    "hel": 5,
    "sewers": 6,
}

# utils.MAP / config 的 map 名 → BIOME_INDEX 的生态名. 只有 anthell 名字对不上
# (地图叫 anthell, 接口生态叫 ant_hell), 其余同名.
MAP_TO_BIOME = {"anthell": "ant_hell"}


def biome_for_map(map_name):
    """把地图名换算成服务器查询接口的生态名(BIOME_INDEX 的键)."""
    return MAP_TO_BIOME.get(map_name, map_name)


def fetch_server_ids(biome="desert", timeout=5):
    """查一次官方接口, 返回这个生态区域当前3个区域(NA/EU/AS对应
    vultr-miami/vultr-frankfurt/vultr-tokyo)的实时服务器码列表.

    biome不在BIOME_INDEX里时抛KeyError(比抛一个更晦涩的URL 404错误清楚).
    网络请求本身失败(超时/接口挂了)时原样抛出urllib的异常, 不在这里吞掉 ——
    switch_server()该知道这次到底有没有真的拿到数据.
    """
    index = BIOME_INDEX[biome]
    url = M28_ENDPOINT_TEMPLATE.format(index=index)
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CONTEXT) as resp:
        data = json.load(resp)
    return [server["id"] for server in data["servers"].values()]

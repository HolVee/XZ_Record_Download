import requests
import argparse
import os
import json
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import base64
from Crypto.PublicKey import RSA
from Crypto.Cipher import PKCS1_v1_5


# 获取桌面路径
def get_desktop_path():

    home_path = Path.home() / 'Desktop'
    return str(home_path)

class XZDownloadCyclingRecord:

    # 最大记录条数
    MAX_RECORD_NUM = 99999

    # 下载配置
    XZ_CFG = {

        "RSA": {
            "public_key": "TUlHZk1BMEdDU3FHU0liM0RRRUJBUVVBQTRHTkFEQ0JpUUtCZ1FEbXVRa0JiaWp1ZERBSmdmZmZEZWVJQnV0cVdIWnZVd2NSdXZXZGc4OTM5M0ZTZHozSUpVSGMwcmdJL1MzV3VVOE4wVmVQSkxtVkFadENPSzRxZTRGWS9lS21XcEptbjdKZlhCNEhUTVdqUFZveVJabVNZalc0TDhHcldtaDUxUWo3RHdwVEFEYWRGM2FxMDRvK3MxYjhMWEphOHI2K1RJcXFMNVdVSHRScW1RSURBUUFC",
            "public_exponent": "010001",
            "key_size": "1024"
        },
        "login": {
            "url": "https://www.imxingzhe.com/api/v1/user/login/",
            "method": "POST",
            "load": {
                "account": "15xxxxxxxx",
                "password": "xxxxxxx=="
            },
            "response": {
                "code": 0,
                "msg": "",
                "data": {
                    "userid": 7000000,
                    "username": "xxxx",
                    "enuid": "xxxxx"
                }
            },
            "headers": {
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Host": "www.imxingzhe.com",
                "Origin": "https://www.imxingzhe.com",
                "Referer": "https://www.imxingzhe.com/login",
            }

        },
        "cycling_record": {
            "url": "https://www.imxingzhe.com/api/v1/pgworkout/",
            "method": "GET",
            "load": {
                "offset": "0",
                "limit": MAX_RECORD_NUM,
                "sport": "",
                "year": "",
                "month": ""
            },
            "response": {
                "code": 0,
                "data": {
                    "data": [
                        {
                            "id": 200000000, # record_id
                            "sport": 3,
                            "title": "2026-03-20 下午 骑行", # title
                            "duration": 9254,
                            "distance": 49143,
                            "elevation_gain": 108,
                            "start_time": 1773991332000,
                            "avg_speed": 19.116,

                        }

                    ]  # 骑行记录数据
                },
                "msg": ""

            },


        },
        "download": {
            "max_retry": 3,
            "timeout": 30,
            "gap": 0.1,
            "max_gap": 3.0,
            "default_path": os.path.join(get_desktop_path(), 'xz_record'),
            "fit": {
                "url": "https://www.imxingzhe.com/api/v1/workout/{record_id}/fit/",
                "method": "GET",
                "response": None,  # 响应里面没有内容，相应的请求头location里有oss的地址，那就是fit文件，但是requests会自动访问location并返回响应的内容
            },
            "gpx": {
                "url": "https://www.imxingzhe.com/api/v1/pgworkout/{record_id}/gpx/",  # record_id从cycling_record来
                "method": "GET",
                "response": None  # gpx文件
            }
        }
    }

    def __init__(self, account, password, year=None, month=None, _all=False, target_dir=None, record_type='gpx', sport=3):

        if record_type.lower() not in ['gpx', 'fit']:
            raise ValueError(f'参数record_type错误，无效类型: {record_type}; 可选类型：1.gpx, 2.fit。默认为gpx')

        self.year = year
        self.month = month
        self._all = _all
        self.record_type = record_type
        self.sport = sport if sport >= 0 else None

        self.account = account
        self.password = password

        self.real_fail = dict()

        self.target_dir = target_dir or self._get_target_dir()
        self.session = self._login()
        self.record_list = self._get_cycling_records()

    # 启动入口
    def start(self, max_workers=20, gap=0.1):
        print("开始下载...")

        futures = []
        max_workers = min(max_workers, os.cpu_count() + 4)
        thread_pool = ThreadPoolExecutor(max_workers=max_workers)

        with thread_pool as executor:
            for record_info in self.record_list:
                futures.append(executor.submit(self._download, record_info))
                time.sleep(gap)

        stat = {
            "记录数量": len(self.record_list),
            "下载成功": 0,
            "下载失败": 0,
            "跳过下载": 0,
        }
        for future in as_completed(futures):
            record_id, status, err = future.result()
            if status == 'fail':
                stat["下载失败"] += 1
            elif status == 'success':
                stat["下载成功"] += 1
            elif status == 'skip':
                stat["跳过下载"] += 1

        print('下载结束')

        if self.real_fail:
            print('下载失败的记录')
            for fail_record in self.real_fail.values():
                print(fail_record)
        else:
            print('无下载失败的记录')

        print("统计结果")
        print(stat)
        print(f"保存路径: {self.target_dir}")

    # 登录
    def _login(self):
        print(f"{self.account} 登陆中...")

        login_cfg = self.XZ_CFG['login']

        login_url = login_cfg['url']
        login_headers = login_cfg['headers']

        login_load = login_cfg['load']
        login_load['account'] = self.account
        login_load['password'] = self._encrypt_password()

        session = requests.session()
        res = session.post(login_url, data=json.dumps(login_load), headers=login_headers)

        if res.status_code != 200:
            raise Exception(f'登录失败, 状态码: {res.status_code}')

        print("登录成功")
        return session

    # 密码加密
    def _encrypt_password(self):

        def rsa_encrypt(s, public_key:str):
            pk = base64.b64decode(public_key.encode()).decode()

            pem_key = f"-----BEGIN PUBLIC KEY-----\n{pk}\n-----END PUBLIC KEY-----"
            pub_key = RSA.import_key(pem_key)
            cipher = PKCS1_v1_5.new(pub_key)

            return cipher.encrypt(s.encode('utf-8'))

        rsa_cfg = self.XZ_CFG['RSA']
        password = self.password
        public_key = rsa_cfg['public_key']

        rsa_encrypted_password = rsa_encrypt(password, public_key)
        encrypted_password = base64.b64encode(rsa_encrypted_password).decode()

        return encrypted_password

    # 获取骑行记录
    def _get_cycling_records(self):
        print(f"获取记录中...")
        cycling_record_cfg = self.XZ_CFG['cycling_record']
        cycling_record_url = cycling_record_cfg['url']

        cycling_record_load = cycling_record_cfg['load']
        cycling_record_load['sport'] = self.sport # 骑行编码为3
        cycling_record_load['year'] = self.year or cycling_record_load.get('year')
        cycling_record_load['month'] = self.month or cycling_record_load.get('month')
        if self._all:
            cycling_record_load['year'] = None
            cycling_record_load['month'] = None
            cycling_record_load['limit'] = self.MAX_RECORD_NUM

        res = self.session.get(cycling_record_url, params=cycling_record_load)

        if res.status_code != 200:
            raise Exception(f'获取骑行记录失败, 状态码: {res.status_code}')

        res_data = res.json().get('data').get('data')

        if not res_data:
            raise Exception(f'无骑行记录')

        record_list = [{"id":record['id'],"title": record["title"]} for record in res_data]
        # self.stat["查询到的记录"] = len(record_list)
        print(f"获取记录成功, 共 {len(record_list)} 条")
        return record_list

    # 文件存放路径
    def _get_target_dir(self):
        target_path = self.XZ_CFG['download']['default_path']
        target_path = os.path.join(target_path, str(self.account), self.record_type)
        if not os.path.exists(target_path):
            # print(target_path)
            os.makedirs(target_path)
        return target_path

    # 下载骑行记录文件
    def _download(self,record_info:dict):

        download_cfg = self.XZ_CFG['download']

        record_name = record_info["title"].replace(' ', '_')
        record_id = record_info["id"]
        record_type = self.record_type

        file_ = os.path.join(self.target_dir, f'{record_name}_{record_id}.{record_type}')

        # 如果文件已经存在，则跳过
        if os.path.exists(file_):
            print(f'{record_name}_{record_id} 已经存在，跳过下载')
            return record_id, 'skip', None

        url = download_cfg[record_type].get('url').format(record_id=record_id)

        max_retry = int(download_cfg['max_retry'])
        timeout = int(download_cfg['timeout'])
        gap = float(download_cfg['gap'])
        max_gap = float(download_cfg['max_gap'])
        err = None

        for attempt in range(max_retry+1):
            try:
                res = self.session.get(url, timeout=timeout)
                if res.status_code == 200:
                    with open(file_, 'wb') as f:
                        f.write(res.content)
                    print(f'{record_name}_{record_id}.{self.record_type} 下载成功')
                    return record_id, 'success', None
                err = f"status={res.status_code}"
            except Exception as e:
                err = repr(e)

            time.sleep(min(2 ** attempt * gap, max_gap))

        self.real_fail[record_id] = record_info

        print(f'{record_name}_{record_id}.{self.record_type} 下载失败, {err}')

        return record_id, 'fail', err

# 测试用例
def demo():
    from account_cfg import account, password

    year = 2024
    month = 8
    _all = False
    record_type = 'gpx'

    downloader = XZDownloadCyclingRecord(account, password, year=year, month=month,_all=_all, record_type=record_type)

    max_workers = 10
    gap = 0.2
    downloader.start(max_workers=max_workers, gap=gap)

# 解析参数
def parse_args():
    parser = argparse.ArgumentParser(description="行者骑行记录下载器")

    # 1. 位置参数（必须传的，比如脚本后面紧跟的账号密码）
    # parser.add_argument("account", help="行者账号")

    # 2. 可选参数（--xxx，不传就用默认值）
    parser.add_argument("-u", "--user", type=str, help="登录账号")
    parser.add_argument("-p", "--password", type=str, help="登录密码")
    parser.add_argument("-y", "--year", type=int, default=None, help="下载年份，如 2025")
    parser.add_argument("-m", "--month", type=int, default=None, help="下载月份，如 3")
    parser.add_argument("-s", "--sport", type=int, choices=[-1,0,1,2,3,4,5,6,7,8],default=-1, help="运动类型(全部: -1, 其他: 0,  徒步: 1, 跑步: 2, 骑行: 3, 游泳: 4, 滑雪: 5, 训练: 6, 室内骑行: 7, 虚拟骑行: 8。默认值: -1)")
    parser.add_argument("-t", "--type", type=str, choices=['gpx', 'fit'], default='gpx',
                        help="下载文件类型 (gpx 或 fit)")
    parser.add_argument("-o", "--output", type=str, default=None, help="自定义保存目录")

    # 3. 布尔开关（带了这个参数就是 True，不带就是 False）
    parser.add_argument("--all", action="store_true", help="下载全部记录（忽略年份月份）")

    # 4. 控制并发和频率的参数
    parser.add_argument("-w", "--workers", type=int, default=20, help="并发线程数")
    parser.add_argument("-g", "--gap", type=float, default=0.1, help="请求间隔时间(秒)")

    return parser.parse_args()

# 主入口
def main():
    # 1. 解析命令行参数
    args = parse_args()

    if args.user is None or args.password is None:
        raise Exception("请输入账号密码, 参考命令: python xz_cycling_record_download.py -u 账号 -p 密码")

    sport_map = {
        -1: '全部',
        0: '其他',
        1: '徒步',
        2: '跑步',
        3: '骑行',
        4: '游泳',
        5: '滑雪',
        6: '训练',
        7: '室内骑行',
        8: '虚拟骑行'
    }

    print(f"准备下载: 账号={args.user}, 运动类型={sport_map.get(args.sport)}, 年份={args.year}, 月份={args.month}, 文件类型={args.type}, 全量={args.all}")

    # 2. 实例化下载类
    downloader = XZDownloadCyclingRecord(
        account=args.user,
        password=args.password,
        year=args.year,
        month=args.month,
        _all=args.all,
        target_dir=args.output,
        record_type=args.type,
        sport=args.sport
    )

    # 3. 启动下载（带上命令行控制的并发数和间隔）
    downloader.start(max_workers=args.workers, gap=args.gap)

if __name__ == '__main__':
    # demo() # 测试
    main()
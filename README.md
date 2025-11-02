[![Downloads](https://static.pepy.tech/personalized-badge/momonga?period=total&units=none&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/momonga)

<img src="https://raw.githubusercontent.com/nbtk/momonga/refs/heads/main/logo.png" width="256">

# Momonga
Python Route B Library: A Communicator for Low-voltage Smart Electric Energy Meters

# Description
MomongaはBルートサービスを利用してスマートメーターと通信するライブラリです。ターゲットデバイスはROHM社製Wi-SUNモジュールBP35C2または互換品です。

# Tested Devices
- ラトックシステム RS-WSUHA-P
- テセラ・テクノロジー RL7023 Stick-D/DSS

# Note
- Momongaは`WOPT 01\r`コマンドを実行して、Wi-SUNモジュールがUDPパケットのペイロードをASCIIフォーマットで出力するように設定します。注意: WOPTコマンドは実行回数に制限があるので初回のみ実行し、その設定はWi-SUNモジュールに保存されます。
- 一部のWi-SUNモジュールでは`ROPT`コマンドが`FAIL ER04`を返しサポートされません。その場合MomongaはASCII出力で動作していると仮定し、`WOPT`コマンドを実行せずに処理を継続します。

# Installation
```shell
$ pip install momonga
```

# Simple Example
下記のコードはPANAセッションを確立し、瞬時電力計測値を取得して表示します。PANのスキャンは最大で約２分、セッション確立は最大で約１分かかります。
BルートID、パスワード、デバイスファイルへのパスは適宜変更してください。
```python3
import momonga
import time

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

with momonga.Momonga(rbid, pwd, dev) as mo:
    while True:
        res = mo.get_instantaneous_power()
        print('%0.1fW' % res)
        time.sleep(60)
```

### Arguments
- rbid: BルートID
- pwd: Bルートパスワード
- dev: Wi-SUNモジュールのデバイスファイルへのパス
- baudrate: シリアル通信のボーレート(デフォルト: 11520)

### Return Value
- mo: Momongaクラスのインスタンス

# Logging
Momongaには下記のロガーがあります。

## momonga.logger
ECHONET Liteスマートメータークラスを抽象化したレイヤのログ

## momonga.session_manager_logger
PANAセッション管理レイヤのログ

## momonga.sk_wrapper_logger
Wi-SUNモジュールとの通信ログ

## ログを有効にした例
```python3
import momonga
import time
import logging

log_fmt = logging.Formatter('%(asctime)s | %(levelname)s | %(name)s - %(message)s')
log_hnd = logging.StreamHandler()
log_hnd.setFormatter(log_fmt)
momonga.logger.addHandler(log_hnd)
momonga.logger.setLevel(logging.DEBUG)
momonga.session_manager_logger.addHandler(log_hnd)
momonga.session_manager_logger.setLevel(logging.DEBUG)
momonga.sk_wrapper_logger.addHandler(log_hnd)
momonga.sk_wrapper_logger.setLevel(logging.DEBUG)

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

with momonga.Momonga(rbid, pwd, dev) as mo:
    while True:
        res = mo.get_instantaneous_power()
        print('%0.1fW' % res)
        time.sleep(60)
```

# Exception
主な例外は下記です。

## momonga.MomongaSkScanFailure
PANをスキャンしたが見つからなかったときに送出される。スマートメーターと通信できるロケーションか、またBルートIDが正しく設定されているかを確認し、再試行すること。

## momonga.MomongaSkJoinFailure
PANAセッションを確立できなかったときに送出される。BルートIDとパスワードを確認し、再試行すること。

## momonga.MomongaNeedToReopen
スマートメーターに対してコマンドを送信できなかったなどの理由で、スマートメーターに再接続が必要なときに送出される。

## momonga.MomongaResponseNotPossible
スマートメーターがリクエストしたEPC (ECHONET Property Code) をサポートしていなかったとき送出される。スマートメーターに対して複数のEPCを同時に発行したとき、ひとつでもサポートされていないEPCがあるとこのエクセプションが送出される。スマートメーターがサポートしているEPCはmomonga.set_properties_to_get_values()、momonga.get_properties_to_get_values()で取得できる。

## 例外を捕捉する例
```python3
import momonga
import time
import sys

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

while True:
    try:
        with momonga.Momonga(rbid, pwd, dev) as mo:
            while True:
                res = mo.get_instantaneous_power()
                print('%0.1fW' % res)
                time.sleep(60)
    except (momonga.MomongaSkScanFailure,
            momonga.MomongaSkJoinFailure,
            momonga.MomongaNeedToReopen) as e:
        print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
        continue
```

# Transmission Restriction
下記のイベントが発生したときMomongaはスマートメーターに対するコマンドの送信をブロッキングします。
1. PANAセッションのライフタイムが近づきWi-SUNモジュールが自動再認証を試みているとき
2. 送信データ量が規定値に達しWi-SUNモジュールが送信制限しているとき
3. 何らかの理由でシリアルデバイスとの通信がブロッキングされたとき

したがって開発者はデータ設定または取得関数を呼び出したあと即座に応答が返ってこない可能性を考慮してください。

# Consideration
- 送信がブロッキングされるなど諸条件により関数呼び出しのあと応答が即座に返らないことがあるため、`momonga.get_historical_cumulative_energy_1()`は呼び出したときに期待した履歴の日付と結果の日付に齟齬が生じる可能性があることに注意してください。特にこの関数は日を跨ぐタイミングで実行すべきではありません。

# API
## momonga.Momonga(rbid: str, pwd: str, dev: str, baudrate: int = 115200, reset_dev: bool = True)
Momongaクラスのインスタンス化。
### Arguments
- rbid: BルートID
- pwd: Bルートパスワード
- dev: デバイスファイルへのパス
- baudrate: シリアル通信のボーレート
- reset_dev: momonga.open()を実行するときSKRESETコマンドを実行するかどうか

## momonga.open()
PANをスキャンし、PANAセッションの確立を行う。　
### Arguments
- Void
### Return Value
- None

## momonga.close()
PANAセッションを終了する。
### Arguments
- Void
### Return Value
- None

## momonga.get_operation_status()
スマートメーターの状態を取得する。
### Arguments
- Void
### Return Value
- bool | None: スマートメーターの状態 (True: オン, False: オフ, None: 不明)

## momonga.get_installation_location()
### Arguments
- Void
### Return Value
- str: スマートメーターの設置場所
 
e.g.
```python3
'garden/perimeter 1'
```
 
## momonga.get_standard_version()
### Arguments
- Void
### Return Value
- str: 規格バージョン

e.g.
```python3
'F.0'
```

## momonga.get_fault_status()
### Arguments
- Void
### Return Value
- bool | None: スマートメーターの異常発生状態 (True: 異常有, False: 異常無, None: 不明)

## momonga.get_manufacturer_code()
### Arguments
- Void
### Return Value
- bytes: 3バイトのメーカーコード

## momonga.get_serial_number()
### Arguments
- Void
### Return Value
- str: 製造番号

## momonga.get_current_time_setting()
### Arguments
- Void
### Return Value
- datetime.time: 現在時刻設定

## momonga.get_current_date_setting()
### Arguments
- Void
### Return Value
- datetime.date: 現在年月日設定

## momonga.get_properties_for_status_notification()
### Arguments
- Void
### Return Value
- set: 状変アナウンスプロパティマップ (monongaは状変アナウンスをサポートしていない)
```python3
{<EchonetPropertyCode.operation_status: 128>,
 <EchonetPropertyCode.installation_location: 129>,
 <EchonetPropertyCode.fault_status: 136>}
```

## momonga.get_properties_to_set_values()
### Arguments
- Void
### Return Value
- set: Setプロパティマップ
```python3
{<EchonetPropertyCode.installation_location: 129>,
 <EchonetPropertyCode.day_for_historical_data_1: 229>,
 <EchonetPropertyCode.time_for_historical_data_2: 237>}
```

## momonga.get_properties_to_get_values()
### Arguments
- Void
### Return Value
- set: Getプロパティマップ
```python3
{<EchonetPropertyCode.operation_status: 128>, <EchonetPropertyCode.installation_location: 129>,
 <EchonetPropertyCode.standard_version_information: 130>, <EchonetPropertyCode.fault_status: 136>,
 <EchonetPropertyCode.manufacturer_code: 138>, <EchonetPropertyCode.serial_number: 141>,
 <EchonetPropertyCode.current_time_setting: 151>, <EchonetPropertyCode.current_date_setting: 152>,
 <EchonetPropertyCode.properties_for_status_notification: 157>, <EchonetPropertyCode.properties_to_set_values: 158>,
 <EchonetPropertyCode.properties_to_get_values: 159>, <EchonetPropertyCode.coefficient_for_cumulative_energy: 211>,
 <EchonetPropertyCode.number_of_effective_digits_for_cumulative_energy: 215>,
 <EchonetPropertyCode.measured_cumulative_energy: 224>, <EchonetPropertyCode.unit_for_cumulative_energy: 225>,
 <EchonetPropertyCode.historical_cumulative_energy_1: 226>, <EchonetPropertyCode.measured_cumulative_energy_reversed: 227>,
 <EchonetPropertyCode.historical_cumulative_energy_1_reversed: 228>, <EchonetPropertyCode.day_for_historical_data_1: 229>,
 <EchonetPropertyCode.instantaneous_power: 231>, <EchonetPropertyCode.instantaneous_current: 232>,
 <EchonetPropertyCode.cumulative_energy_measured_at_fixed_time: 234>,
 <EchonetPropertyCode.cumulative_energy_measured_at_fixed_time_reversed: 235>,
 <EchonetPropertyCode.historical_cumulative_energy_2: 236>, <EchonetPropertyCode.time_for_historical_data_2: 237>}
```

## momonga.get_route_b_id()
Bルート識別番号を取得する。 
### Arguments
- Void
### Return Value
- dict: {'manufacturer code': manufacturer_code, 'authentication id': authentication_id}

## momonga.get_coefficient_for_cumulative_energy()
積算電力量計測値、履歴を実使用量に換算する係数を取得する。Momongaが出力する結果には適宜この値が乗じられている。
### Arguments
- Void
### Return Value
- int: 係数

## momonga.get_number_of_effective_digits_for_cumulative_energy()
積算電力量計測値の有効桁数を取得する。
- Void
### Return Value
- int: 有効桁数

## momonga.get_measured_cumulative_energy(reverse: bool = False)
積算電力量計測値を取得する。
### Arguments
- reverse: Trueのとき逆方向の積算電力量を取得する
### Return Value
- int | float: 積算電力量(kWh)

## momonga.get_unit_for_cumulative_energy()
積算電力量計測値、履歴の乗率を取得する。Momongaが出力する結果には適宜この値が乗じられている。
### Arguments
- Void
### Return Value
- int | float: 積算電力量の乗率

## momonga.get_historical_cumulative_energy_1(day: int = 0, reverse: bool = False)
積算電力量計測値履歴1を取得する。
### Arguments
- day: 積算履歴収集日(0:当日、1~:前日の日数)
- reverse: Trueのとき逆方向の積算電力量を取得する
### Return Value
- list: 収集日時と積算電力量(kWh)

e.g.
```python3
[{'timestamp': datetime.datetime,
  'cumulative energy': int | float | None}]
```
注意: 収集日時はスマートメーター側で設定されたものではなくMomonga自身が設定しているため、実行中に日を跨ぐと収集日時に齟齬が生じる可能性がある。

## momonga.set_day_for_historical_data_1(day: int = 0)
積算履歴収集日1を設定する。
### Arguments
- day: 積算履歴収集日(0:当日、1~:前日の日数)
### Return Value
- None

## momonga.get_day_for_historical_data_1()
積算履歴収集日1を設定する。
### Arguments
- Void
### Return Value
- int: 積算履歴収集日1

## momonga.get_instantaneous_power()
瞬時電力計測値を取得する。
### Arguments
- Void
### Return Value
- float: 瞬時電力測定値(W)

## momonga.get_instantaneous_current()
瞬時電流計測値を取得する。
### Arguments
- Void
### Return Value
- dict: R相瞬時電流(A)とT相瞬時電流(A)

e.g.
```python3
{'r phase current': float,
 't phase current': float}
```

## momonga.get_cumulative_energy_measured_at_fixed_time(reverse: bool = False)
定時積算電力量計測値を取得する。
### Arguments
- reverse: Trueのとき逆方向の積算電力量を取得する
### Return Value
- dict: 収集日時と積算電力量(kWh)

e.g.
```python3
{'timestamp': datetime.datetime,
 'cumulative energy': int | float}
```

## momonga.get_historical_cumulative_energy_2(timestamp: datetime.datetime = None, num_of_data_points: int = 12)
積算履歴収集日時、収集コマ数ならびに積算電力量の計測結果履歴を、正・逆 30 分毎のデータで過去最大6時間分取得する。
### Arguments
- timestamp: 収集日時 (Noneのときは現時刻)
- num_of_data_points: 収集コマ数 1~12
### Return Value
- list: 収集日時と正方向および逆方向の積算電力量(kWh)

e.g.
```python3
[{'timestamp': datetime.datetime,
  'cumulative energy': {'normal direction': int | float | None,
                        'reverse direction': int | float | None}}]
```

## momonga.set_time_for_historical_data_2(timestamp: datetime.datetime, num_of_data_points: int = 12)
積算履歴収集日時ならびに収集コマ数を設定する。
### Arguments
- timestamp: 収集日時 (Noneのときは現時刻)
- num_of_data_points: 収集コマ数
### Return Value
- None

## momonga.get_time_for_historical_data_2()
積算履歴収集日時ならびに収集コマ数を取得する。
### Arguments
- Void
### Return Value
- dict: 収集日時と収集コマ数

e.g.
```python3
{'timestamp': datetime.datetime | None,
 'number of data points': int}
```

## momonga.get_historical_cumulative_energy_3(timestamp: datetime.datetime = None, num_of_data_points: int = 10)
積算履歴収集日時、収集コマ数ならびに積算電力量の計測結果履歴を、正・逆 1 分毎のデータで過去最大6時間分取得する。
### Arguments
- timestamp: 収集日時 (Noneのときは現時刻)
- num_of_data_points: 収集コマ数 1~10
### Return Value
- list: 収集日時と正方向および逆方向の積算電力量(kWh)

e.g.
```python3
[{'timestamp': datetime.datetime,
  'cumulative energy': {'normal direction': int | float | None,
                        'reverse direction': int | float | None}}]
```

## momonga.set_time_for_historical_data_3(timestamp: datetime.datetime, num_of_data_points: int = 10)
積算履歴収集日時ならびに収集コマ数を設定する。
### Arguments
- timestamp: 収集日時 (Noneのときは現時刻)
- num_of_data_points: 収集コマ数
### Return Value
- None

## momonga.get_time_for_historical_data_3()
積算履歴収集日時ならびに収集コマ数を取得する。
### Arguments
- Void
### Return Value
- dict: 収集日時と収集コマ数

e.g.
```python3
{'timestamp': datetime.datetime | None,
 'number of data points': int}
```

## momonga.request_to_get()
複数のEchonetプロパティを一括送信するためのインタフェース
### Arguments
- properties: EchonetPropertyCodeの集合
### Return Value
- dict: EchonetPropertyCodeと結果

e.g.
```python3
from momonga import EchonetPropertyCode as EPC

with momonga.Momonga(rbid, pwd, dev) as mo:
    while True:
        res = mo.request_to_get({
            EPC.instantaneous_power,
            EPC.instantaneous_current,
            EPC.measured_cumulative_energy,
        })

        for epc, r in res.items():
            print(f'epc: {epc.name}, result: {r}')

        time.sleep(60)
```

## Feedback
イシュー報告、プルリクエスト、コメント等、なんでもよいのでフィードバックお待ちしています。星をもらうと開発が活発になります。<br>
If you have any problems, questions, suggestions or comments, please let me know. It can be in English. All feedback is welcome.

## Credits
This project was originally developed during my time at BitMeister Inc., with support and resources generously provided by the company. I'm really thankful for the people and the environment that helped make it happen.  It's now maintained independently.

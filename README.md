# Momonga
Python Route B Library: A Comunicator for Low-voltage Smart Electric Energy Meters

# Discription
MomongaはBルートサービスを利用してスマートメーターと通信するライブラリです。ターゲットデバイスはROHM社製Wi-SUNモジュールBP35C2を搭載したラトックシステムRS-WSUHA-Pです。

# Preparation
Momongaを使用するためには事前にWi-SUNモジュールにシリアル接続し`WOPT 01\r`コマンドを実行してUDPパケットのペイロードをASCIIフォーマットで出力するように設定してください。注意: WOPTコマンドは実行回数に制限がありますので初回のみ実行してください。設定は保存されます。

# Installation
```shell
$ pip install momonga
```

# Simple Example
下記のコードはPANAセッションを確立し、瞬時電力計測値を取得して表示します。PANのスキャンは最大で約２分、セッション確立は最大で約１分かかります。
BルートID、パスワード、デバイスファイルへのパスは適宜変更してください。
```python3
import momonga


rbid = 'SET A ROUTE B ID'
pwd  = 'SET A ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

with momonga.Momonga(rbid, pwd, dev) as mo:
    res = momonga.get_instantaneous_power()
    print('%0.1fW' % res)
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

rbid = 'SET A ROUTE B ID'
pwd  = 'SET A ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

with momonga.Momonga(rbid, pwd, dev) as mo:
    res = momonga.get_instantaneous_power()
    print('%0.1fW' % res)
```

# Exception
主な例外は下記です。

## momonga.MomongaSkScanFailure
PANをスキャンしたが見つからなかったときに送出される。スマートメーターと通信できるロケーションか、またBルートIDが正しく設定されているかを確認し、再試行すること。

## momonga.MomongaSkJoinFailure
PANAセッションを確立できなかったときに送出される。BルートIDとパスワードを確認し、再試行すること。

## momonga.MomongaNeedToReopen
スマートメーターに対してコマンドを送信できなかったなどの理由で、スマートメーターに再接続が必要なときに送出される。

## 例外を補足する例
```python3
import momonga
import sys


rbid = 'SET A ROUTE B ID'
pwd  = 'SET A ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

while True:
    try:
        with momonga.Momonga(rbid, pwd, dev) as mo:
            res = momonga.get_instantaneous_power()
            print('%0.1fW' % res)
            break
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
- bool: スマートメーターの状態 (True: オン False: オフ None: 不明)

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
- float: 積算電力量(kWh)

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
  'cumulative energy': float}]
```
注意: 収集日時はスマートメーター側で設定されたものではなくMomonga自身が設定しているため、実行中に日を跨ぐと収集日時に齟齬が生じる可能性がある。

## momonga.set_day_for_which_to_retrieve_historical_data_1(day: int = 0)
積算履歴収集日1を設定する。
### Arguments
- day: 積算履歴収集日(0:当日、1~:前日の日数)
### Return Value
- None

## momonga.get_day_for_which_to_retrieve_historical_data_1()
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
{'r phase current': float, 't phase current': float}
```

## momonga.get_cumulative_energy_measured_at_fixed_time(reverse: bool = False)
定時積算電力量計測値を取得する。
### Arguments
- reverse: Trueのとき逆方向の積算電力量を取得する
### Return Value
- dict: 収集日時と積算電力量(kWh)

e.g.
```python3
{'datetime': datetime.datetime,
 'cumulative energy': float}
```

## momonga.get_historical_cumulative_energy_2(timestamp: datetime.datetime = datetime.datetime.now(), num_of_data_points: int = 12)
積算履歴収集日時、収集コマ数ならびに積算電力量の計測結果履歴を、正・逆 30 分毎のデータで過去最大6時間分取得する。
### Arguments
- timestamp: 収集日時
- num_of_data_points: 収集コマ数 1~12
### Return Value
- list: 収集日時と正方向および逆方向の積算電力量(kWh)

e.g.
```python3
[{'timestamp': datetime.datetime,
  'cumulative energy': {'normal direction': float,
                        'reverse direction': float}}]
```

## momonga.set_time_for_which_to_retrieve_historical_data_2(timestamp: datetime.datetime, num_of_data_points: int = 12)
積算履歴収集日時ならびに収集コマ数を設定する。
### Arguments
- timestamp: 収集日時
- num_of_data_points: 収集コマ数
### Return Value
- None

## momonga.get_time_for_which_to_retrieve_historical_data_2()
積算履歴収集日時ならびに収集コマ数を取得する。
### Arguments
- Void
### Return Value
- dict: 収集日時と収集コマ数

e.g.
```python3
{'timestamp': datetime,
 'number of data points': int}
```

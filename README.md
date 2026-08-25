[![Downloads](https://static.pepy.tech/personalized-badge/momonga?period=total&units=none&left_color=grey&right_color=blue&left_text=Downloads)](https://pepy.tech/project/momonga)

<img src="https://raw.githubusercontent.com/nbtk/momonga/refs/heads/main/logo.png" width="256">

# Momonga
Python Route B Library: A Communicator for Low-voltage Smart Electric Energy Meters

# Description
MomongaはBルートサービスを利用してスマートメーターと通信するPythonライブラリ。ターゲットデバイスはROHM社製Wi-SUNモジュールBP35C2または互換品。

# Tested Devices
- ラトックシステム RS-WSUHA-P
- テセラ・テクノロジー RL7023 Stick-D/DSS
- テセラ・テクノロジー RL7023 Stick-D/IPS

# Note
- Momongaは`WOPT 01\r`コマンドを実行して、Wi-SUNモジュールがUDPパケットのペイロードをASCIIフォーマットで出力するように設定する。注意: WOPTコマンドは実行回数に制限があるので初回のみ実行し、その設定はWi-SUNモジュールに保存される。
- 一部のWi-SUNモジュールでは`ROPT`コマンドが`FAIL ER04`を返しサポートされない。その場合MomongaはASCII出力で動作していると仮定し、`WOPT`コマンドを実行せずに処理を継続する。
- メソッドは物理量に即して命名しており、ECHONETの英語版ドキュメントの表記とは必ずしも一致しない。対応するEPCを調べる場合はメソッド名ではなくEPCのコード値で検索すること。
- 送信ブロッキングなど諸条件により応答が遅延することがあるため、`get_historical_cumulative_energy_1()`は日を跨ぐタイミングで実行すべきではない。
- プロパティの定義（EDTの長さ、乗率、データなしを表すコードなど）は、ECHONET ConsortiumのMachine Readable Appendix **dataVersion 1.3.2 / release R（2026-06-12、配布物名 MRA_v1.4.0）** の低圧スマート電力量メータクラス（0x0288）および機器オブジェクトスーパークラス（0x0000）に照合している。

# Installation
```shell
$ pip install momonga
```

# Simple Example
下記のコードはPANAセッションを確立し、瞬時電力計測値を取得して表示する。既定値ではPANのスキャンに最大で約２分、PANAセッションの確立に最大で約２分かかる（`scan_retries`、`join_retries`で変わる）。
BルートID、パスワード、デバイスファイルへのパスは適宜変更すること。
```python3
import momonga
import time

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0' # in a case of RaspberryPi OS

with momonga.Momonga(rbid, pwd, dev) as mo:
    while True:
        res = mo.get_instantaneous_power()
        print('no data' if res is None else '%0.1fW' % res)
        time.sleep(60)
```

### Arguments
- rbid: BルートID
- pwd: Bルートパスワード
- dev: Wi-SUNモジュールのデバイスファイルへのパス
- baudrate: シリアル通信のボーレート(デフォルト: 115200)

### Return Value
- mo: Momongaクラスのインスタンス

# Logging
Momongaには下記のロガーがある。

## momonga.logger
ECHONET Liteスマートメータークラスを抽象化したレイヤのログ

## momonga.session_manager_logger
PANAセッション管理レイヤのログ

## momonga.sk_wrapper_logger
Wi-SUNモジュールとの通信ログ

## Logging Example
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
        print('no data' if res is None else '%0.1fW' % res)
        time.sleep(60)
```

# Exception
主な例外は下記のとおり。

## momonga.MomongaError
Momongaが送出する例外すべての基底クラス。原因を問わず「Momonga由来の失敗」をまとめて捕まえたい場合に使う。

## momonga.MomongaRuntimeError
`momonga.open()`を呼ぶ前にリクエストを発行したなど、使い方が誤っているときに送出される。`RuntimeError`のサブクラスでもある。再接続では直らないので、捕捉して再試行するのではなく呼び出し方を直すこと。

## momonga.MomongaConnectionFailure
セッションが乗っている無線通信やデバイスといったインフラに問題があるときに送出される例外の基底クラス。下記の4つがこれを継承する。原因は違うが、呼び出し側の対応は「待ってから再試行する」で共通なので、個別に列挙せずこれを捕捉すればよい。

- `MomongaSkScanFailure` — PANが見つからない
- `MomongaSkJoinFailure` — PANAセッションを確立できない
- `MomongaTimeoutError` — Wi-SUNモジュールが応答しない
- `MomongaIOError` — デバイスファイルやUSBドングルの失敗

この基底クラス自身が直接送出されることはない。捕捉のためだけに存在する。

対になるのが`MomongaNeedToReopen`で、セッションに異常があるときに送出される。セッションを張り直せば直る可能性が高い。

なお`reopen_delays`が働くのはリクエスト中だけで、`momonga.open()`は対象外。対象になるのは`MomongaNeedToReopen`と`MomongaIOError`である。

## momonga.MomongaSkScanFailure
PANをスキャンしたが見つからなかったときに送出される。スマートメーターと通信できるロケーションか、またBルートIDが正しく設定されているかを確認し、再試行すること。

## momonga.MomongaSkJoinFailure
PANAセッションを確立できなかったときに送出される。BルートIDとパスワードを確認し、再試行すること。

## momonga.MomongaNeedToReopen
スマートメーターに対してコマンドを送信できなかったなどの理由で、スマートメーターに再接続が必要なときに送出される例外の基底クラス。下記の3つがこれを継承する。原因は違うが、呼び出し側の対応は「セッションを張り直す」で共通なので、個別に列挙せずこれを捕捉すればよい。

- `MomongaXmitTimeout` — 制限時間内に送信権を得られない
- `MomongaSkCommandBusy` — 別のSKコマンドが実行中で開始できない
- `MomongaSkCommandCancelled` — `momonga.close()`が実行中のSKコマンドを打ち切った

この基底クラス自身も、応答が得られない、パケット配信元が止まった、といった場合に直接送出される。

対になるのが`MomongaConnectionFailure`で、セッションが乗っているインフラに問題があるときに送出される。

## momonga.MomongaXmitTimeout
`momonga.xmit_timeout`で指定した秒数のあいだにパケットを送信できなかったときに送出される。`MomongaNeedToReopen`のサブクラス。

## momonga.MomongaSkCommandBusy
別のSKコマンドが実行中で、制限時間内にコマンドを開始できなかったときに送出される。`MomongaNeedToReopen`のサブクラス。

## momonga.MomongaSkCommandCancelled
`momonga.close()`がセッションを閉じるために、実行中のSKコマンドを打ち切ったときに送出される。`MomongaNeedToReopen`のサブクラス。

## momonga.MomongaTimeoutError
`momonga.open()`の実行中にWi-SUNモジュールが応答しなかったときに送出される。デバイスファイルのパスと、モジュールが正しく接続されているかを確認すること。

この例外は`TimeoutError`のサブクラスでもある。Python 3.11以降`asyncio.TimeoutError`は`TimeoutError`と同じクラスなので、`await`を`asyncio.wait_for()`で囲んで`asyncio.TimeoutError`を捕捉していると、自分が指定した待ち時間が尽きた場合と区別できない。区別が必要なら`MomongaTimeoutError`を先に捕捉すること。なお`MomongaXmitTimeout`と`MomongaSkCommandBusy`は`TimeoutError`を継承していないので、この問題は起きない。

## momonga.MomongaIOError
シリアルデバイスそのものが失敗したときに送出される。デバイスファイルが存在しない、権限がない、USBドングルが抜けた、といった場合。pyserialの`SerialException`やOSの`FileNotFoundError`はこの例外に包まれるので、Momongaを使う側がpyserialをimportする必要はない。`__cause__`に元の例外が入っている。

この例外は`OSError`のサブクラスでもある。`reopen_delays`を指定していれば自動再接続の対象になる。

## momonga.MomongaResponseNotPossible
スマートメーターがリクエストしたEPC (ECHONET Property Code) をサポートしていなかったとき送出される。スマートメーターに対して複数のEPCを同時に発行したとき、ひとつでもサポートされていないEPCがあるとこのエクセプションが送出される。スマートメーターがサポートしているEPCは`momonga.get_properties_to_set_values()`、`momonga.get_properties_to_get_values()`で取得できる。

## momonga.MomongaResponseNotExpected
スマートメーターの応答が読めなかったときに送出される。宣言された長さがプロパティに足りない、プロパティコードが要求と一致しない、といった場合。セッションが失われたわけではないので、次のリクエストは通ることが多い。通知（`get_notification()`）ではこの例外は送出されず、読めなかったプロパティの値が生のバイト列のまま返り、警告がログに出る。

## Exception Handling Example
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
                try:
                    res = mo.get_instantaneous_power()
                except momonga.MomongaResponseNotExpected as e:
                    # one response that could not be read, not a lost session
                    print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
                else:
                    print('no data' if res is None else '%0.1fW' % res)
                time.sleep(60)
    except (momonga.MomongaConnectionFailure,
            momonga.MomongaNeedToReopen) as e:
        # a session that could not be built, and one that was lost.
        # MomongaSkScanFailure, MomongaSkJoinFailure, MomongaTimeoutError and
        # MomongaIOError are the first; MomongaXmitTimeout,
        # MomongaSkCommandBusy and MomongaSkCommandCancelled are the second
        print('%s: %s' % (type(e).__name__, e), file=sys.stderr)
        continue
```

`MomongaResponseNotPossible`と`MomongaRuntimeError`を捕捉していないのは意図的である。前者はスマートメーターがそのEPCをサポートしていないという意味で、後者は使い方の誤りなので、どちらも再接続では直らない。握りつぶすと原因が見えないまま無限ループになる。

## No Handler At All
`reopen_delays`で無限に再接続を試み、`scan_retries`と`join_retries`を現実的な範囲で大きな値に設定したうえで、稀な例外は補足せずギブアップしてプロセスを終了するような選択も取り得る。

```python3
import momonga
import time

from itertools import chain, repeat

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0'

def backoff():
    return chain([60.0, 120.0, 300.0], repeat(600.0))

with momonga.Momonga(rbid, pwd, dev, reopen_delays=backoff,
                     scan_retries=6, join_retries=15) as mo:
    while True:
        res = mo.get_instantaneous_power()
        print('no data' if res is None else '%0.1fW' % res)
        time.sleep(60)
```

# Transmission Restriction
下記のイベントが発生したときMomongaはスマートメーターに対するコマンドの送信をブロッキングする。
1. PANAセッションのライフタイムが近づきWi-SUNモジュールが自動再認証を試みているとき
2. 送信データ量が規定値に達しWi-SUNモジュールが送信制限しているとき

したがって開発者はデータ設定または取得関数を呼び出したあと即座に応答が返ってこない可能性を考慮すること。

# No Data

スマートメーターが値を持たないとき、ECHONETは数値のかわりに規定のコードを返す（積算電力量は0xFFFFFFFE、瞬時電力は0x7FFFFFFE、瞬時電流は相ごとに0x7FFE）。Momongaはこれらを`None`として返す。

したがって計測値を返す関数は`None`を返しうる。値ごとに`None`を判定してから使うこと。履歴系のように複数の値を含む結果では、値ごとに独立して`None`になる。

# Notification
スマートメーターは定時積算電力量（EPC: 0xEA/0xEB）を毎時0分・30分から5分以内に自動通知します（INF/INFC）。
Momongaはこれらの通知を`get_notification()`で受け取れる。`AsyncMomonga`では`notifications()`も使える。

INFCを受信した場合、Momongaは自動的にINFC_Resを送信する。この送信はベストエフォートで、送信ブロッキング中などで15秒以内に送出できないときは送信を諦める。通知そのものの受け取りは`timeout`に指定した時間を超えない。

## Notification Example
```python3
import momonga

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0'

with momonga.Momonga(rbid, pwd, dev) as mo:
    while True:
        notif = mo.get_notification(timeout=2400)
        if notif is None:
            continue  # timed out
        esv = notif['esv']
        for epc, value in notif['properties'].items():
            print(f'ESV: {esv.name}, EPC: {epc}, value: {value}')
```

## Async Notification Example
```python3
import asyncio
import momonga

rbid = 'SET YOUR ROUTE B ID'
pwd  = 'SET YOUR ROUTE B PASSWORD'
dev  = '/dev/ttyUSB0'

async def main():
    async with momonga.AsyncMomonga(rbid, pwd, dev) as mo:
        async for notif in mo.notifications(timeout=2400):
            for epc, value in notif['properties'].items():
                print(f'EPC: {epc}, value: {value}')

asyncio.run(main())
```

# API
## momonga.Momonga(rbid: str, pwd: str, dev: str, baudrate: int = 115200, reset_dev: bool = True, reopen_delays: Iterable[float] | Callable[[], Iterable[float]] | None = None, scan_retries: int = 3, join_retries: int = 3)
Momongaクラスのインスタンス化。
### Arguments
- rbid: BルートID
- pwd: Bルートパスワード
- dev: デバイスファイルへのパス
- baudrate: シリアル通信のボーレート
- reset_dev: `momonga.open()`を実行するときSKRESETコマンドを実行するかどうか
- reopen_delays: `MomongaNeedToReopen` 発生時に再接続を試みるまでの待機秒数の列。`None` の場合は自動再接続しない。再接続が必要になるたびに列の先頭から使われる。最初の`momonga.open()`は対象外である。接続そのものの失敗（`MomongaConnectionFailure`とその4つのサブクラス）はここを通らず即座に送出されるので、無人運転では呼び出し側で待ってから再試行すること。待たずに繰り返すと電波を使うだけで状況は変わらない。
- scan_retries: PANのスキャンを繰り返す回数。1以上。使い切ると`MomongaSkScanFailure`を送出する
- join_retries: PANAセッションの確立を試みる回数。1以上。1回あたり最大約40秒。使い切ると`MomongaSkJoinFailure`を送出する

e.g.
```python3
from itertools import chain, repeat

# reconnect up to 3 times, 10 minutes apart
momonga.Momonga(rbid, pwd, dev, reopen_delays=[600.0, 600.0, 600.0])

# reconnect indefinitely, 10 minutes apart
momonga.Momonga(rbid, pwd, dev, reopen_delays=repeat(600.0))

# back off: a minute, then two, then five, then every ten indefinitely
def backoff():
    return chain([60.0, 120.0, 300.0], repeat(600.0))

momonga.Momonga(rbid, pwd, dev, reopen_delays=backoff)

# try harder to join before giving up (scanning is left at its default)
momonga.Momonga(rbid, pwd, dev, join_retries=15)
```

`reopen_delays`は再接続のたびに列の先頭から使われる。リストでも、一度しか回せないイテレータでも同じで、後者は内部で再生される。ただしその再生はひとつのインスタンスの中だけで成り立つ。**同じイテレータを別のインスタンスに渡すと前回の続きから始まる**ので、上のバックオフを`chain(...)`のまま渡すと最初のセッションだけバックオフが機能し、以降は10分固定になる。コーラブルを渡せば毎回新しい列が作られ、インスタンスをまたいでもバックオフが機能する。

## momonga.xmit_retries
ひとつのリクエストを送り直す回数の上限。使い切ると`MomongaNeedToReopen`を送出する。既定値は12。

## momonga.recv_timeout
1回の送信に対して応答を待つ秒数。超えると送り直す。既定値は12。

スマートメーターが応答しないとき、ひとつのリクエストを諦めるまでにかかる時間はおおむね`momonga.xmit_retries`と`momonga.recv_timeout`の積になる。既定値では約144秒。ただしこれは応答を待つ時間だけで、送信ブロッキング中の待ち時間は含まない。そちらは`momonga.xmit_timeout`が上限になる。

## momonga.xmit_timeout
ひとつのリクエストが送信権を得るまでに待つ秒数の上限。送信ブロッキングが続いてこの秒数を超えると`MomongaXmitTimeout`を送出する。`MomongaNeedToReopen`のサブクラスなので、`reopen_delays`を指定していれば自動再接続の対象になる。既定値は300。

この上限はリクエスト全体に対して1回分で、`momonga.xmit_retries`の回数だけ繰り返されることはない。`None`を指定すると上限なしになる。

`momonga.open()`が内部で発行するリクエスト（積算電力量の単位と係数の取得）にも同じ上限が適用される。PANのスキャンとPANAセッションの確立は対象外なので、影響を受けるのはこの2リクエストだけである。

## momonga.internal_xmit_interval
momongaが続けて送信するときに空ける秒数。既定値は5。

使われるのは次の2箇所。

- `momonga.open()`のなか。PANAセッション確立の直後と、積算電力量の単位・係数を取得したそれぞれの後（既定値では合計15秒）
- Wi-SUNモジュールが`EVENT 21`で送信失敗を通知したときの、送り直しの前

スマートメーターから応答が返らず`momonga.recv_timeout`で打ち切ったときの送り直しには使われない。この場合は待たずに送り直す。

これらのパラメータはインスタンス化したあとに変更できる。

e.g.
```python3
mo = momonga.Momonga(rbid, pwd, dev)
mo.recv_timeout = 30  # longer, for a meter that answers slowly
mo.xmit_retries = 3   # give up sooner and let reopen_delays rebuild the session
mo.xmit_timeout = 300 # give up after five minutes of blocked transmission
```

## momonga.open()
PANをスキャンし、PANAセッションの確立を行う。　

所要時間はPANのスキャンとPANAセッションの確立が支配的で、電波状況によって数十秒から数分かかる。確立できなかった場合も、`MomongaSkScanFailure`または`MomongaSkJoinFailure`を送出するまでに同程度の時間がかかる。既定値での上限はスキャンが約2分（17秒＋35秒＋69秒）、確立が約2分（40秒×3回）で、`scan_retries`と`join_retries`を増やせばそのぶん延びる。`reopen_delays`で再接続の間隔を決めるときは、1回の再接続にこの時間が加わることを見込むこと。
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

## momonga.reopen()
PANAセッションを一度終了し、張り直す。`MomongaNeedToReopen`を受け取ったあとに手動で再接続するときに使う。`reopen_delays`を指定している場合は自動で呼ばれるので、通常は直接呼ぶ必要はない。

再接続のあいだ`momonga.is_open`はFalseになる。この間に他のスレッドから発行されたリクエストは`MomongaNeedToReopen`となり、`reopen_delays`を指定していれば再接続の完了後に自動で再試行される。`momonga.get_notification()`は再接続の完了を待ってから新しいセッションの通知を返す。
### Arguments
- Void
### Return Value
- None

## momonga.lqi / momonga.rssi
最後にスマートメーターから届いたパケットの受信品質。読み取り専用。

- `momonga.lqi`: 受信品質を表す0-255の値（`int`）
- `momonga.rssi`: 受信電力（`float`、dBm）。`0.275 × lqi - 104.27`で算出

`momonga.open()`を実行した直後や、まだ一度もパケットが届いていないあいだは両方とも`None`。BP35A1系のWi-SUNモジュールはERXUDPに受信品質を含めないため、その場合も`None`のままになる。

セッションを張り直すと`None`に戻る。

e.g.
```python3
with momonga.Momonga(rbid, pwd, dev) as mo:
    while True:
        res = mo.get_instantaneous_power()
        reading = 'no data' if res is None else '%0.1fW' % res
        print('%s (rssi: %s dBm)' % (reading, mo.rssi))
        time.sleep(60)
```

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
- set: 状変アナウンスプロパティマップ。このセットに含まれるEPCの値変化をスマートメーターが自動通知する。通知の受け取りには`get_notification()`（`AsyncMomonga`では`notifications()`も）を使用する
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

## momonga.get_one_minute_measured_cumulative_energy()
1分毎の積算電力量計測値を取得する。
### Arguments
- Void
### Return Value
- dict: 収集日時と正方向および逆方向の積算電力量(kWh)

e.g.
```python3
{'timestamp': datetime.datetime,
 'cumulative energy': {'normal direction': int | float | None,
                       'reverse direction': int | float | None}}
```

## momonga.get_coefficient_for_cumulative_energy()
積算電力量計測値、履歴を実使用量に換算する係数を取得する。Momongaが出力する結果には適宜この値が乗じられている。
### Arguments
- Void
### Return Value
- int: 係数

## momonga.get_number_of_effective_digits_for_cumulative_energy()
積算電力量計測値の有効桁数を取得する。
### Arguments
- Void
### Return Value
- int: 有効桁数

## momonga.get_measured_cumulative_energy(reverse: bool = False)
積算電力量計測値を取得する。
### Arguments
- reverse: Trueのとき逆方向の積算電力量を取得する
### Return Value
- int | float | None: 積算電力量(kWh)。スマートメーターが値を持たないときはNone

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
積算履歴収集日1を取得する。
### Arguments
- Void
### Return Value
- int: 積算履歴収集日1

## momonga.get_instantaneous_power()
瞬時電力計測値を取得する。
### Arguments
- Void
### Return Value
- int | None: 瞬時電力測定値(W)。スマートメーターが値を持たないときはNone

## momonga.get_instantaneous_current()
瞬時電流計測値を取得する。
### Arguments
- Void
### Return Value
- dict: R相瞬時電流(A)とT相瞬時電流(A)。相ごとに、値を持たないときはNone

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
- dict: 収集日時と積算電力量(kWh)。積算電力量は、値を持たないときはNone

e.g.
```python3
{'timestamp': datetime.datetime,
 'cumulative energy': int | float}
```

## momonga.get_historical_cumulative_energy_2(timestamp: datetime.datetime | None = None, num_of_data_points: int = 12)
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
- timestamp: 収集日時
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

## momonga.get_historical_cumulative_energy_3(timestamp: datetime.datetime | None = None, num_of_data_points: int = 10)
積算履歴収集日時、収集コマ数ならびに積算電力量の計測結果履歴を、正・逆 1 分毎のデータで過去最大10分間ぶん取得する。
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
- timestamp: 収集日時
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

## momonga.request_to_set(day_for_historical_data_1: dict | None = None, time_for_historical_data_2: dict | None = None, time_for_historical_data_3: dict | None = None)
複数のEchonetプロパティを一括設定するためのインタフェース。指定した引数だけが1回のリクエストにまとめられる。すべてNoneのときは何も送信しない。
### Arguments
- day_for_historical_data_1: `momonga.set_day_for_historical_data_1()`に渡す引数
- time_for_historical_data_2: `momonga.set_time_for_historical_data_2()`に渡す引数
- time_for_historical_data_3: `momonga.set_time_for_historical_data_3()`に渡す引数
### Return Value
- None

e.g.
```python3
import datetime

with momonga.Momonga(rbid, pwd, dev) as mo:
    mo.request_to_set(
        day_for_historical_data_1={'day': 1},
        time_for_historical_data_2={'timestamp': datetime.datetime.now(),
                                    'num_of_data_points': 12},
    )
```

## momonga.request_to_get(properties: set[EchonetPropertyCode])
複数のEchonetプロパティを一括取得するためのインタフェース。1回のリクエストにまとめて送信する。
### Arguments
- properties: EchonetPropertyCodeの集合
### Return Value
- dict: EchonetPropertyCodeと結果

e.g.
```python3
import time

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

## momonga.get_notification(timeout: int | float | None = None)
スマートメーターからの通知（INF/INFC）を受け取る。INFCを受信した場合はINFC_Resを自動送信する。
### Arguments
- timeout: 待機秒数。Noneのとき通知が届くまでブロッキングする
### Return Value
- dict | None: 通知データ。タイムアウト時はNone

```python3
{'esv': momonga.EchonetServiceCode.inf,
 'properties': {momonga.EchonetPropertyCode.cumulative_energy_measured_at_fixed_time: ...}}
```

### Note: timeout=None is not recommended
PANAセッションが切断された場合でも、スマートメーターやWi-SUNモジュールがEVENTを送出せずに沈黙する状態（電波途絶、スマートメーター電源断など）では、MomongaはPANAセッションの切断を検知できない。その場合`get_notification(timeout=None)`は無期限にブロッキングする。

有限のtimeoutを設定し、`None`が一定回数連続した場合はスマートメーターにコマンドを送信してセッションの疎通を確認することを推奨する。セッションが切断されていれば`MomongaNeedToReopen`が送出される。

```python3
with momonga.Momonga(rbid, pwd, dev) as mo:
    consecutive_timeouts = 0
    while True:
        notif = mo.get_notification(timeout=2400)  # 40 minutes
        if notif is None:
            consecutive_timeouts += 1
            if consecutive_timeouts >= 3:
                mo.get_operation_status()  # raises MomongaNeedToReopen if session is lost
            continue
        consecutive_timeouts = 0
        # process notification
```

# AsyncMomonga
`AsyncMomonga`は`Momonga`の全メソッドを`asyncio`で利用できるラッパークラスである。`Momonga`のブロッキング処理はインスタンスごとに持つスレッドプールで実行されるため、イベントループをブロックしない。プロセスで共有されるデフォルトのexecutorは使わない。

スレッドプールは3つに分かれている。`get_notification()`と`notifications()`は専用プール、`open()`/`close()`/`reopen()`はもう1つの専用プール、それ以外のメソッドは汎用プールを使う。リクエストが何本詰まっていても、通知の`timeout`が守られ、セッションの開閉が待たされないようにするためである。専用プールはそれぞれワーカーを2本持つ。1本は実行中の呼び出し用、もう1本は、キャンセルされて誰も待っていない呼び出しが次の呼び出しを塞がないための予備。

スレッドプールを停止するのは`async with`文を抜けるときだけである。抜けたあとのインスタンスは再利用できない。

`AsyncMomonga.close()`はスマートメーターとのセッションを閉じるだけで、スレッドプールはそのまま残る。`close()`のあとに`open()`を呼んで使い続けられるのはこのためである。`async with`を使わずに`open()`と`close()`だけで使う場合、スレッドプールはプロセスが終わるまで残るが、ワーカーはアイドル状態なのでプロセスの終了を妨げない。

## Note: awaitをキャンセルしても処理は止まらない
`asyncio.wait_for()`やタスクのキャンセルで待つのをやめても、スレッドプールで動いている`Momonga`の処理は最後まで走り続ける。実行中のSKコマンドを途中で捨てるとWi-SUNモジュールの応答とコマンド列が同期を失うため、途中で止める手段は用意していない。

放棄された処理はそのリクエストが終わるまで汎用プールの枠を占有する。占有時間は`Momonga`側の設定で決まる。

- `reopen_delays`を指定していない場合の上限はおおむね`xmit_timeout`と`xmit_retries × recv_timeout`の和。既定値では約7分
- `reopen_delays`に`repeat()`など終わりのない列を渡している場合、放棄されたリクエストは再接続を繰り返していつまでも終わらない

占有時間を短くしたい場合は`xmit_timeout`を下げ、`max_workers`には余裕を持たせること。終わりのない`reopen_delays`を避ければ、占有時間の上限も有限になる。汎用プールが放棄されたリクエストで埋まっても、`async with`文からの退出は専用スレッドで実行されるので待たされない。

e.g.
```python3
async with momonga.AsyncMomonga(rbid, pwd, dev,
                                reopen_delays=[600.0, 600.0, 600.0]) as mo:
    mo.xmit_timeout = 60  # give up after a minute of blocked transmission
```

`get_notification()`と`notifications()`はこの制限を受けない。1秒以下の単位で読み取りを区切っているため、キャンセルしても次の読み取りはすぐ始められる。読み取り済みの通知は次の呼び出しに引き継がれる。ただしキャンセルした読み取りがINFC_Resの送出中だった場合、そのワーカーは最大15秒残る。予備のワーカーがあるので次の読み取りは待たされない。

## momonga.AsyncMomonga(rbid: str, pwd: str, dev: str, baudrate: int = 115200, reset_dev: bool = True, reopen_delays: Iterable[float] | Callable[[], Iterable[float]] | None = None, max_workers: int = 4, scan_retries: int = 3, join_retries: int = 3)
AsyncMomongaクラスのインスタンス化。`max_workers`以外の引数は`Momonga`と同じ。
### Arguments
- max_workers: 汎用プールのワーカー数。通知の読み取りとセッションの開閉は専用スレッドで動くのでこの数には含まれない。リクエストは内部で直列化されるため増やしても速くはならない。既定値は4（実行中のリクエスト1本と、放棄されたリクエストのための予備3本）

`momonga.xmit_retries`、`momonga.recv_timeout`、`momonga.xmit_timeout`、`momonga.internal_xmit_interval`は`AsyncMomonga`のインスタンスにもそのまま設定できる。`momonga.is_open`、`momonga.energy_unit`、`momonga.energy_coefficient`、`momonga.lqi`、`momonga.rssi`は読み取りのみ。

`async with`文による使用を推奨する。

```python3
import asyncio
import momonga

async def main():
    async with momonga.AsyncMomonga(rbid, pwd, dev) as mo:
        power = await mo.get_instantaneous_power()
        print('no data' if power is None else f'{power}W')

asyncio.run(main())
```

## async AsyncMomonga.notifications(timeout: int | float = 60)
通知を非同期ジェネレータとして受け取る。`timeout`秒待って通知がない場合は次の待機に入る（`None`は返さない）。

```python3
async def main():
    async with momonga.AsyncMomonga(rbid, pwd, dev) as mo:
        async for notif in mo.notifications(timeout=2400):
            print(notif)
```

## async AsyncMomonga.get_notification(timeout: int | float | None = None)
同期版`get_notification()`と同じ動作。タイムアウト時は`None`を返す。

## Other Methods
`Momonga`の全メソッドに対応する`async`版が定義されている。`await mo.メソッド名()`の形式で呼び出せる。

## Feedback
イシュー報告、プルリクエスト、コメント等、なんでもよいのでフィードバックを歓迎する。星をもらうと開発が活発になる。<br>
Questions, suggestions, and comments are welcome! Please feel free to write in English.

## Credits
This project was originally developed during my time at BitMeister Inc., with support and resources generously provided by the company. I am really thankful for the people and the environment that helped make it happen. It is now maintained independently.

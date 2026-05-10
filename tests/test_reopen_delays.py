import unittest
from unittest.mock import patch

import momonga


class TestReopenDelays(unittest.TestCase):
    def setUp(self) -> None:
        self.instance = momonga.Momonga('rbid', 'pwd', '/dev/null', reopen_delays=[256.0, 1024.0])

    def test_request_retries_after_reopen(self) -> None:
        request_calls = []

        def fake_request(_esv, _props):
            request_calls.append(1)
            if len(request_calls) == 1:
                raise momonga.MomongaNeedToReopen('retry me')
            return ['ok']

        with patch.object(self.instance, '_Momonga__request', side_effect=fake_request), \
                patch.object(self.instance, 'reopen') as reopen_mock, \
                patch('momonga.momonga.time.sleep') as sleep_mock:
            result = self.instance._Momonga__request_with_recovery(
                momonga.momonga.EchonetServiceCode.get,
                [],
            )

        self.assertEqual(result, ['ok'])
        self.assertEqual(len(request_calls), 2)
        reopen_mock.assert_called_once_with()
        sleep_mock.assert_called_once_with(256.0)

    def test_request_raises_after_delays_exhausted(self) -> None:
        with patch.object(self.instance, '_Momonga__request', side_effect=momonga.MomongaNeedToReopen('still failing')), \
                patch.object(self.instance, 'reopen') as reopen_mock, \
                patch('momonga.momonga.time.sleep') as sleep_mock:
            with self.assertRaises(momonga.MomongaNeedToReopen):
                self.instance._Momonga__request_with_recovery(
                    momonga.momonga.EchonetServiceCode.get,
                    [],
                )

        self.assertEqual(reopen_mock.call_count, 2)
        self.assertEqual(sleep_mock.call_args_list[0].args, (256.0,))
        self.assertEqual(sleep_mock.call_args_list[1].args, (1024.0,))

    def test_negative_delay_is_rejected(self) -> None:
        instance = momonga.Momonga('rbid', 'pwd', '/dev/null', reopen_delays=[-1.0])

        with patch.object(instance, '_Momonga__request', side_effect=momonga.MomongaNeedToReopen('retry me')):
            with self.assertRaises(momonga.MomongaValueError):
                instance._Momonga__request_with_recovery(
                    momonga.momonga.EchonetServiceCode.get,
                    [],
                )


if __name__ == '__main__':
    unittest.main()
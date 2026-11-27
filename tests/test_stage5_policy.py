import unittest

from core.connectivity import internet_reachable
from core.policy import can_start, must_auto_stop


class PolicyTests(unittest.TestCase):
    def base(self, **over):
        args = dict(is_admin=True, real_internet=False, test_mode=False, has_enabled_domains=True)
        args.update(over)
        return can_start(**args)

    def test_allowed_in_normal_conditions(self):
        self.assertTrue(self.base().allowed)

    def test_refused_without_enabled_domains(self):
        d = self.base(has_enabled_domains=False)
        self.assertFalse(d.allowed)
        self.assertTrue(d.reason)

    def test_refused_without_admin(self):
        self.assertFalse(self.base(is_admin=False).allowed)

    def test_refused_when_real_internet_and_no_test_mode(self):
        self.assertFalse(self.base(real_internet=True).allowed)

    def test_allowed_with_real_internet_only_in_test_mode(self):
        self.assertTrue(self.base(real_internet=True, test_mode=True).allowed)

    def test_auto_stop_rules(self):
        self.assertTrue(must_auto_stop(running=True, real_internet=True, test_mode=False))
        self.assertFalse(must_auto_stop(running=True, real_internet=True, test_mode=True))
        self.assertFalse(must_auto_stop(running=False, real_internet=True, test_mode=False))
        self.assertFalse(must_auto_stop(running=True, real_internet=False, test_mode=False))


class ConnectivityTests(unittest.TestCase):
    def test_offline_when_every_probe_fails(self):
        self.assertFalse(internet_reachable(probe=lambda h, p: False))

    def test_online_when_any_probe_succeeds(self):
        calls = []
        def probe(h, p):
            calls.append(h)
            return h == "8.8.8.8"
        self.assertTrue(internet_reachable(probe=probe))
        self.assertEqual(calls, ["1.1.1.1", "8.8.8.8"])


if __name__ == "__main__":
    unittest.main()

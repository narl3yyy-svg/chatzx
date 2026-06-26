from chatxz.core.contacts import should_update_contact_ip


def test_prefers_local_subnet_ip():
    scope = "172.17.121.37"
    assert should_update_contact_ip("10.0.5.29", "172.17.13.110", scope) is True
    assert should_update_contact_ip("172.17.13.110", "10.0.5.29", scope) is False
    assert should_update_contact_ip("", "172.17.13.110", scope) is True


def test_updates_when_no_scope():
    assert should_update_contact_ip("10.0.5.29", "172.17.13.110", None) is True


def test_ignores_cross_10_subnet_beacon():
    scope = "10.10.100.4"
    assert should_update_contact_ip("10.10.100.12", "10.0.30.112", scope) is False
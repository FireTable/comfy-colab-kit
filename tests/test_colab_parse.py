from gate.colab import parse_ccu_balance, parse_sessions


def test_parse_sessions_cli_075():
    out = ("[comfy] gpu-l4-s-abc | Hardware: NVIDIA L4 | Shape: Standard | Variant: GPU\n"
           "[?] gpu-t4-s-xyz | Hardware: T4 | Shape: Standard | Variant: GPU\n")
    assert parse_sessions(out) == ["comfy", "?"]


def test_parse_sessions_empty():
    assert [] == parse_sessions("[colab] No active sessions found on server.\n")


def test_parse_usage():
    assert parse_ccu_balance("Current balance: 199.83 compute units\nUsage rate: 0.00/hr\n") == 199.83

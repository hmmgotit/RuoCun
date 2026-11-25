"""Frozen sandbox.test.ts:27–49 network policy on the existing file sandbox."""


def test_source_network_default_and_enabled_flags_on_both_platforms():
    from ruocun.platform.sandbox import sandboxSpawn

    command, cwd = "ls", "/tmp/work"
    for platform, switch in (("linux", "--unshare-net"), ("darwin", "(deny network*)")):
        ordinary = sandboxSpawn(command, cwd, environ={}, platform=platform, exists=lambda _: True)
        blocked = sandboxSpawn(command, cwd, environ={"RUOCUN_SANDBOX_NO_NET": "1"},
                               platform=platform, exists=lambda _: True)
        assert switch not in " ".join(ordinary["args"])
        assert switch in " ".join(blocked["args"])
        assert blocked["args"][-3:] == ["/bin/sh", "-c", command]
        if platform == "linux":
            assert blocked["args"] == [*ordinary["args"][:-3], switch, *ordinary["args"][-3:]]
        else:
            assert blocked["args"][1] == ordinary["args"][1] + " " + switch


def test_native_network_policy_cannot_replace_the_required_file_sandbox():
    from ruocun.platform.sandbox import sandboxNoNet, sandboxSpawn

    assert not sandboxNoNet({})
    assert not sandboxNoNet({"RUOCUN_SANDBOX_NO_NET": "", "DAO_SANDBOX_NO_NET": "1"})
    assert not sandboxNoNet({"RUOCUN_SANDBOX_NO_NET": "true"})
    no_net = {"RUOCUN_SANDBOX_NO_NET": "1"}
    assert sandboxNoNet(no_net)
    assert "--unshare-net" in sandboxSpawn("ls", "/tmp/work", environ=no_net, platform="linux",
                                         exists=lambda _: True)["args"]
    assert "不能整次绕过" in sandboxSpawn("ls", "/tmp/work", True, environ=no_net, platform="linux")["error"]
    assert "找不到 bwrap" in sandboxSpawn("ls", "/tmp/work", environ=no_net, platform="linux",
                                          exists=lambda _: False)["error"]

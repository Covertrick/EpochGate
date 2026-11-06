from epochgate.eg2_boundary import SECRET_NAMES, agent_process_env, command_refused, container_exec_args


def test_agent_env_and_exec_do_not_carry_secret_names():
    parent = {name: "hidden-value" for name in SECRET_NAMES}
    parent["PATH"] = "kept"
    child = agent_process_env(parent)
    assert "PATH" in child
    for name in SECRET_NAMES:
        assert name not in child
    args = container_exec_args("eg-agent", ["python", "-c", "print(1)"])
    rendered = " ".join(args)
    assert "-e" not in args
    for name in SECRET_NAMES:
        assert name not in rendered
    assert "hidden-value" not in rendered


def test_reading_the_key_or_pushing_is_refused():
    assert command_refused("printenv DEEPSEEK_API_KEY")
    assert command_refused("git push")
    assert command_refused("python -m pytest astropy/modeling/tests/test_separable.py -q") is False
